"""
LLMReranker — re-orders a candidate list by passing titles + abstracts to an
LLM and asking it to rank by query relevance.

Default tier is "simple" (fast, no thinking) because rerank quality vs cost is
much better on a flash-class model — the heavy lifting is done by the
retrievers; rerank just needs to read short text and put numbers in order.
Override via `LLM_TIER_RERANK=complex` if you want thinking mode.

Falls back to the fused order on any error so search never hard-fails.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from llm_interface import LLMProvider
from retrieval import SearchHit


MAX_RERANK_CANDIDATES = 30
MAX_ABSTRACT_CHARS = 600


@dataclass
class RerankResult:
    paper_id: int
    score: float        # 0.0 - 1.0 relevance to query
    reason: str         # one short sentence


class LLMReranker:

    def __init__(self, provider: LLMProvider):
        self._llm = provider
        self._tier = (os.environ.get("LLM_TIER_RERANK", "simple")).strip().lower()
        if self._tier not in ("complex", "simple"):
            self._tier = "simple"

    def rerank(
        self,
        query: str,
        candidates: list[SearchHit],
        top_k: int = 10,
    ) -> list[SearchHit]:
        if not candidates:
            return []
        candidates = candidates[:MAX_RERANK_CANDIDATES]

        block_lines = []
        for c in candidates:
            abstract = (c.abstract or "")[:MAX_ABSTRACT_CHARS]
            block_lines.append(
                f"- id={c.paper_id}\n"
                f"  title: {c.title}\n"
                f"  abstract: {abstract}"
            )
        catalog = "\n".join(block_lines)

        prompt = (
            f"USER QUERY:\n{query.strip()}\n\n"
            f"CANDIDATE PAPERS:\n{catalog}\n\n"
            "Score each candidate's relevance to the query on a scale 0.0-1.0 "
            "(1.0 = perfect match, 0.0 = unrelated). Be discriminating; avoid "
            "clustering scores near 0.5.\n\n"
            'Return JSON with key "ranked" whose value is an array of objects:\n'
            "  id      (integer; from the list above)\n"
            "  score   (float 0.0-1.0)\n"
            "  reason  (one short sentence, ≤ 25 words)\n"
            "Include every candidate exactly once, sorted highest score first."
        )

        try:
            data = self._llm.complete_json(
                prompt,
                system="You are a research librarian. Be concise. Return only JSON.",
                tier=self._tier,
            )
            ranked_raw = data.get("ranked") or data.get("results") or []
            parsed: dict[int, RerankResult] = {}
            for item in ranked_raw:
                try:
                    pid = int(item.get("id"))
                    score = float(item.get("score", 0.0))
                except (TypeError, ValueError):
                    continue
                reason = str(item.get("reason", "")).strip()
                score = max(0.0, min(1.0, score))
                parsed[pid] = RerankResult(paper_id=pid, score=score, reason=reason)
        except Exception:
            # Fall back to fused order, no extra annotation
            return candidates[:top_k]

        # Merge rerank scores onto SearchHit; fall back to fused score
        reranked: list[SearchHit] = []
        for c in candidates:
            rr = parsed.get(c.paper_id)
            if rr is None:
                c.source_scores["rerank"] = 0.0
            else:
                c.score = rr.score
                c.source_scores["rerank"] = rr.score
                c.rerank_reason = rr.reason
            reranked.append(c)

        reranked.sort(key=lambda h: -h.source_scores.get("rerank", 0.0))
        return reranked[:top_k]
