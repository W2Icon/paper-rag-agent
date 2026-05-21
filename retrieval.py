"""
Multi-path recall + Reciprocal Rank Fusion (RRF).

Two recall paths (Phase 3 default):
  1. Vector path  — cosine similarity over abstract_embedding (Qwen v4 1024-d).
  2. Lexical path — SQLite FTS5 over (title, abstract, keywords), BM25 ranked.

Fusion: standard RRF with k=60 (Cormack et al. 2009). Robust to score-scale
differences and doesn't need per-path tuning.

Metadata filters are applied as a post-filter on the fused set to avoid
biasing recall. Supported filters: year range, tag, doi presence.

Embeddings are loaded **once per Retriever instance** into a single numpy
matrix; subsequent queries reuse the matrix. Re-instantiate the Retriever
after writing new embeddings.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Optional

from db_connection import DatabaseConnection
from db.fts_index import ensure_fts_index


VECTOR_DIM_DEFAULT = 1024
RRF_K = 60


# ── Result types ──────────────────────────────────────────────────


@dataclass
class SearchHit:
    paper_id: int
    score: float                                      # fused / rerank score
    source_scores: dict[str, float] = field(default_factory=dict)  # {"vector": .., "fts": ..}
    rank_in_source: dict[str, int] = field(default_factory=dict)   # {"vector": 3, "fts": 7}
    title: str = ""
    abstract: str = ""
    year: Optional[str] = None
    doi: Optional[str] = None
    rerank_reason: str = ""

    def explain(self) -> str:
        bits = []
        for src in ("vector", "fts", "graph", "section", "viewpoint"):
            if src in self.rank_in_source:
                bits.append(f"{src}#{self.rank_in_source[src]}({self.source_scores.get(src,0):.3f})")
        return " ".join(bits) or "—"


# ── Vector path ───────────────────────────────────────────────────


def _bytes_to_float32_list(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _is_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _cosine_matrix(query: list[float], matrix: list[list[float]]) -> list[float]:
    """Compute cosine similarity of one query vector against many docs.
    Pure-Python implementation to avoid forcing numpy as a dependency."""
    q_norm = math.sqrt(sum(x * x for x in query)) + 1e-12
    out = []
    for row in matrix:
        if not row:
            out.append(-1.0)
            continue
        dot = 0.0
        sq = 0.0
        for q, r in zip(query, row):
            dot += q * r
            sq += r * r
        out.append(dot / (q_norm * (math.sqrt(sq) + 1e-12)))
    return out


# ── Retriever ─────────────────────────────────────────────────────


class Retriever:

    def __init__(self, db: DatabaseConnection, embed_field: str = "abstract_embedding"):
        """`embed_field` selects which paper-level embedding to compare against
        (title_embedding / abstract_embedding / fulltext_embedding)."""
        if embed_field not in ("title_embedding", "abstract_embedding", "fulltext_embedding"):
            raise ValueError(f"unsupported embed_field: {embed_field}")
        self._db = db
        self._embed_field = embed_field
        self._paper_ids: list[int] = []
        self._paper_vecs: list[list[float]] = []
        self._loaded = False
        # Section-level state (lazy-loaded on first section_search call)
        self._section_paper_ids: list[int] = []     # paper_id per section
        self._section_types: list[str] = []          # section_type per section
        self._section_vecs: list[list[float]] = []   # section_embedding per section
        self._sections_loaded = False
        # Viewpoint-level state (lazy-loaded on first viewpoint_search call)
        self._viewpoint_paper_ids: list[int] = []
        self._viewpoint_vecs: list[list[float]] = []
        self._viewpoints_loaded = False

    # ── Lazy load embeddings ─────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        rows = self._db.conn.execute(
            f"SELECT id, {self._embed_field} AS emb FROM papers "
            f"WHERE {self._embed_field} IS NOT NULL"
        ).fetchall()
        self._paper_ids = [r["id"] for r in rows]
        self._paper_vecs = [_bytes_to_float32_list(r["emb"]) for r in rows]
        self._loaded = True

    def _load_sections(self) -> None:
        if self._sections_loaded:
            return
        rows = self._db.conn.execute(
            "SELECT paper_id, section_type, section_embedding AS emb "
            "FROM sections WHERE section_embedding IS NOT NULL "
            "AND section_type IS NOT NULL"
        ).fetchall()
        self._section_paper_ids = [r["paper_id"] for r in rows]
        self._section_types = [r["section_type"] for r in rows]
        self._section_vecs = [_bytes_to_float32_list(r["emb"]) for r in rows]
        self._sections_loaded = True

    def _load_viewpoints(self) -> None:
        if self._viewpoints_loaded:
            return
        rows = self._db.conn.execute(
            "SELECT paper_id, viewpoint_embedding AS emb "
            "FROM lit_review_entries WHERE viewpoint_embedding IS NOT NULL"
        ).fetchall()
        self._viewpoint_paper_ids = [r["paper_id"] for r in rows]
        self._viewpoint_vecs = [_bytes_to_float32_list(r["emb"]) for r in rows]
        self._viewpoints_loaded = True

    @property
    def embedded_paper_count(self) -> int:
        self._load()
        return len(self._paper_ids)

    @property
    def embedded_section_count(self) -> int:
        self._load_sections()
        return len(self._section_vecs)

    @property
    def embedded_viewpoint_count(self) -> int:
        self._load_viewpoints()
        return len(self._viewpoint_vecs)

    # ── Single-path searches ─────────────────────────────────────

    def vector_search(self, query_vec: list[float], k: int = 50) -> list[tuple[int, float]]:
        """Returns [(paper_id, cosine_score), ...] in descending score order."""
        self._load()
        if not self._paper_vecs:
            return []
        sims = _cosine_matrix(query_vec, self._paper_vecs)
        ranked = sorted(zip(self._paper_ids, sims), key=lambda x: -x[1])
        return ranked[:k]

    def section_search(
        self,
        query_vec: list[float],
        section_weights: dict[str, float],
        k: int = 50,
    ) -> list[tuple[int, float]]:
        """Intent-aware recall path.

        Per paper, score = max over its sections of `weight[section_type] *
        cosine(query, section_embedding)`. Sections whose type isn't in
        `section_weights` (or has weight 0) are ignored.

        weighted-max is used (not weighted-sum) so a single highly-relevant
        section wins over a paper that has many lukewarm sections. This
        matches the semantics of "find papers whose <intent> section is
        on-topic for the query".

        Returns [(paper_id, score), ...] descending. Empty list when no
        sections have embeddings or the weight dict is empty/all-zero.
        """
        if not section_weights or not any(w > 0 for w in section_weights.values()):
            return []
        self._load_sections()
        if not self._section_vecs:
            return []

        q_norm = math.sqrt(sum(x * x for x in query_vec)) + 1e-12
        best_per_paper: dict[int, float] = {}
        for pid, stype, vec in zip(self._section_paper_ids,
                                    self._section_types,
                                    self._section_vecs):
            w = section_weights.get(stype, 0.0)
            if w <= 0:
                continue
            # inlined cosine for speed
            dot = 0.0
            sq = 0.0
            for a, b in zip(query_vec, vec):
                dot += a * b
                sq += b * b
            sim = dot / (q_norm * (math.sqrt(sq) + 1e-12))
            score = w * sim
            prev = best_per_paper.get(pid)
            if prev is None or score > prev:
                best_per_paper[pid] = score

        ranked = sorted(best_per_paper.items(), key=lambda x: -x[1])
        return ranked[:k]

    def viewpoint_search(
        self,
        query_vec: list[float],
        k: int = 50,
    ) -> list[tuple[int, float]]:
        """Retrieve papers by LLM-extracted viewpoints.

        Each `lit_review_entries` row is a short, self-contained sentence about
        how a paper relates to one of its references (e.g. "uses Smith 2020 as
        a baseline but claims 12% improvement on MAPE"). Embedding these gives
        a high-signal retrieval surface for queries that ask about arguments /
        positioning / cited work, which the raw abstract often won't cover.

        weighted-max aggregation per paper — same semantics as section_search:
        a paper wins if its STRONGEST viewpoint is on-topic, not if it has many
        lukewarm ones.
        """
        self._load_viewpoints()
        if not self._viewpoint_vecs:
            return []

        q_norm = math.sqrt(sum(x * x for x in query_vec)) + 1e-12
        best_per_paper: dict[int, float] = {}
        for pid, vec in zip(self._viewpoint_paper_ids, self._viewpoint_vecs):
            dot = 0.0
            sq = 0.0
            for a, b in zip(query_vec, vec):
                dot += a * b
                sq += b * b
            sim = dot / (q_norm * (math.sqrt(sq) + 1e-12))
            prev = best_per_paper.get(pid)
            if prev is None or sim > prev:
                best_per_paper[pid] = sim

        ranked = sorted(best_per_paper.items(), key=lambda x: -x[1])
        return ranked[:k]

    def fts_search(self, query: str, k: int = 50) -> list[tuple[int, float]]:
        """BM25-ranked lexical search via SQLite FTS5. FTS5's bm25() returns
        *lower-is-better*, so we negate. Query strategy: tokenise by word-chars
        and CJK, quote each token, AND them. Fall back to OR if AND empty."""
        import re

        ensure_fts_index(self._db)
        tokens = re.findall(r"[\w一-鿿]+", query, flags=re.UNICODE)
        # Filter very short tokens (FTS5 unicode61 won't index them anyway)
        tokens = [t for t in tokens if len(t) > 1 or _is_cjk(t)]
        if not tokens:
            return []

        def run(match_expr: str) -> list[tuple[int, float]]:
            rows = self._db.conn.execute(
                """
                SELECT rowid AS paper_id, bm25(papers_fts) AS bm25_score
                FROM papers_fts
                WHERE papers_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (match_expr, k),
            ).fetchall()
            return [(r["paper_id"], -r["bm25_score"]) for r in rows]

        # AND first (precise), fall back to OR (recall)
        and_expr = " ".join(f'"{t}"' for t in tokens)
        try:
            hits = run(and_expr)
        except Exception:
            hits = []
        if hits:
            return hits
        or_expr = " OR ".join(f'"{t}"' for t in tokens)
        try:
            return run(or_expr)
        except Exception:
            return []

    # ── Fusion ───────────────────────────────────────────────────

    @staticmethod
    def rrf_fuse(
        hit_lists: dict[str, list[tuple[int, float]]],
        k_rrf: int = RRF_K,
        top_k: int = 50,
    ) -> list[SearchHit]:
        """Reciprocal rank fusion: score(d) = Σ_path 1 / (k + rank_path(d))."""
        acc: dict[int, SearchHit] = {}
        for source, hits in hit_lists.items():
            for rank, (pid, score) in enumerate(hits, start=1):
                h = acc.setdefault(pid, SearchHit(paper_id=pid, score=0.0))
                h.score += 1.0 / (k_rrf + rank)
                h.source_scores[source] = score
                h.rank_in_source[source] = rank
        ranked = sorted(acc.values(), key=lambda h: -h.score)
        return ranked[:top_k]

    # ── Hybrid search (main entry point) ─────────────────────────

    def hybrid_search(
        self,
        query_text: str,
        query_vec: Optional[list[float]],
        *,
        per_path_k: int = 50,
        top_k: int = 20,
        use_vector: bool = True,
        use_fts: bool = True,
        use_graph: bool = False,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        tag: Optional[str] = None,
        section_weights: Optional[dict[str, float]] = None,
        use_viewpoints: bool = False,
    ) -> list[SearchHit]:
        """Multi-path retrieval. Paths are RRF-fused.

        Paths:
          - "vector"    : cosine on `embed_field` (default: abstract_embedding)
          - "fts"       : BM25 lexical
          - "graph"     : knowledge-graph traversal (keyword / research_field
                          / paper anchors). Enabled iff `use_graph=True`.
          - "section"   : intent-weighted max over section_embeddings.
                          Enabled iff `section_weights` is a non-empty dict and
                          a `query_vec` is available.
          - "viewpoint" : max over LLM-extracted viewpoint embeddings.
                          Enabled iff `use_viewpoints=True` and `query_vec` is
                          available. Useful for "what does X paper argue about
                          Y?" style queries.
        """
        hit_lists: dict[str, list[tuple[int, float]]] = {}
        if use_vector and query_vec is not None:
            hit_lists["vector"] = self.vector_search(query_vec, k=per_path_k)
        if use_fts:
            hit_lists["fts"] = self.fts_search(query_text, k=per_path_k)
        if use_graph:
            from graph_retrieval import GraphRetriever
            gr_hits = GraphRetriever(self._db).search(query_text, k=per_path_k)
            if gr_hits:
                hit_lists["graph"] = gr_hits
        if section_weights and query_vec is not None:
            sec_hits = self.section_search(query_vec, section_weights, k=per_path_k)
            if sec_hits:
                hit_lists["section"] = sec_hits
        if use_viewpoints and query_vec is not None:
            vp_hits = self.viewpoint_search(query_vec, k=per_path_k)
            if vp_hits:
                hit_lists["viewpoint"] = vp_hits

        # Allow single-path (no fusion needed but RRF still works on one list)
        if not hit_lists:
            return []

        fused = self.rrf_fuse(hit_lists, top_k=max(top_k * 3, 50))

        # Enrich + filter
        fused = self._enrich_with_metadata(fused)
        fused = _apply_filters(fused, year_from=year_from, year_to=year_to, tag=tag,
                               db=self._db)
        return fused[:top_k]

    # ── Enrichment ───────────────────────────────────────────────

    def _enrich_with_metadata(self, hits: list[SearchHit]) -> list[SearchHit]:
        if not hits:
            return []
        ids = [h.paper_id for h in hits]
        placeholders = ",".join(["?"] * len(ids))
        rows = self._db.conn.execute(
            f"SELECT id, title, abstract, doi FROM papers WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        meta = {r["id"]: r for r in rows}
        for h in hits:
            row = meta.get(h.paper_id)
            if row is None:
                continue
            h.title = row["title"] or ""
            h.abstract = row["abstract"] or ""
            h.doi = row["doi"]
        return hits


# ── Filters ───────────────────────────────────────────────────────


def _apply_filters(
    hits: list[SearchHit],
    *,
    year_from: Optional[int],
    year_to: Optional[int],
    tag: Optional[str],
    db: DatabaseConnection,
) -> list[SearchHit]:
    if not hits or (year_from is None and year_to is None and not tag):
        return hits

    # Build allowed-paper-id set based on filters
    ids = [h.paper_id for h in hits]
    placeholders = ",".join(["?"] * len(ids))
    params: list = list(ids)
    where = [f"p.id IN ({placeholders})"]

    if tag:
        where.append(
            "p.id IN (SELECT pt.paper_id FROM paper_tags pt "
            "JOIN tags t ON t.id = pt.tag_id WHERE t.name = ?)"
        )
        params.append(tag)

    # Year filter: papers.year doesn't exist (only references have year).
    # Approximation: look in references for self-cite? No — better fallback is
    # to use the most-common year of cited refs, or just ignore. For now we
    # filter by ingested_at year as a fallback — many users mean "papers I
    # added in 2024". Document this caveat in the CLI help.
    if year_from is not None:
        where.append("CAST(SUBSTR(p.ingested_at, 1, 4) AS INTEGER) >= ?")
        params.append(year_from)
    if year_to is not None:
        where.append("CAST(SUBSTR(p.ingested_at, 1, 4) AS INTEGER) <= ?")
        params.append(year_to)

    sql = f"SELECT id FROM papers p WHERE {' AND '.join(where)}"
    allowed = {r["id"] for r in db.conn.execute(sql, params).fetchall()}
    return [h for h in hits if h.paper_id in allowed]
