"""
PaperAnalyzer — Phase 2 LLM analysis orchestration.

Three independent stages (each callable on its own); each maps to a tier that
can be overridden via env vars so users tune cost/quality per agent.

  Stage              Default tier   Why
  -----------------  -------------  ----------------------------------
  analyze_paper      complex        Deep reasoning over multi-section input
  score_references   simple         Cheap batch classification
  extract_lit_review complex        Careful viewpoint extraction per cited work

Overrides:
    LLM_TIER_ANALYZE_PAPER=simple
    LLM_TIER_SCORE_REFS=complex
    LLM_TIER_LIT_REVIEW=simple
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from llm_interface import LLMProvider, LLMConfig
from models import (
    PaperRecord,
    SectionRecord,
    ReferenceRecord,
    CitationLocationRecord,
    LitReviewEntryRecord,
)


def _tier_env(name: str, default: str) -> str:
    val = (os.environ.get(name) or default).strip().lower()
    return val if val in ("complex", "simple") else default


# Hard caps to keep prompts bounded and costs predictable.
MAX_ABSTRACT_CHARS = 2500
MAX_SECTION_CHARS = 1500
MAX_SECTIONS_FOR_SUMMARY = 8
MAX_REFS_PER_BATCH = 25
MAX_CITED_REFS_FOR_LIT_REVIEW = 40


@dataclass
class PaperAnalysis:
    summary: str
    research_field: str
    methodology: str
    key_findings: list[str]


@dataclass
class RefScore:
    ref_id: int
    relevance_score: float  # 0.0 - 1.0
    relationship: str       # one of: foundational | comparison | methodology | dataset | background | extension | other


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def _section_snippet(sec: SectionRecord) -> str:
    body = " ".join(sec.paragraphs) if sec.paragraphs else sec.body_text
    return f"## {sec.heading}\n{_truncate(body, MAX_SECTION_CHARS)}"


def _pick_summary_sections(sections: list[SectionRecord]) -> list[SectionRecord]:
    """Prefer Intro / Methods / Results / Conclusion-style sections; cap count."""
    if not sections:
        return []
    priority_keywords = (
        "introduction", "background", "method", "approach", "model",
        "result", "experiment", "discussion", "conclusion", "summary",
        "引言", "方法", "实验", "结果", "讨论", "结论",
    )
    scored: list[tuple[int, SectionRecord]] = []
    for s in sections:
        heading = (s.heading or "").lower()
        score = 0
        for kw in priority_keywords:
            if kw in heading:
                score = 10
                break
        # Level-1 headings preferred when no keyword hit
        if score == 0 and s.level == 1:
            score = 3
        scored.append((score, s))

    scored.sort(key=lambda x: (-x[0], x[1].sort_order))
    picked = [s for _, s in scored[:MAX_SECTIONS_FOR_SUMMARY]]
    # Restore original order for readability
    picked.sort(key=lambda s: s.sort_order)
    return picked


class PaperAnalyzer:

    def __init__(self, llm: LLMProvider, config: Optional[LLMConfig] = None):
        self._llm = llm
        self._config = config or LLMConfig.from_env()

        # Per-stage tier mapping (env-overridable)
        self._tier_analyze = _tier_env("LLM_TIER_ANALYZE_PAPER", "complex")
        self._tier_score = _tier_env("LLM_TIER_SCORE_REFS", "simple")
        self._tier_lit_review = _tier_env("LLM_TIER_LIT_REVIEW", "complex")

    @property
    def model_label(self) -> str:
        # Label lit-review entries with the model that produced them
        tier = (self._config.complex_tier if self._tier_lit_review == "complex"
                else self._config.simple_tier)
        return tier.model

    def describe_configuration(self) -> str:
        return (
            f"analyze_paper      → {self._tier_analyze:<7} ({self._tier_obj(self._tier_analyze).describe()})\n"
            f"score_references   → {self._tier_score:<7} ({self._tier_obj(self._tier_score).describe()})\n"
            f"extract_lit_review → {self._tier_lit_review:<7} ({self._tier_obj(self._tier_lit_review).describe()})"
        )

    def _tier_obj(self, tier: str):
        return self._config.complex_tier if tier == "complex" else self._config.simple_tier

    # ── Stage 1: paper-level summary ──────────────────────────────

    def analyze_paper(
        self,
        paper: PaperRecord,
        sections: list[SectionRecord],
    ) -> PaperAnalysis:
        picked = _pick_summary_sections(sections)
        section_blob = "\n\n".join(_section_snippet(s) for s in picked) if picked else "(no sections extracted)"

        prompt = (
            "You are analysing an academic paper. Output a structured JSON object "
            "with the schema described below. Be specific and technical; avoid generic prose.\n\n"
            f"TITLE: {paper.title}\n"
            f"AUTHORS: {', '.join(paper.authors[:8])}\n"
            f"KEYWORDS: {', '.join(paper.keywords)}\n\n"
            f"ABSTRACT:\n{_truncate(paper.abstract, MAX_ABSTRACT_CHARS)}\n\n"
            f"SELECTED SECTIONS:\n{section_blob}\n\n"
            "Return JSON with EXACTLY these keys:\n"
            "  summary          (string, 3-5 sentences, what the paper does and why it matters)\n"
            "  research_field   (string, short label, e.g. 'EV energy consumption modeling')\n"
            "  methodology      (string, 2-4 sentences, concrete techniques/models/datasets used)\n"
            "  key_findings     (array of 3-6 short strings, each a concrete result or contribution)\n"
            "\nWrite in the same language as the paper title (English title → English output, "
            "Chinese title → Chinese output)."
        )

        data = self._llm.complete_json(
            prompt,
            system="You are a careful academic research assistant. Return only valid JSON.",
            tier=self._tier_analyze,
        )

        return PaperAnalysis(
            summary=str(data.get("summary", "")).strip(),
            research_field=str(data.get("research_field", "")).strip(),
            methodology=str(data.get("methodology", "")).strip(),
            key_findings=[str(x).strip() for x in (data.get("key_findings") or []) if str(x).strip()],
        )

    # ── Stage 2: per-reference relevance + relationship ──────────

    def score_references(
        self,
        paper: PaperRecord,
        references: list[ReferenceRecord],
    ) -> list[RefScore]:
        if not references:
            return []

        paper_context = (
            f"TITLE: {paper.title}\n"
            f"FIELD: {paper.llm_research_field or '(unknown)'}\n"
            f"ABSTRACT: {_truncate(paper.abstract, 1200)}"
        )

        results: list[RefScore] = []
        for i in range(0, len(references), MAX_REFS_PER_BATCH):
            batch = references[i:i + MAX_REFS_PER_BATCH]
            results.extend(self._score_batch(paper_context, batch))
        return results

    def _score_batch(
        self,
        paper_context: str,
        refs: list[ReferenceRecord],
    ) -> list[RefScore]:
        ref_lines = []
        for r in refs:
            ref_lines.append(
                f"- id={r.id} | {r.title or r.text[:120]} "
                f"({r.year or 'n.d.'}; {', '.join(r.authors[:2]) or 'unknown'})"
            )
        ref_blob = "\n".join(ref_lines)

        prompt = (
            "Given the paper context below, score each reference for relevance to the "
            "paper's core contribution, and label the relationship type.\n\n"
            f"PAPER CONTEXT:\n{paper_context}\n\n"
            f"REFERENCES:\n{ref_blob}\n\n"
            "For each reference output an object with:\n"
            "  id              (integer, the id shown above)\n"
            "  relevance_score (float 0.0-1.0; 1.0 = essential to the paper's argument)\n"
            "  relationship    (one of: foundational | comparison | methodology | dataset | "
            "background | extension | other)\n\n"
            'Return JSON with a single key "scores" whose value is the array of objects above. '
            "Include EVERY reference id exactly once."
        )

        data = self._llm.complete_json(
            prompt,
            system="You are a careful academic research assistant. Return only valid JSON.",
            tier=self._tier_score,
        )

        allowed_relationships = {
            "foundational", "comparison", "methodology",
            "dataset", "background", "extension", "other",
        }
        scored: dict[int, RefScore] = {}
        for item in (data.get("scores") or data.get("references") or []):
            try:
                rid = int(item.get("id"))
                score = float(item.get("relevance_score", 0.0))
                rel = str(item.get("relationship", "other")).strip().lower()
            except (TypeError, ValueError):
                continue
            if rel not in allowed_relationships:
                rel = "other"
            score = max(0.0, min(1.0, score))
            scored[rid] = RefScore(ref_id=rid, relevance_score=score, relationship=rel)

        # Fill defaults for any refs the LLM dropped
        output = []
        for r in refs:
            if r.id in scored:
                output.append(scored[r.id])
            else:
                output.append(RefScore(ref_id=r.id, relevance_score=0.0, relationship="other"))
        return output

    # ── Stage 3: literature review viewpoint extraction ──────────

    def extract_lit_review(
        self,
        paper_id: int,
        sections: list[SectionRecord],
        references: list[ReferenceRecord],
        citations: list[CitationLocationRecord],
    ) -> list[LitReviewEntryRecord]:
        """Extract one viewpoint per cited reference that appears in a
        related-work / introduction-style section."""

        if not references or not citations:
            return []

        # Map ref_number → reference and ref id → reference
        refs_by_id = {r.id: r for r in references}
        refs_by_number = {r.ref_number: r for r in references if r.ref_number is not None}

        # Build (section, citation, ref) triples — restrict to lit-review-ish sections
        target_keywords = (
            "introduction", "background", "related", "literature",
            "review", "prior work", "previous work",
            "引言", "相关工作", "文献综述",
        )
        target_sections = [
            s for s in sections
            if any(kw in (s.heading or "").lower() for kw in target_keywords)
        ]
        if not target_sections:
            return []  # No lit-review-style section found — skip

        # Build a page-range index for sections to attribute citations
        def find_section(page: int) -> Optional[SectionRecord]:
            for s in target_sections:
                if s.page_start <= page <= s.page_end:
                    return s
            return None

        # Build cited_ref → list of sentences within target sections
        ref_to_contexts: dict[int, list[tuple[str, Optional[int]]]] = {}
        for c in citations:
            sec = find_section(c.page)
            if sec is None:
                continue
            ref = refs_by_id.get(c.reference_id)
            if ref is None:
                # Try matching by parsed citation text like "[3]"
                num_str = c.text.strip("[]()［］ ")
                try:
                    ref = refs_by_number.get(int(num_str.split(",")[0].split("-")[0]))
                except ValueError:
                    ref = None
            if ref is None:
                continue
            ref_to_contexts.setdefault(ref.id, []).append((c.sentence, sec.id))

        if not ref_to_contexts:
            return []

        # Limit how many refs we send to the LLM (most-cited first)
        ordered = sorted(ref_to_contexts.items(), key=lambda kv: -len(kv[1]))[:MAX_CITED_REFS_FOR_LIT_REVIEW]

        entries: list[LitReviewEntryRecord] = []
        for ref_id, contexts in ordered:
            ref = refs_by_id[ref_id]
            entry = self._lit_review_for_ref(paper_id, ref, contexts)
            if entry is not None:
                entries.append(entry)
        return entries

    def _lit_review_for_ref(
        self,
        paper_id: int,
        ref: ReferenceRecord,
        contexts: list[tuple[str, Optional[int]]],
    ) -> Optional[LitReviewEntryRecord]:
        if not contexts:
            return None

        section_id = contexts[0][1]
        ctx_blob = "\n".join(f"- {s.strip()}" for s, _ in contexts[:4])

        prompt = (
            "Read how the current paper discusses the cited work below in its "
            "introduction / related-work section. Extract the AUTHORS' VIEWPOINT "
            "on this cited work — what they take from it, agree/disagree with, "
            "or extend.\n\n"
            f"CITED WORK: {ref.title or ref.text[:200]}\n"
            f"  authors: {', '.join(ref.authors[:3])}\n"
            f"  year:    {ref.year or 'n.d.'}\n\n"
            f"CITING SENTENCES (from the current paper):\n{ctx_blob}\n\n"
            "Return JSON with:\n"
            "  viewpoint  (string, 1-2 sentences, the current paper's stance on this work)\n"
            "  category   (one of: builds_on | contrasts | limitation | motivation | "
            "method_source | data_source | example | other)\n"
            'If the citing sentences do not actually express a stance, return {"viewpoint": "", "category": "other"}.'
        )

        data = self._llm.complete_json(
            prompt,
            system="You are a careful academic research assistant. Return only valid JSON.",
            tier=self._tier_lit_review,
        )

        viewpoint = str(data.get("viewpoint", "")).strip()
        if not viewpoint:
            return None
        category = str(data.get("category", "other")).strip().lower()
        allowed = {"builds_on", "contrasts", "limitation", "motivation",
                   "method_source", "data_source", "example", "other"}
        if category not in allowed:
            category = "other"

        return LitReviewEntryRecord(
            paper_id=paper_id,
            section_id=section_id,
            cited_ref_id=ref.id,
            viewpoint=viewpoint,
            context=ctx_blob,
            category=category,
            llm_model=self.model_label,
        )
