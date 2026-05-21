"""
Rule-based section type classifier.

Maps a free-form section heading (e.g. "3.2 Proposed Method", "II. Related Work",
"实验结果", "Discussion and Future Work") to a canonical `section_type` string.

The classifier is intentionally heading-only (no body inspection) and pure-Python
so it can run during ingest with zero LLM cost. Coverage is expected around 80-90%
on conference/journal papers with standard headings; unmatched headings fall back
to `"other"` and can be re-classified later with an LLM pass if needed.

Used by:
  - converters.sections_to_records() — at ingest time
  - cli.py classify-sections — backfill for already-ingested papers
"""

from __future__ import annotations

import re

# Canonical section types. Order is documentation only; classification uses
# the priority list in CLASSIFY_RULES below.
SECTION_TYPES = (
    "abstract",
    "intro",
    "related_work",
    "methods",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "acknowledgments",
    "references",
    "appendix",
    "other",
)


# ── Normalization ─────────────────────────────────────────────────

# Leading numbering: "1.", "1.2.", "1.2.3", "I.", "IV)", "A.", "A.1"
_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+)*"           # 1, 1.2, 1.2.3
    r"|[IVXLCDM]+"             # roman
    r"|[A-Z]"                  # A, B, ...
    r")"
    r"\s*[\.\)\:、\s]+\s*",
    re.IGNORECASE,
)

# Punctuation we strip from the tail of a heading
_TAIL_PUNCT_RE = re.compile(r"[\s\.\:\;\!\?\、\,，。：；]+$")


def normalize_heading(heading: str) -> str:
    """Lowercase + strip numbering prefix + collapse whitespace.

    Returns "" for empty / numbers-only / single-char headings.
    """
    if not heading:
        return ""
    text = heading.strip()
    # Strip numbering prefix (may apply twice for "1. A. Foo")
    for _ in range(2):
        new = _NUM_PREFIX_RE.sub("", text, count=1)
        if new == text:
            break
        text = new
    text = _TAIL_PUNCT_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if len(text) < 2:
        return ""
    # Pure-number leftovers ("1", "1 2 3") → not a real heading
    if re.fullmatch(r"[\d\s\.]+", text):
        return ""
    return text


# ── Rules ─────────────────────────────────────────────────────────

# Each rule = (section_type, list of substring keywords).
# Order matters: first matching rule wins. Keywords are lowercase and matched
# against the normalized heading via substring containment. Put more-specific
# rules first (e.g. "related work" before "methods" so "related work" doesn't
# get caught by a stray "work" keyword).
CLASSIFY_RULES: list[tuple[str, list[str]]] = [
    # 1. Abstract — usually unique, easy
    ("abstract", ["abstract", "摘要"]),

    # 2. References / Bibliography — must come before anything that contains "reference"
    ("references", ["references", "bibliography", "参考文献", "引用文献"]),

    # 3. Acknowledgments — distinctive
    ("acknowledgments", ["acknowledgment", "acknowledgement", "致谢", "鸣谢"]),

    # 4. Appendix / Supplementary
    ("appendix", ["appendix", "appendices", "supplement", "supplementary",
                  "附录", "补充材料"]),

    # 5. Related Work / Literature Review — before "methods" / "results" to win compounds
    ("related_work", ["related work", "related works", "literature review",
                      "prior work", "previous work", "state of the art",
                      "state-of-the-art", "相关工作", "文献综述", "相关研究"]),

    # 6. Conclusion / Summary / Future Work — before "discussion" so
    #    "Discussion and Conclusion" lands as conclusion (heavier signal).
    ("conclusion", ["conclusion", "conclusions", "concluding", "future work",
                    "future research", "future directions", "summary",
                    "closing remarks", "结论", "总结", "未来工作", "展望"]),

    # 7. Results / Findings / Analysis / Ablation — before "discussion" so
    #    "Results and Discussion" (very common heading) lands as results, and
    #    before "experiments" so "Experimental Results" lands as results.
    ("results", ["result", "results", "finding", "findings", "performance",
                 "ablation", "analysis", "validation", "case study",
                 "case studies", "结果", "分析", "效果", "性能"]),

    # 8. Discussion / Limitations / Implications
    ("discussion", ["discussion", "limitation", "limitations", "implication",
                    "implications", "threats to validity", "讨论", "局限"]),

    # 9. Experiments / Setup / Implementation / Training — distinct from results
    ("experiments", ["experiment", "experiments", "experimental setup",
                     "evaluation setup", "implementation", "implementations",
                     "training", "training details", "dataset", "datasets",
                     "benchmark", "benchmarks", "evaluation", "evaluations",
                     "setup", "实验", "数据集", "训练"]),

    # 10. Methods — broad bucket
    ("methods", ["method", "methods", "methodology", "approach", "approaches",
                 "proposed method", "proposed approach", "proposed model",
                 "model", "algorithm", "algorithms", "framework", "architecture",
                 "system design", "design", "formulation", "problem statement",
                 "problem formulation", "preliminaries", "definition",
                 "definitions", "notation", "方法", "模型", "算法", "框架",
                 "架构", "问题描述", "问题定义"]),

    # 11. Introduction / Background / Motivation
    ("intro", ["introduction", "background", "motivation", "overview",
               "objective", "objectives", "scope", "contribution",
               "contributions", "引言", "导论", "概述", "背景", "动机"]),
]


# ── Classifier ────────────────────────────────────────────────────


def classify_section_type(heading: str) -> str:
    """Map a heading string to a canonical section_type.

    Returns one of SECTION_TYPES. Unmatched / empty / number-only headings
    return `"other"`.
    """
    norm = normalize_heading(heading)
    if not norm:
        return "other"
    for section_type, keywords in CLASSIFY_RULES:
        for kw in keywords:
            if kw in norm:
                return section_type
    return "other"


# ── Coverage helper (used by backfill CLI) ────────────────────────


def coverage_breakdown(headings: list[str]) -> dict[str, int]:
    """Return a count-by-type dict over a list of headings. Useful for
    eyeballing rule-coverage after a backfill run.
    """
    counts: dict[str, int] = {t: 0 for t in SECTION_TYPES}
    for h in headings:
        counts[classify_section_type(h)] += 1
    return counts
