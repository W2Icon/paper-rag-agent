"""
Citation cross-analysis — detect shared references between paper pairs and
characterize the paper-to-paper relationship via LLM.

Three detection paths (ranked by precision, run in order):
  1. DOI exact match              — highest precision, lowest recall
  2. Normalized title match       — lower-case, strip punctuation/whitespace
  3. ref_embedding cosine > thr   — fuzzy, catches paraphrased titles
                                    (optional, off by default — O(N²) refs)

Output goes into the shared_citations table. The LLM relationship pass runs
*after* detection on pairs that share ≥ min_shared_refs references.
"""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

from db_connection import DatabaseConnection
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.shared_citations_repo import SharedCitationsRepo
from llm_interface import LLMProvider
from models import SharedCitationRecord


ALLOWED_PAIR_RELATIONSHIPS = {
    "builds_on_same_foundation",
    "competing_methods",
    "complementary",
    "methodological_overlap",
    "benchmarking_overlap",
    "weak_overlap",
    "other",
}


EMB_MATCH_DEFAULT_THRESHOLD = 0.92


# ── Title normalization ───────────────────────────────────────────


_PUNCT_RE = re.compile(r"[^\w\s一-鿿]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(text: str) -> str:
    """Aggressive normalization for fuzzy title matching: lowercase, strip
    punctuation, collapse whitespace. Empty input → empty string."""
    if not text:
        return ""
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


# ── Detection result ──────────────────────────────────────────────


@dataclass
class SharedRefMatch:
    paper_a_id: int
    paper_b_id: int
    title: str            # canonical title used in the row
    doi: Optional[str]
    match_type: str       # "doi" | "title" | "embedding"
    confidence: float     # 1.0 doi; 0.95 title; cosine for embedding


# ── Detector ──────────────────────────────────────────────────────


class CitationCrossAnalyzer:

    def __init__(self, db: DatabaseConnection):
        self._db = db
        self._paper_repo = PaperRepo(db)
        self._repo = SharedCitationsRepo(db)

    # ── Main entry point ─────────────────────────────────────────

    def detect_all_shared_citations(
        self,
        *,
        use_doi: bool = True,
        use_title: bool = True,
        use_embedding: bool = False,
        embedding_threshold: float = EMB_MATCH_DEFAULT_THRESHOLD,
        paper_id_filter: Optional[int] = None,
    ) -> int:
        """Scan the entire library, find shared references, write rows.

        If `paper_id_filter` is set, only pairs (X, paper_id_filter) are
        considered — cheap "what shares refs with this paper?" mode.

        Returns the count of newly inserted shared_citations rows.
        """
        matches: list[SharedRefMatch] = []

        if use_doi:
            matches.extend(self._detect_by_doi(paper_id_filter))
        if use_title:
            matches.extend(self._detect_by_title(paper_id_filter))
        if use_embedding:
            matches.extend(self._detect_by_embedding(
                paper_id_filter, embedding_threshold))

        # Deduplicate by (a,b,normalized title) — keep highest-confidence
        deduped: dict[tuple[int, int, str], SharedRefMatch] = {}
        for m in matches:
            a, b = sorted([m.paper_a_id, m.paper_b_id])
            if a == b:
                continue
            key = (a, b, normalize_title(m.title)[:200])
            existing = deduped.get(key)
            if existing is None or m.confidence > existing.confidence:
                m.paper_a_id, m.paper_b_id = a, b
                deduped[key] = m

        records = [
            SharedCitationRecord(
                paper_a_id=m.paper_a_id,
                paper_b_id=m.paper_b_id,
                shared_ref_title=m.title,
                shared_ref_doi=m.doi,
                confidence=m.confidence,
            )
            for m in deduped.values()
        ]

        # Attach pair-level similarity score (paper abstract cosine)
        sim_cache = self._compute_pair_similarity(records)
        for r in records:
            r.similarity_score = sim_cache.get((r.paper_a_id, r.paper_b_id))

        return self._repo.insert_many(records)

    # ── Detection: DOI ───────────────────────────────────────────

    def _detect_by_doi(self, paper_id_filter: Optional[int]) -> list[SharedRefMatch]:
        sql = """
            SELECT
                a.paper_id AS a_pid,
                b.paper_id AS b_pid,
                a.title    AS a_title,
                a.identifiers AS a_ids
            FROM references_ a
            JOIN references_ b ON
                a.paper_id < b.paper_id AND
                json_extract(a.identifiers, '$.DOI') IS NOT NULL AND
                json_extract(a.identifiers, '$.DOI') != '' AND
                json_extract(a.identifiers, '$.DOI') = json_extract(b.identifiers, '$.DOI')
        """
        params: list = []
        if paper_id_filter is not None:
            sql += " WHERE a.paper_id = ? OR b.paper_id = ?"
            params = [paper_id_filter, paper_id_filter]
        rows = self._db.conn.execute(sql, params).fetchall()

        import json as _json
        matches = []
        for r in rows:
            ids = _json.loads(r["a_ids"] or "{}")
            matches.append(SharedRefMatch(
                paper_a_id=r["a_pid"],
                paper_b_id=r["b_pid"],
                title=r["a_title"] or "(no title)",
                doi=ids.get("DOI"),
                match_type="doi",
                confidence=1.0,
            ))
        return matches

    # ── Detection: normalized title ──────────────────────────────

    def _detect_by_title(self, paper_id_filter: Optional[int]) -> list[SharedRefMatch]:
        sql = "SELECT id, paper_id, title FROM references_ WHERE title != ''"
        params: list = []
        if paper_id_filter is not None:
            # Pull refs from the target paper + every other paper
            sql = ("SELECT id, paper_id, title FROM references_ WHERE title != '' "
                   "AND (paper_id = ? OR ? IN (SELECT id FROM papers))")
            params = [paper_id_filter, paper_id_filter]
        rows = self._db.conn.execute(sql, params).fetchall()

        # Bucket refs by normalized title
        buckets: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for r in rows:
            norm = normalize_title(r["title"])
            if len(norm) < 8:  # too-short titles are noise
                continue
            buckets[norm].append((r["paper_id"], r["id"], r["title"]))

        matches = []
        for norm, items in buckets.items():
            if len(items) < 2:
                continue
            # Emit all distinct paper pairs sharing this title
            seen_pairs = set()
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a_pid, _, a_title = items[i]
                    b_pid, _, _ = items[j]
                    if a_pid == b_pid:
                        continue
                    if paper_id_filter is not None and paper_id_filter not in (a_pid, b_pid):
                        continue
                    pair = (min(a_pid, b_pid), max(a_pid, b_pid))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    matches.append(SharedRefMatch(
                        paper_a_id=a_pid,
                        paper_b_id=b_pid,
                        title=a_title,
                        doi=None,
                        match_type="title",
                        confidence=0.95,
                    ))
        return matches

    # ── Detection: ref_embedding cosine ──────────────────────────

    def _detect_by_embedding(
        self,
        paper_id_filter: Optional[int],
        threshold: float,
    ) -> list[SharedRefMatch]:
        # Load all (id, paper_id, title, ref_embedding) where embedding present
        sql = """
            SELECT id, paper_id, title, ref_embedding
            FROM references_
            WHERE ref_embedding IS NOT NULL
        """
        rows = self._db.conn.execute(sql).fetchall()
        if not rows:
            return []

        import math
        vectors = []
        for r in rows:
            blob = r["ref_embedding"]
            count = len(blob) // 4
            vectors.append(list(struct.unpack(f"<{count}f", blob)))

        # Normalize once
        norms = [math.sqrt(sum(x*x for x in v)) + 1e-12 for v in vectors]

        # Pre-compute set of refs of the filter paper for fast filter
        filter_paper_ids = None
        if paper_id_filter is not None:
            filter_paper_ids = {paper_id_filter}

        matches = []
        seen = set()
        for i in range(len(rows)):
            a_pid = rows[i]["paper_id"]
            for j in range(i + 1, len(rows)):
                b_pid = rows[j]["paper_id"]
                if a_pid == b_pid:
                    continue
                if filter_paper_ids and paper_id_filter not in (a_pid, b_pid):
                    continue
                # cosine
                dot = 0.0
                vi, vj = vectors[i], vectors[j]
                if len(vi) != len(vj):
                    continue
                for x, y in zip(vi, vj):
                    dot += x * y
                cos = dot / (norms[i] * norms[j])
                if cos < threshold:
                    continue
                pair = (min(a_pid, b_pid), max(a_pid, b_pid), rows[i]["id"], rows[j]["id"])
                if pair in seen:
                    continue
                seen.add(pair)
                matches.append(SharedRefMatch(
                    paper_a_id=a_pid,
                    paper_b_id=b_pid,
                    title=rows[i]["title"] or rows[j]["title"] or "(no title)",
                    doi=None,
                    match_type="embedding",
                    confidence=round(cos, 3),
                ))
        return matches

    # ── Pair-level abstract similarity ───────────────────────────

    # ── LLM pair relationship analysis ───────────────────────────

    def analyze_pair_relationship(
        self,
        provider: LLMProvider,
        paper_a_id: int,
        paper_b_id: int,
        *,
        tier: str = "simple",
    ) -> Optional[tuple[str, str]]:
        """Ask the LLM to characterize the paper-to-paper relationship based
        on the set of shared citations. Returns (relationship, explanation),
        or None if there's nothing to analyze."""
        a, b = sorted([paper_a_id, paper_b_id])
        paper_a = self._paper_repo.get_by_id(a)
        paper_b = self._paper_repo.get_by_id(b)
        if not paper_a or not paper_b:
            return None

        shared = self._repo.get_for_pair(a, b)
        if not shared:
            return None

        shared_lines = [
            f"- {s.shared_ref_title}"
            + (f"  doi:{s.shared_ref_doi}" if s.shared_ref_doi else "")
            + f"  [match:{s.confidence:.2f}]"
            for s in shared[:20]
        ]
        shared_blob = "\n".join(shared_lines)

        prompt = (
            "Two papers share references. Characterize how they relate to each "
            "other AS A PAIR (not how each treats individual refs).\n\n"
            f"PAPER A [{a}]\n"
            f"  Title:    {paper_a.title}\n"
            f"  Field:    {paper_a.llm_research_field or '(unknown)'}\n"
            f"  Abstract: {(paper_a.abstract or '')[:600]}\n\n"
            f"PAPER B [{b}]\n"
            f"  Title:    {paper_b.title}\n"
            f"  Field:    {paper_b.llm_research_field or '(unknown)'}\n"
            f"  Abstract: {(paper_b.abstract or '')[:600]}\n\n"
            f"SHARED REFERENCES ({len(shared)}):\n{shared_blob}\n\n"
            "Return JSON with EXACTLY these keys:\n"
            '  relationship  (one of: "builds_on_same_foundation", "competing_methods", '
            '"complementary", "methodological_overlap", "benchmarking_overlap", '
            '"weak_overlap", "other")\n'
            "  explanation   (1-3 sentences, concrete: name the shared works that "
            "drive your call)"
        )

        data = provider.complete_json(
            prompt,
            system="You are an experienced bibliographer. Be precise. Return only JSON.",
            tier=tier,
        )
        rel = str(data.get("relationship", "other")).strip().lower()
        if rel not in ALLOWED_PAIR_RELATIONSHIPS:
            rel = "other"
        expl = str(data.get("explanation", "")).strip()
        if not expl:
            return None
        return rel, expl

    def relate_all_pairs(
        self,
        provider: LLMProvider,
        *,
        min_shared: int = 2,
        limit: int = 50,
        tier: str = "simple",
        verbose: bool = False,
    ) -> tuple[int, int]:
        """For each pair with relationship still NULL and ≥ min_shared
        shared refs, run analyze_pair_relationship() and persist.
        Returns (success_count, failure_count)."""
        pairs = self._repo.list_pairs_missing_relationship(
            min_shared=min_shared, limit=limit
        )
        ok = 0
        fail = 0
        for a, b, shared_count in pairs:
            try:
                result = self.analyze_pair_relationship(provider, a, b, tier=tier)
                if result is None:
                    fail += 1
                    continue
                rel, expl = result
                self._repo.update_relationship_for_pair(
                    a, b, relationship=rel, explanation=expl,
                )
                ok += 1
                if verbose:
                    print(f"  pair ({a},{b}) [{shared_count} shared] → {rel}")
            except Exception as e:
                fail += 1
                if verbose:
                    print(f"  pair ({a},{b}) FAILED: {e}")
        return ok, fail

    def _compute_pair_similarity(
        self,
        records: Iterable[SharedCitationRecord],
    ) -> dict[tuple[int, int], float]:
        pairs = {(r.paper_a_id, r.paper_b_id) for r in records}
        if not pairs:
            return {}

        paper_ids = {pid for pair in pairs for pid in pair}
        placeholders = ",".join(["?"] * len(paper_ids))
        rows = self._db.conn.execute(
            f"SELECT id, abstract_embedding FROM papers WHERE id IN ({placeholders})",
            list(paper_ids),
        ).fetchall()
        vec_map: dict[int, list[float]] = {}
        for r in rows:
            if r["abstract_embedding"] is None:
                continue
            blob = r["abstract_embedding"]
            count = len(blob) // 4
            vec_map[r["id"]] = list(struct.unpack(f"<{count}f", blob))

        import math
        out = {}
        for a, b in pairs:
            va, vb = vec_map.get(a), vec_map.get(b)
            if not va or not vb or len(va) != len(vb):
                continue
            dot = sum(x*y for x, y in zip(va, vb))
            na = math.sqrt(sum(x*x for x in va))
            nb = math.sqrt(sum(x*x for x in vb))
            out[(a, b)] = dot / (na * nb + 1e-12)
        return out
