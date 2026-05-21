"""
Graph-based retrieval over kg_nodes / kg_edges (schema v5).

This is the third recall path alongside `vector_search` and `fts_search`
in retrieval.py. Output shape (`list[tuple[paper_id, score]]`) is identical
so it slots straight into `Retriever.rrf_fuse`.

Algorithm:
  1. Anchor identification — map the query to a set of `kg_nodes` with
     per-anchor relevance scores. Three layers (cheapest first):
       a. exact-phrase match against `keyword` / `research_field` names
       b. n-gram (3/2/1) match — partial phrase hits
       c. token substring LIKE fallback — catches "EV" → "EV (electric vehicle)"
  2. BFS scoring — for each anchor, accumulate score on connected
     `paper_local` nodes:
       depth 1  paper → has_keyword/in_field → anchor             (full weight)
       depth 2  paper → has_keyword → co-occurring-kw ← anchor    (decayed)
  3. Optional external recommendations — for "what should I add to my
     library?" use cases, surface `paper_external` nodes most-cited by
     local papers matching the anchors.

Co-occurrence edges are derived at query time (JOIN over `has_keyword`),
not materialized, so the graph stays cheap to maintain incrementally.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from db_connection import DatabaseConnection
from knowledge_graph import _normalize_keyword


# Tunables — kept module-level so callers / tests can tweak without subclassing
DEFAULT_DEPTH = 2
DEPTH1_WEIGHT = 1.0
DEPTH2_WEIGHT = 0.3
PHRASE_RELEVANCE = 1.0
NGRAM_RELEVANCE = 0.8
SUBSTRING_RELEVANCE = 0.5
MAX_NGRAMS = 40
TOP_COOCCUR = 10
MIN_TOKEN_LEN = 2


class GraphRetriever:

    def __init__(self, db: DatabaseConnection):
        self._db = db

    # ── Anchor identification ────────────────────────────────────

    def _find_anchors(self, query: str) -> dict[int, tuple[float, str]]:
        """Returns {node_id: (relevance, node_type)}. Only keyword and
        research_field nodes are anchor candidates (paper nodes don't have
        text useful for matching here)."""
        norm = _normalize_keyword(query)
        if not norm:
            return {}

        anchors: dict[int, tuple[float, str]] = {}

        # (a) full-phrase exact match
        for r in self._db.conn.execute(
            "SELECT id, node_type FROM kg_nodes "
            "WHERE node_type IN ('keyword', 'research_field') AND name = ?",
            (norm,),
        ).fetchall():
            anchors[r["id"]] = (PHRASE_RELEVANCE, r["node_type"])

        # (b) n-gram match: try (3,2,1)-grams from the normalized query
        tokens = [t for t in norm.split() if t]
        ngrams: list[str] = []
        seen_ng: set[str] = {norm}
        for n in (3, 2, 1):
            if n > len(tokens):
                continue
            for i in range(len(tokens) - n + 1):
                ng = " ".join(tokens[i:i + n])
                if ng and ng not in seen_ng and len(ng) >= MIN_TOKEN_LEN:
                    ngrams.append(ng)
                    seen_ng.add(ng)
                if len(ngrams) >= MAX_NGRAMS:
                    break
            if len(ngrams) >= MAX_NGRAMS:
                break
        if ngrams:
            placeholders = ",".join(["?"] * len(ngrams))
            for r in self._db.conn.execute(
                f"SELECT id, node_type FROM kg_nodes "
                f"WHERE node_type IN ('keyword', 'research_field') "
                f"AND name IN ({placeholders})",
                ngrams,
            ).fetchall():
                anchors.setdefault(r["id"], (NGRAM_RELEVANCE, r["node_type"]))

        # (c) substring fallback — catches "EV" → "EV (electric vehicle)".
        #     Skip tokens that already exact-matched (we already used them
        #     in n-gram), and very short tokens (would match too much).
        for tok in tokens:
            if len(tok) < 3:
                continue
            for r in self._db.conn.execute(
                "SELECT id, node_type FROM kg_nodes "
                "WHERE node_type IN ('keyword', 'research_field') "
                "AND name LIKE ? LIMIT 5",
                (f"%{tok}%",),
            ).fetchall():
                anchors.setdefault(r["id"], (SUBSTRING_RELEVANCE, r["node_type"]))

        return anchors

    # ── Edge helpers ─────────────────────────────────────────────

    def _papers_by_term(self, node_id: int, node_type: str) -> list[int]:
        edge_type = "has_keyword" if node_type == "keyword" else "in_field"
        rows = self._db.conn.execute(
            "SELECT n.paper_id FROM kg_edges e "
            "JOIN kg_nodes n ON n.id = e.src_id "
            "WHERE e.dst_id = ? AND e.edge_type = ? AND n.paper_id IS NOT NULL",
            (node_id, edge_type),
        ).fetchall()
        return [r["paper_id"] for r in rows]

    def _co_occurring_keywords(self, kw_node_id: int, top: int) -> list[tuple[int, int]]:
        """Derived co_occurs edges: keywords sharing ≥1 paper with `kw_node_id`."""
        rows = self._db.conn.execute(
            """
            SELECT e2.dst_id AS kw_id, COUNT(*) AS cnt
            FROM kg_edges e1
            JOIN kg_edges e2 ON e1.src_id = e2.src_id
            WHERE e1.dst_id = ? AND e1.edge_type = 'has_keyword'
              AND e2.edge_type = 'has_keyword' AND e2.dst_id != ?
            GROUP BY e2.dst_id
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (kw_node_id, kw_node_id, top),
        ).fetchall()
        return [(r["kw_id"], r["cnt"]) for r in rows]

    # ── Search ───────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 50,
        *,
        depth: int = DEFAULT_DEPTH,
        include_external: bool = False,
    ) -> list[tuple[int, float]]:
        """Graph recall path. Returns [(paper_id, score), ...] in descending
        order over `paper_local` nodes. Empty list when no anchors match.

        `include_external` is currently a no-op for this method; use
        `search_external_recommendations()` to surface uncited externals as
        a separate ranked list (different output shape).
        """
        anchors = self._find_anchors(query)
        if not anchors:
            return []

        scores: dict[int, float] = defaultdict(float)
        visited_kw: set[int] = set()

        # Depth 1
        for node_id, (relevance, node_type) in anchors.items():
            if node_type in ("keyword", "research_field"):
                for pid in self._papers_by_term(node_id, node_type):
                    scores[pid] += relevance * DEPTH1_WEIGHT
                if node_type == "keyword":
                    visited_kw.add(node_id)

        # Depth 2 — co-occurring keywords' papers
        if depth >= 2:
            for node_id, (relevance, node_type) in list(anchors.items()):
                if node_type != "keyword":
                    continue
                for co_id, cooc_cnt in self._co_occurring_keywords(node_id, TOP_COOCCUR):
                    if co_id in visited_kw:
                        continue
                    # Co-occurrence strength shrinks decay; saturating curve
                    boost = relevance * DEPTH2_WEIGHT * (cooc_cnt / (cooc_cnt + 1))
                    for pid in self._papers_by_term(co_id, "keyword"):
                        scores[pid] += boost
                    visited_kw.add(co_id)

        return sorted(scores.items(), key=lambda x: -x[1])[:k]

    def search_external_recommendations(
        self,
        query: str,
        k: int = 20,
    ) -> list[dict]:
        """Identify `paper_external` nodes most-cited by local papers that
        match the query's anchors. Useful as a "missing-from-library" signal
        for gap analysis or reading-list suggestions.

        Returns a list of {title, doi, cite_count, citing_paper_ids[]} dicts
        in descending citation count.
        """
        anchors = self._find_anchors(query)
        if not anchors:
            return []
        local_papers: set[int] = set()
        for node_id, (_, node_type) in anchors.items():
            if node_type in ("keyword", "research_field"):
                local_papers.update(self._papers_by_term(node_id, node_type))
        if not local_papers:
            return []

        placeholders = ",".join(["?"] * len(local_papers))
        rows = self._db.conn.execute(
            f"""
            SELECT n.id AS ext_id, n.display_name AS title, n.doi,
                   COUNT(*) AS cite_count,
                   GROUP_CONCAT(src.paper_id) AS citing_ids
            FROM kg_edges e
            JOIN kg_nodes src ON src.id = e.src_id
            JOIN kg_nodes n ON n.id = e.dst_id
            WHERE e.edge_type = 'cites'
              AND src.paper_id IN ({placeholders})
              AND n.node_type = 'paper_external'
            GROUP BY n.id
            ORDER BY cite_count DESC, n.display_name
            LIMIT ?
            """,
            (*local_papers, k),
        ).fetchall()
        return [
            {
                "title": r["title"],
                "doi": r["doi"],
                "cite_count": r["cite_count"],
                "citing_paper_ids": [
                    int(x) for x in (r["citing_ids"] or "").split(",") if x
                ],
            }
            for r in rows
        ]

    # ── Explainability ───────────────────────────────────────────

    def explain(self, query: str) -> dict:
        """Return a debug-friendly breakdown of anchors and their per-anchor
        paper contributions. Useful for CLI / routing decisions."""
        anchors = self._find_anchors(query)
        out = {"query_normalized": _normalize_keyword(query),
               "anchors": [], "score": []}
        if not anchors:
            return out
        for node_id, (relevance, node_type) in anchors.items():
            row = self._db.conn.execute(
                "SELECT display_name FROM kg_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            paper_ids = self._papers_by_term(node_id, node_type) \
                if node_type in ("keyword", "research_field") else []
            out["anchors"].append({
                "node_id": node_id,
                "node_type": node_type,
                "display_name": row["display_name"] if row else "",
                "relevance": relevance,
                "paper_ids": paper_ids,
            })
        out["score"] = self.search(query, k=50)
        return out
