"""
Knowledge graph builder over kg_nodes / kg_edges (schema v5).

Node types:
  paper_local       imported paper (has paper_id)
  paper_external    referenced paper that hasn't been imported yet
  keyword           normalized keyword string
  research_field    llm-extracted research field
  author            (reserved for future use)

Stored edge types (paper-owned, idempotently rebuilt per add_paper):
  has_keyword       paper_local -> keyword
  in_field          paper_local -> research_field
  cites             paper_local -> paper_external
  cross_cites       paper_local -> paper_local

Derived edges (computed on demand in GraphRetriever, not materialized):
  co_occurs   keyword <-> keyword       (papers having both)
  covers      research_field -> keyword (papers in field with keyword)

Promotion: when ingesting a new paper, we first look for a paper_external
node matching its DOI or normalized title. If found, we upgrade that node
to paper_local in place so existing `cites` edges from other local papers
instantly point at the new local node — no edge rewrite needed.

`add_paper` is idempotent: paper-owned edges are deleted before re-adding,
and INSERT OR IGNORE on nodes is harmless on second call.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from db_connection import DatabaseConnection


# ── Normalizers ───────────────────────────────────────────────────


_TITLE_CLEAN = re.compile(r"[^a-z0-9一-鿿\s]+")
_WHITESPACE = re.compile(r"\s+")


def _normalize_keyword(s: str) -> str:
    """lowercase, collapse internal whitespace, strip. Empty if all-whitespace."""
    if not s:
        return ""
    return _WHITESPACE.sub(" ", s.lower().strip())


def _normalize_title(s: str) -> str:
    """For paper identity matching: lowercase, drop punctuation, collapse spaces.
    Keeps ASCII alphanumeric and CJK; strips everything else."""
    if not s:
        return ""
    return _WHITESPACE.sub(" ", _TITLE_CLEAN.sub(" ", s.lower())).strip()


def _ref_doi(identifiers_json: str) -> Optional[str]:
    """Pull DOI from a references_.identifiers JSON blob. Empty -> None."""
    if not identifiers_json:
        return None
    try:
        d = json.loads(identifiers_json)
    except (ValueError, TypeError):
        return None
    doi = (d.get("DOI") or d.get("doi") or "").strip()
    return doi.lower() if doi else None


# ── Builder ───────────────────────────────────────────────────────


class KGBuilder:
    """All methods leave the connection in a committed state on success."""

    PAPER_OWNED_EDGES = ("has_keyword", "in_field", "cites", "cross_cites")

    def __init__(self, db: DatabaseConnection):
        self._db = db

    # ── Node / edge primitives ───────────────────────────────────

    def _get_or_create_node(
        self,
        node_type: str,
        name: str,
        display_name: str,
        *,
        paper_id: Optional[int] = None,
        doi: Optional[str] = None,
    ) -> int:
        cur = self._db.conn.execute(
            "INSERT OR IGNORE INTO kg_nodes "
            "(node_type, name, display_name, paper_id, doi) VALUES (?, ?, ?, ?, ?)",
            (node_type, name, display_name, paper_id, doi),
        )
        if cur.lastrowid and cur.rowcount > 0:
            return cur.lastrowid
        row = self._db.conn.execute(
            "SELECT id FROM kg_nodes WHERE node_type=? AND name=?",
            (node_type, name),
        ).fetchone()
        return row["id"]

    def _add_edge(self, src_id: int, dst_id: int, edge_type: str, weight: float = 1.0) -> None:
        if src_id == dst_id:
            return  # never self-loop
        cur = self._db.conn.execute(
            "INSERT OR IGNORE INTO kg_edges (src_id, dst_id, edge_type, weight) "
            "VALUES (?, ?, ?, ?)",
            (src_id, dst_id, edge_type, weight),
        )
        # On duplicate: weight stays. Co_occurs / covers are derived at query
        # time (see module docstring) so we don't accumulate here.
        del cur

    # ── Lookups ──────────────────────────────────────────────────

    def _fetch_paper(self, paper_id: int) -> Optional[dict]:
        row = self._db.conn.execute(
            "SELECT id, title, doi, keywords, llm_research_field "
            "FROM papers WHERE id=?",
            (paper_id,),
        ).fetchone()
        return dict(row) if row else None

    def _fetch_references(self, paper_id: int) -> list[dict]:
        rows = self._db.conn.execute(
            "SELECT id, title, identifiers FROM references_ "
            "WHERE paper_id=? AND title IS NOT NULL AND title != ''",
            (paper_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _build_local_paper_index(self) -> tuple[dict[str, int], dict[str, int]]:
        """Returns (doi -> paper_id, normalized_title -> paper_id) for fast
        cross_cites lookup. Called once per add_paper / build_all."""
        by_doi: dict[str, int] = {}
        by_title: dict[str, int] = {}
        rows = self._db.conn.execute(
            "SELECT id, title, doi FROM papers"
        ).fetchall()
        for r in rows:
            if r["doi"]:
                by_doi[r["doi"].lower().strip()] = r["id"]
            if r["title"]:
                nt = _normalize_title(r["title"])
                if nt and len(nt) >= 8:
                    by_title[nt] = r["id"]
        return by_doi, by_title

    def _local_node_id(self, paper_id: int) -> Optional[int]:
        row = self._db.conn.execute(
            "SELECT id FROM kg_nodes WHERE node_type='paper_local' AND paper_id=?",
            (paper_id,),
        ).fetchone()
        return row["id"] if row else None

    # ── Promotion ────────────────────────────────────────────────

    def _try_promote_external(
        self, paper_id: int, title: str, doi: Optional[str]
    ) -> Optional[int]:
        """If a paper_external node matches (by DOI first, then normalized
        title), upgrade it to paper_local in place. Returns the upgraded node
        id, or None if no match was found.

        Edges already pointing to the external node (`cites` from other locals)
        are preserved automatically — only the node's identity changes.
        """
        candidate_id = None
        if doi:
            row = self._db.conn.execute(
                "SELECT id FROM kg_nodes WHERE node_type='paper_external' "
                "AND doi=? LIMIT 1",
                (doi.lower().strip(),),
            ).fetchone()
            if row:
                candidate_id = row["id"]
        if candidate_id is None:
            nt = _normalize_title(title)
            if nt and len(nt) >= 8:
                row = self._db.conn.execute(
                    "SELECT id FROM kg_nodes WHERE node_type='paper_external' "
                    "AND name=? LIMIT 1",
                    (nt,),
                ).fetchone()
                if row:
                    candidate_id = row["id"]
        if candidate_id is None:
            return None

        new_name = f"local-{paper_id}"
        # Safety: if a paper_local with this name already exists (re-ingestion
        # of same paper_id with a still-orphaned external from a different
        # title), prefer the existing local node and drop the duplicate.
        existing = self._db.conn.execute(
            "SELECT id FROM kg_nodes WHERE node_type='paper_local' AND name=? LIMIT 1",
            (new_name,),
        ).fetchone()
        if existing:
            self._db.conn.execute(
                "DELETE FROM kg_nodes WHERE id=?", (candidate_id,)
            )
            return existing["id"]

        self._db.conn.execute(
            "UPDATE kg_nodes "
            "SET node_type='paper_local', name=?, paper_id=?, "
            "    display_name=?, doi=COALESCE(doi, ?) "
            "WHERE id=?",
            (new_name, paper_id, title or new_name, doi, candidate_id),
        )
        # Retype incoming `cites` edges (from other locals) to `cross_cites`
        # since the destination is no longer external.
        self._db.conn.execute(
            "UPDATE OR IGNORE kg_edges SET edge_type='cross_cites' "
            "WHERE dst_id=? AND edge_type='cites'",
            (candidate_id,),
        )
        # UPDATE OR IGNORE drops conflicts with a pre-existing cross_cites
        # edge to the same src; clean those leftover stale rows.
        self._db.conn.execute(
            "DELETE FROM kg_edges WHERE dst_id=? AND edge_type='cites'",
            (candidate_id,),
        )
        return candidate_id

    # ── External-node helper ─────────────────────────────────────

    def _ensure_external_node(self, ref: dict) -> Optional[int]:
        """Get-or-create a paper_external node for a reference. Returns None
        if the ref has no usable title."""
        title = (ref.get("title") or "").strip()
        if not title:
            return None
        norm = _normalize_title(title)
        if not norm or len(norm) < 4:
            return None
        doi = _ref_doi(ref.get("identifiers") or "")
        # Prefer DOI-based identity when available (more stable than title)
        if doi:
            row = self._db.conn.execute(
                "SELECT id FROM kg_nodes WHERE node_type='paper_external' AND doi=?",
                (doi,),
            ).fetchone()
            if row:
                return row["id"]
        node_id = self._get_or_create_node(
            "paper_external", norm, title, doi=doi,
        )
        if doi:
            # Backfill DOI on a node that was first created by title alone
            self._db.conn.execute(
                "UPDATE kg_nodes SET doi=? WHERE id=? AND doi IS NULL",
                (doi, node_id),
            )
        return node_id

    # ── Public API ───────────────────────────────────────────────

    def add_paper(self, paper_id: int) -> dict:
        """Idempotent sync of paper's nodes & edges into the graph.

        Returns counts for telemetry / CLI display.
        """
        paper = self._fetch_paper(paper_id)
        if paper is None:
            raise ValueError(f"paper {paper_id} not found")

        title = (paper["title"] or "").strip()
        doi = (paper["doi"] or "").strip().lower() or None
        try:
            keywords = json.loads(paper["keywords"] or "[]")
            if not isinstance(keywords, list):
                keywords = []
        except (ValueError, TypeError):
            keywords = []
        research_field = (paper["llm_research_field"] or "").strip()

        with self._db.transaction() as cur:
            del cur  # we use db.conn directly; transaction is the boundary

            promoted_id = self._try_promote_external(paper_id, title, doi)
            if promoted_id is not None:
                local_id = promoted_id
            else:
                local_id = self._get_or_create_node(
                    "paper_local",
                    f"local-{paper_id}",
                    title or f"paper-{paper_id}",
                    paper_id=paper_id,
                    doi=doi,
                )

            # Idempotency: drop paper-owned outbound edges before re-adding.
            placeholders = ",".join(["?"] * len(self.PAPER_OWNED_EDGES))
            self._db.conn.execute(
                f"DELETE FROM kg_edges WHERE src_id=? AND edge_type IN ({placeholders})",
                (local_id, *self.PAPER_OWNED_EDGES),
            )

            # Keywords
            kw_count = 0
            for kw in keywords:
                if not isinstance(kw, str):
                    continue
                norm = _normalize_keyword(kw)
                if not norm:
                    continue
                kw_id = self._get_or_create_node("keyword", norm, kw.strip() or norm)
                self._add_edge(local_id, kw_id, "has_keyword")
                kw_count += 1

            # Research field
            if research_field:
                rf_norm = _normalize_keyword(research_field)
                if rf_norm:
                    rf_id = self._get_or_create_node(
                        "research_field", rf_norm, research_field
                    )
                    self._add_edge(local_id, rf_id, "in_field")

            # References → cites / cross_cites
            local_by_doi, local_by_title = self._build_local_paper_index()
            cites = 0
            cross_cites = 0
            for ref in self._fetch_references(paper_id):
                ref_doi = _ref_doi(ref.get("identifiers") or "")
                ref_title = (ref.get("title") or "").strip()
                matched_local_paper_id = None
                if ref_doi:
                    matched_local_paper_id = local_by_doi.get(ref_doi)
                if matched_local_paper_id is None:
                    nt = _normalize_title(ref_title)
                    if nt and len(nt) >= 8:
                        matched_local_paper_id = local_by_title.get(nt)
                if matched_local_paper_id and matched_local_paper_id != paper_id:
                    target_node = self._local_node_id(matched_local_paper_id)
                    if target_node:
                        self._add_edge(local_id, target_node, "cross_cites")
                        cross_cites += 1
                else:
                    ext_id = self._ensure_external_node(ref)
                    if ext_id is not None:
                        self._add_edge(local_id, ext_id, "cites")
                        cites += 1

        return {
            "paper_id": paper_id,
            "local_node_id": local_id,
            "promoted_external": promoted_id is not None,
            "keywords": kw_count,
            "research_field": bool(research_field),
            "cites": cites,
            "cross_cites": cross_cites,
        }

    def remove_paper(self, paper_id: int) -> dict:
        """Drop the paper_local node and cascade its edges, then sweep orphans.

        Safe to call before OR after the paper has been deleted from `papers`
        (since paper_local node has ON DELETE CASCADE on paper_id).
        """
        with self._db.transaction():
            self._db.conn.execute(
                "DELETE FROM kg_nodes WHERE node_type='paper_local' AND paper_id=?",
                (paper_id,),
            )
            orphans = self._sweep_orphans()
        return {"paper_id": paper_id, **orphans}

    def _sweep_orphans(self) -> dict:
        """Delete keyword / research_field / paper_external nodes that have no
        remaining edges. Returns counts."""
        deleted = {"keyword": 0, "research_field": 0, "paper_external": 0}
        for nt in deleted:
            # A node is orphan iff it has no incoming or outgoing edges.
            # paper_external: only ever appears as `dst_id` of `cites`.
            # keyword: only ever appears as `dst_id` of `has_keyword`.
            # research_field: only ever appears as `dst_id` of `in_field`.
            cur = self._db.conn.execute(
                "DELETE FROM kg_nodes WHERE node_type=? "
                "AND id NOT IN (SELECT DISTINCT dst_id FROM kg_edges) "
                "AND id NOT IN (SELECT DISTINCT src_id FROM kg_edges)",
                (nt,),
            )
            deleted[nt] = cur.rowcount or 0
        return {"orphans_removed": deleted}

    def build_all(self, rebuild: bool = False) -> dict:
        """Build the graph from scratch (or refresh incrementally).

        rebuild=True wipes kg_nodes/kg_edges first. Otherwise paper-owned edges
        of each paper are re-synced (idempotent), and orphans are swept once
        at the end.
        """
        if rebuild:
            with self._db.transaction():
                self._db.conn.execute("DELETE FROM kg_edges")
                self._db.conn.execute("DELETE FROM kg_nodes")

        ids = [r["id"] for r in self._db.conn.execute(
            "SELECT id FROM papers ORDER BY id"
        ).fetchall()]
        for pid in ids:
            self.add_paper(pid)
        with self._db.transaction():
            orphans = self._sweep_orphans()
        return {"papers": len(ids), **orphans}

    def promote_externals(self) -> dict:
        """Scan paper_external nodes and promote any whose DOI / normalized
        title matches an existing paper_local. Useful after bulk import where
        a paper was ingested AFTER another paper had already referenced it.

        Returns count of promotions.
        """
        # Build local lookup
        local_by_doi: dict[str, int] = {}
        local_by_title: dict[str, int] = {}
        for r in self._db.conn.execute(
            "SELECT n.id, n.paper_id, n.doi, p.title FROM kg_nodes n "
            "JOIN papers p ON p.id = n.paper_id "
            "WHERE n.node_type='paper_local'"
        ).fetchall():
            if r["doi"]:
                local_by_doi[r["doi"].lower().strip()] = (r["id"], r["paper_id"])
            nt = _normalize_title(r["title"] or "")
            if nt and len(nt) >= 8:
                local_by_title[nt] = (r["id"], r["paper_id"])

        externals = self._db.conn.execute(
            "SELECT id, name, doi FROM kg_nodes WHERE node_type='paper_external'"
        ).fetchall()

        promoted = 0
        with self._db.transaction():
            for e in externals:
                hit = None
                if e["doi"]:
                    hit = local_by_doi.get(e["doi"])
                if hit is None:
                    hit = local_by_title.get(e["name"])
                if hit is None:
                    continue
                local_node_id, _local_paper_id = hit
                # Merge: rewire edges that point at the external to point at
                # the local node instead. UPDATE OR IGNORE drops dups (which
                # are then orphaned in kg_edges and cleaned by the DELETE).
                # Retype `cites` → `cross_cites` since dst is now local
                self._db.conn.execute(
                    "UPDATE OR IGNORE kg_edges SET edge_type='cross_cites' "
                    "WHERE dst_id=? AND edge_type='cites'",
                    (e["id"],),
                )
                # Rewire remaining edges from external to local node
                self._db.conn.execute(
                    "UPDATE OR IGNORE kg_edges SET dst_id=? WHERE dst_id=?",
                    (local_node_id, e["id"]),
                )
                self._db.conn.execute(
                    "UPDATE OR IGNORE kg_edges SET src_id=? WHERE src_id=?",
                    (local_node_id, e["id"]),
                )
                # Drop orphaned edges (those that lost their UPDATE OR IGNORE
                # races against existing edges to the local node)
                self._db.conn.execute(
                    "DELETE FROM kg_edges WHERE src_id=? OR dst_id=?",
                    (e["id"], e["id"]),
                )
                self._db.conn.execute("DELETE FROM kg_nodes WHERE id=?", (e["id"],))
                promoted += 1
        return {"promoted": promoted}

    def stats(self) -> dict:
        """Per-type node + edge counts for `kg stats` CLI."""
        node_counts = {}
        for r in self._db.conn.execute(
            "SELECT node_type, COUNT(*) AS n FROM kg_nodes GROUP BY node_type"
        ).fetchall():
            node_counts[r["node_type"]] = r["n"]
        edge_counts = {}
        for r in self._db.conn.execute(
            "SELECT edge_type, COUNT(*) AS n FROM kg_edges GROUP BY edge_type"
        ).fetchall():
            edge_counts[r["edge_type"]] = r["n"]
        return {"nodes": node_counts, "edges": edge_counts}
