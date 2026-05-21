"""
Query → retrieval intent → section-weighted retrieval profile.

The user's question implicitly tells us which parts of a paper to look at:
  - "summarize X" → abstract + conclusion + intro
  - "how does X work" → methods (+ experiments)
  - "what gaps exist in X" → discussion + conclusion + related_work
  - "what's been done on X" → related_work + intro

`classify_intent(query)` returns a `RetrievalProfile` whose `section_weights`
are consumed by `Retriever.section_search()` as a per-section-type multiplier.

Classification is rule-based (regex + keyword) by default — zero LLM cost on
the hot path. Set `use_llm=True` to fall back to a single simple-tier LLM call
when no rule matches.

Adding a new intent: append to INTENT_PROFILES + INTENT_RULES below. Keep
rules ordered most-specific → least-specific (first match wins).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from section_classifier import SECTION_TYPES


# ── Profiles (section_type → weight) ──────────────────────────────
#
# Weights are relative multipliers applied to per-section cosine scores.
# Section types NOT in the dict get weight 0 (excluded from the section path).
# A profile's weights need not sum to 1 — the section path is RRF-fused with
# the abstract + FTS paths, so only the rank order it produces matters.

INTENT_PROFILES: dict[str, dict[str, float]] = {
    "summary": {
        "abstract": 2.0,
        "conclusion": 2.0,
        "intro": 1.0,
    },
    "methodology": {
        "methods": 2.5,
        "experiments": 1.0,
        "intro": 0.5,
    },
    "experiments_results": {
        "results": 2.0,
        "experiments": 2.0,
        "discussion": 1.0,
    },
    "related_work": {
        "related_work": 2.5,
        "intro": 1.5,
    },
    "gap": {
        "discussion": 2.0,
        "conclusion": 1.5,
        "related_work": 1.0,
        "intro": 0.5,
    },
    "definitions": {
        "intro": 2.0,
        "abstract": 1.5,
        "methods": 1.0,
    },
    # `default` is special-cased: no section path is added (falls back to
    # abstract + FTS only). Lookup callers check the intent name, not the dict.
    "default": {},
}


# Intents that benefit from searching LLM-extracted viewpoints
# (`lit_review_entries`) — queries about how papers argue, compare, or position
# themselves vs prior work. The viewpoint corpus is short, distilled text
# with explicit claims, so it's the best surface for these intent families.
INTENT_USES_VIEWPOINTS: set[str] = {
    "related_work",
    "gap",
    "summary",        # summarization queries often want the paper's claims
    "experiments_results",  # comparison statements live in viewpoints
}


# ── Rules (priority-ordered) ──────────────────────────────────────
#
# First matching rule wins. Each entry is (intent_name, regex_patterns).
# Patterns are matched case-insensitively against the lowercased query.

INTENT_RULES: list[tuple[str, list[str]]] = [
    # 1. Summary — must be checked before `definitions` so "what is this paper"
    #    (summary) wins over the generic "what is X" (definitions).
    ("summary", [
        r"\bsummari[sz]e\b", r"\bsummary\b", r"\boverview\b",
        r"\btl;?dr\b",
        r"\bwhat (is|are) (this|the) paper", r"\babout the paper\b",
        r"\bmain contribution", r"\bkey takeaway",
        r"概述", r"概要", r"主要内容", r"核心观点", r"总结",
    ]),

    # 2. Related work — before methodology, so "literature on X" doesn't get
    #    eaten by some stray "method" mention.
    ("related_work", [
        r"\brelated work\b", r"\bliterature review\b", r"\bprior work\b",
        r"\bprevious work\b", r"\bstate[- ]of[- ]the[- ]art\b",
        r"\bsurvey of\b", r"\breview of (?:the )?literature\b",
        r"\bwhat has been done\b", r"\bexisting (?:approaches|methods|work)\b",
        r"\bhas anyone\b",
        r"相关工作", r"文献综述", r"已有工作", r"相关研究",
        r"前人工作", r"综述",
    ]),

    # 3. Gap / limitations / future work
    ("gap", [
        r"\bgaps?\b", r"\blimitation", r"\bweakness", r"\bshortcoming",
        r"\bopen (?:problem|question|challenge)",
        r"\bfuture work\b", r"\bfuture (?:research|direction)",
        r"\bunsolved\b", r"\bunaddressed\b",
        r"未解决", r"局限", r"不足", r"未来工作",
        r"改进空间", r"缺陷", r"空白",
    ]),

    # 4. Methodology — before results, because "how does X compare" leans
    #    methodology (the comparison method itself).
    ("methodology", [
        r"\bmethodolog", r"\bapproach\b", r"\bhow (?:does|do|did) (?:they|it|the)",
        r"\bhow (?:is|are) .* (?:implemented|computed|trained)",
        r"\balgorithm\b", r"\bmodel architecture\b", r"\bmodel design\b",
        r"\bproposed method\b", r"\bproposed approach\b",
        r"\btechnique used\b", r"\bimplementation detail",
        r"方法论", r"如何实现", r"怎么做", r"怎么实现",
        r"算法", r"模型架构", r"技术方案",
    ]),

    # 5. Experiments / results / numbers
    ("experiments_results", [
        r"\bresult", r"\bexperiment", r"\bevaluation\b", r"\bbenchmark",
        r"\bperformance\b", r"\baccuracy\b", r"\b(?:f1|f-1)\b", r"\bauc\b",
        r"\bablation\b", r"\boutperform", r"\b(?:vs\.?|versus)\b",
        r"\bcompared (?:to|with)\b",
        r"实验", r"结果", r"性能", r"效果", r"对比",
        r"评估", r"评测",
    ]),

    # 6. Definitions — checked LAST so "what is this paper" already routed
    #    to summary by rule 1.
    ("definitions", [
        r"\bwhat is\b", r"\bwhat are\b", r"\bdefine\b", r"\bdefinition of\b",
        r"\bmeaning of\b", r"\bconcept of\b", r"\bexplain the term\b",
        r"什么是", r"定义", r"概念", r"含义",
    ]),
]


# ── Public API ────────────────────────────────────────────────────


@dataclass
class RetrievalProfile:
    intent: str                                      # e.g. "summary" or "default"
    section_weights: dict[str, float] = field(default_factory=dict)
    matched_by: str = "rule"                         # "rule" | "llm" | "manual" | "default"
    matched_pattern: Optional[str] = None            # regex string that fired (rule mode)

    @property
    def is_default(self) -> bool:
        """Default profile = no section recall path is added."""
        return self.intent == "default" or not self.section_weights

    @property
    def use_viewpoints(self) -> bool:
        """Should the retriever also pull from viewpoint embeddings?"""
        return self.intent in INTENT_USES_VIEWPOINTS


def get_profile(intent: str) -> RetrievalProfile:
    """Construct a profile from an intent name. Unknown names → default."""
    weights = INTENT_PROFILES.get(intent)
    if weights is None:
        return RetrievalProfile(intent="default", matched_by="default")
    # Sanity: drop any weight keys that aren't valid section types
    safe = {k: v for k, v in weights.items() if k in SECTION_TYPES}
    return RetrievalProfile(
        intent=intent,
        section_weights=safe,
        matched_by="manual",
    )


def classify_intent(query: str, *, use_llm: bool = False,
                    llm_provider=None) -> RetrievalProfile:
    """Resolve a free-form query to a RetrievalProfile.

    Strategy: try regex rules (free). If no rule matches and `use_llm=True`,
    fall back to a single simple-tier LLM call. Otherwise return `default`.
    """
    if not query or not query.strip():
        return RetrievalProfile(intent="default", matched_by="default")

    q = query.lower()
    for intent_name, patterns in INTENT_RULES:
        for pat in patterns:
            if re.search(pat, q):
                weights = INTENT_PROFILES.get(intent_name, {})
                return RetrievalProfile(
                    intent=intent_name,
                    section_weights={k: v for k, v in weights.items()
                                     if k in SECTION_TYPES},
                    matched_by="rule",
                    matched_pattern=pat,
                )

    if use_llm and llm_provider is not None:
        try:
            return _llm_classify(query, llm_provider)
        except Exception:
            pass

    return RetrievalProfile(intent="default", matched_by="default")


# ── LLM fallback (opt-in) ─────────────────────────────────────────


_LLM_PROMPT = """\
You classify a research-paper search query into one retrieval intent.
Pick exactly ONE label from this list (return JSON only):

  summary             — user wants the paper's overall point / abstract / takeaway
  related_work        — user is surveying prior work or asking what's been done on X
  methodology         — user is asking how something works / is implemented
  experiments_results — user wants empirical results, numbers, comparisons, ablations
  gap                 — user is hunting limitations / open problems / future directions
  definitions         — user wants a concept / term defined
  default             — none of the above; treat as generic retrieval

Return: {"intent": "<label>"}
"""


def _llm_classify(query: str, provider) -> RetrievalProfile:
    """Single simple-tier LLM call. Returns default profile on any parse failure."""
    import json
    response = provider.complete_json(
        system=_LLM_PROMPT,
        user=f"Query: {query}",
        tier="simple",
    )
    label = (response.get("intent") or "default").strip()
    if label not in INTENT_PROFILES:
        label = "default"
    weights = INTENT_PROFILES.get(label, {})
    return RetrievalProfile(
        intent=label,
        section_weights={k: v for k, v in weights.items() if k in SECTION_TYPES},
        matched_by="llm",
    )


# ── Debug helpers ─────────────────────────────────────────────────


def all_intents() -> list[str]:
    return list(INTENT_PROFILES.keys())
