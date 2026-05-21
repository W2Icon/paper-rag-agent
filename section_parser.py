"""
Section parser — detect section headings and group text lines into structured
sections with paragraphs from the parts output of get_ref_lines(full_text=True).
"""

import re
from dataclasses import dataclass, field

# Heading numbering patterns
_NUMBERED_RE = re.compile(
    r"^(?:\d+\.\d+\.\d+|\d+\.\d+|\d+|[IVX]+)(?:\.|\s|\))\s+([A-Z一-龥])"
)

# Section heading keywords (lowercase)
_SECTION_KW = {
    "introduction", "method", "methods", "methodology", "result", "results",
    "discussion", "conclusion", "conclusions", "background", "related work",
    "experiment", "experiments", "experimental", "evaluation", "implementation",
    "approach", "overview", "summary", "future work", "acknowledgment",
    "acknowledgements", "acknowledgement", "appendix", "references", "bibliography",
    "abstract", "literature review", "problem statement", "proposed method",
    "analysis", "case study", "limitation", "limitations", "model", "algorithm",
    "dataset", "data", "preliminaries", "formulation", "setup", "design",
    "performance", "validation", "ablation", "findings", "implications",
    "contributions", "contribution", "motivation", "objective", "objectives",
    "scope", "framework", "architecture", "training", "inference", "deployment",
    "引言", "方法", "结果", "讨论", "结论", "摘要", "参考文献", "致谢",
    "相关工作", "背景", "实验", "分析", "总结",
}

# Journal masthead / footer / front-matter noise.
# Each pattern matches a SINGLE line that should be dropped before paragraph
# grouping and never considered as a heading. These come up on the first page
# of nearly every Elsevier / Springer / IEEE PDF and pollute downstream
# sections when not filtered.
_NOISE_PATTERNS = [
    re.compile(r"^Contents\s+lists?\s+available\s+at", re.I),
    re.compile(r"^journal\s+homepage\s*[:：]", re.I),
    re.compile(r"^https?://", re.I),
    re.compile(r"^©\s*\d{4}", re.I),
    re.compile(r"^\(?cc\)?\s*by[-\s]", re.I),                  # CC BY-NC-ND license blurb
    re.compile(r"^\d{3,4}-\d{3,4}\s*/", re.I),                 # ISSN prefix "0360-5442/"
    re.compile(r"^\*?\s*Corresponding\s+author", re.I),
    re.compile(r"^E-?mail\s+address(?:es)?\s*[:：]", re.I),
    re.compile(r"^Article\s+history\s*[:：]", re.I),
    re.compile(r"^Received\s+(?:in\s+revised\s+form|\d)", re.I),
    re.compile(r"^Accepted\s+\d", re.I),
    re.compile(r"^Available\s+online", re.I),
    re.compile(r"^Published\s+by\s+Elsevier", re.I),
    re.compile(r"^This\s+is\s+an\s+open[-\s]access\s+article", re.I),
    # Volume citation in masthead, e.g. "Energy 141 (2017) 1955e1968"
    re.compile(r"^[A-Z][A-Za-z &-]{2,40}\s+\d+\s+\(\d{4}\)\s+\d", ),
    # Elsevier letter-spaced front-matter labels
    re.compile(r"^a\s+r\s+t\s+i\s+c\s+l\s+e\s+i\s+n\s+f\s+o\s*$", re.I),
    re.compile(r"^a\s+b\s+s\s+t\s+r\s+a\s+c\s+t\s*$", re.I),
    re.compile(r"^g\s+r\s+a\s+p\s+h\s+i\s+c\s+a\s+l\s+a\s+b\s+s\s+t\s+r\s+a\s+c\s+t\s*$", re.I),
    re.compile(r"^Keywords?\s*[:：]\s*$", re.I),
    re.compile(r"^Dataset\s+link\s*[:：]", re.I),
    # Affiliation lines (university/lab + city + country, often comma-separated)
    re.compile(
        r"^\s*(?:State\s+Key\s+Lab|.*?(?:University|Institute|Laboratory|"
        r"College|School|Department|Academy)).{5,}\b(?:China|USA|UK|Korea|Japan|"
        r"India|Canada|Germany|France|Italy|Spain|Australia)\b",
        re.I,
    ),
    # Single lowercase letter (affiliation footnote marker like "a", "b")
    re.compile(r"^[a-z]\s*$"),
    # Figure axis labels like "Speed [km/h]", "Motor torque [Nm]", "Power [kW]"
    re.compile(
        r"^[A-Z][A-Za-z ]{2,30}\s*\[\s*(?:km/h|m/s|Nm|N\s*m|kW|kWh|kJ|MJ|"
        r"m|s|ms|min|Hz|kg|°C|°|%|V|A|Ω|J|W|rad|rpm|deg|MPa|kPa|Pa)\s*\]\s*$",
        re.I,
    ),
    # Pure axis-label with no number ("Vehicle efficiency", "Motor efficiency")
    # — too ambiguous to filter on text alone; keep height-based heading rule
    # responsible for these (it requires multi-word + non-period).
]


def _is_noise(text: str) -> bool:
    """Return True if line is masthead/footer/affiliation/axis-label junk."""
    t = text.strip()
    if not t:
        return True
    for pat in _NOISE_PATTERNS:
        if pat.search(t):
            return True
    return False


@dataclass
class Section:
    heading: str
    level: int  # 1, 2, 3
    paragraphs: list[str]
    page_start: int
    page_end: int
    # Optional rich content — populated by layout-aware backends (MinerU).
    # PyMuPDF backend leaves these empty for backward compatibility.
    tables: list[dict] = field(default_factory=list)      # {html, markdown, caption, page, bbox}
    formulas: list[dict] = field(default_factory=list)    # {latex, type, page}
    images: list[dict] = field(default_factory=list)      # {path, caption, page}

    def to_dict(self) -> dict:
        return {
            "heading": self.heading,
            "level": self.level,
            "paragraphs": self.paragraphs,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "tables": self.tables,
            "formulas": self.formulas,
            "images": self.images,
        }


def _is_heading(text: str, line_height: float, body_avg_height: float, part_line_count: int) -> bool:
    """Determine if a line looks like a section heading.

    Rules (in priority order):
      1. Reject empty / over-long / single-char / pure-punct lines.
      2. Reject masthead-footer noise (journal name, DOI, affiliation, axis label).
      3. Reject equation fragments (incl. Elsevier `¼` for `=`, ð/þ glyphs).
      4. ACCEPT numbered headings: `1.`, `2.1`, `3.3.1`, `I.`, `A.`.
      5. ACCEPT exact keyword match ("Introduction", "Conclusions", etc.).
      6. ACCEPT short keyword-prefixed lines ("Related Work", "Problem Statement").
      7. Height-based fallback is STRICT: needs ≥3 words, no terminal body punct,
         starts with capital, not in noise patterns. Without these guards the
         masthead "Energy" (single word, larger font) becomes a false heading
         that swallows the abstract + intro.
    """
    text_stripped = text.strip()
    if not text_stripped or len(text_stripped) > 150:
        return False
    if len(text_stripped) <= 2:
        return False
    if _is_noise(text_stripped):
        return False
    if _is_equation(text_stripped):
        return False
    if re.match(r"^[\d\s\.\,\;\:\!\?\+\-\*\/\=\>\<\(\)\[\]\{\}\|\\∑∫∏√∞≈≠≤≥±×÷∂∇∆∈∉⊂⊆∪∩∧∨→⇒↔∀∃∄]+$", text_stripped):
        return False

    # Rule 4: Numbered patterns: "1.", "2.1", "3.3.1", "I.", "A."
    if re.match(r"^(?:\d+\.\d+\.\d+|\d+\.\d+|\d+|[IVXivx]+)(?:\.|\s|\))\s+[A-Z一-龥]", text_stripped):
        if len(text_stripped) < 120:
            return True

    # Rule 5 + 6: Keyword matches (unnumbered headings).
    t_lower = text_stripped.lower().rstrip(".:").strip()
    if t_lower in _SECTION_KW:
        return True
    if any(t_lower.startswith(kw + " ") or t_lower.startswith(kw + ":") for kw in _SECTION_KW if " " in kw):
        return True
    # Single-word keyword followed by short qualifier ("Methods overview", "Results: ...")
    first_word = t_lower.split()[0] if t_lower else ""
    if first_word in _SECTION_KW and len(t_lower.split()) <= 6:
        return True

    # Rule 7: Strict height-based fallback. Multiple guards prevent the
    # journal-name masthead ("Energy") and figure axis labels from being
    # treated as headings.
    if body_avg_height > 0 and line_height > body_avg_height * 1.3:
        word_count = len(text_stripped.split())
        if (
            word_count >= 3
            and len(text_stripped) >= 8
            and len(text_stripped) <= 100
            and not text_stripped.endswith((".", ",", ";", ":", "?", "!"))
            and text_stripped[0].isupper()
        ):
            return True

    return False


def _determine_level(text: str, line_height: float, body_avg_height: float) -> int:
    """Determine heading level (1, 2, or 3)."""
    stripped = text.strip()

    # Level 3: three-part numbers like "3.3.1" or "A.1.1"
    if re.match(r"^(?:\d+\.\d+\.\d+|[A-Z]\.\d+\.\d+)", stripped):
        return 3

    # Level 2: two-part numbers like "3.1" or "A.1"
    if re.match(r"^(?:\d+\.\d+|[A-Z]\.\d+)", stripped):
        return 2

    # Level 1: single numbers like "1." or "I." or no number
    if re.match(r"^(?:\d+|[IVXivx]+|[A-Z])(?:\.|\s|\))", stripped):
        return 1

    # Unnumbered: if font is significantly larger than body → level 1
    if body_avg_height > 0 and line_height > body_avg_height * 1.3:
        return 1

    # Top-level keyword headings get level 1
    t_lower = stripped.lower().rstrip(".")
    if t_lower in {"introduction", "method", "methods", "methodology",
                    "result", "results", "discussion", "conclusion", "conclusions",
                    "references", "bibliography", "abstract", "acknowledgment",
                    "acknowledgements", "acknowledgement", "appendix",
                    "background", "related work", "experimental", "experiment",
                    "experiments", "evaluation", "implementation",
                    "引言", "方法", "结果", "讨论", "结论", "摘要", "参考文献", "致谢",
                    "相关工作", "背景", "实验", "分析", "总结"}:
        return 1

    return 2  # default unnumbered → level 2


def _group_paragraphs(lines: list["PDFLine"]) -> list[str]:  # noqa: F821
    """Group text lines into paragraphs based on vertical spacing and indentation."""
    if not lines:
        return []

    lines = [l for l in lines if l.text.strip()]
    if not lines:
        return []

    paragraphs: list[str] = []
    current_para: list[str] = [lines[0].text.strip()]
    body_height = _mode_height([l.height for l in lines])

    for i in range(1, len(lines)):
        prev = lines[i - 1]
        curr = lines[i]
        gap = abs(curr.y - (prev.y + prev.height))
        indent_change = abs(curr.x - prev.x)
        curr_text = curr.text.strip()

        # Skip equation/isolated math fragments
        if _is_equation(curr_text):
            continue

        # New paragraph conditions:
        # 1. Large vertical gap (> 2x body line height)
        # 2. Significant first-line indent increase (paragraph start)
        new_para = False
        if gap > body_height * 2.5:
            new_para = True
        elif indent_change > body_height * 1.5 and curr.x > prev.x:
            new_para = True

        if new_para:
            if current_para:
                para_text = " ".join(current_para).strip()
                if len(para_text) > 10:
                    paragraphs.append(para_text)
            current_para = [curr_text]
        else:
            # Continue paragraph: handle hyphen breaks
            if prev.text.strip().endswith("-"):
                current_para[-1] = current_para[-1].rstrip("-") + curr_text
            else:
                current_para.append(curr_text)

    if current_para:
        para_text = " ".join(current_para).strip()
        if len(para_text) > 10:
            paragraphs.append(para_text)

    return paragraphs


def _is_equation(text: str) -> bool:
    """Check if text looks like a standalone math equation fragment.

    PyMuPDF + Elsevier's font remapping injects several false-positive
    'words' that look like garbled math:
      - `¼` is used in place of `=` (e.g. "F grade ¼ mg h")
      - `ð` and `þ` substitute for `(` and `)` in subscripts (e.g. "ð i Þ")
      - Lots of single-char "words" because subscripts are exploded into
        separate tokens ("K r e   0 : 0411 adec ð i Þ  v ð i Þ")
    Detect these so they're filtered out of heading candidates.
    """
    t = text.strip()
    if not t:
        return True
    # Short text with math/Unicode math symbols (incl. Elsevier OCR artifacts)
    math_chars = sum(1 for c in t if c in "∑∫∏√∞≈≠≤≥±×÷∂∇∆∈∉⊂⊆∪∩∧∨→⇒↔∀∃∄‖∣⌊⌋⌈⌉⟨⟩¼½¾°§†‡¶")
    if math_chars >= 1 and len(t) < 60:
        return True
    # Contains "=" or "¼" and math notation (subscripts, fractions)
    if any(eq in t for eq in ("=", "¼", "≈", "≃")) and re.search(r"[∑∫√∞≈≠≤≥±×÷∂∇∆ðþ]", t):
        return True
    # OCR'd brackets ð / þ (Elsevier remapping) — almost always equations
    if ("ð" in t or "þ" in t) and len(t) < 120:
        return True
    # Looks like a standalone math fragment: short, has Greek/script chars
    if len(t) < 30 and re.search(r"[\U0001D400-\U0001D7FF]", t):
        return True
    # Just a number, punctuation, or isolated math
    if re.match(r"^[\d\s\.\,\;\:\!\?\+\-\*\/\=\>\<\(\)\[\]\{\}\|\\]+$", t) and len(t) < 20:
        return True
    # Token-level heuristic: many single-char "words" → equation fragment
    # ("X n i ¼ 1 E loss load ð i Þ", "F grade ¼ mg h").
    tokens = t.split()
    if len(tokens) >= 3:
        single_char = sum(1 for w in tokens if len(w) == 1)
        if single_char / len(tokens) >= 0.5 and len(t) < 100:
            return True
    # Standalone equation like "𝑁Baseline2 =" or "MAPE = 100%" with math symbols
    if re.match(r"^[A-Za-z][\w\s]{0,20}[=≈¼]\s*[\d\w\s%]", t) and len(t) < 60:
        return True
    return False


def _mode_height(heights: list[float]) -> float:
    """Most common height, used as body text height."""
    if not heights:
        return 8.0
    from collections import Counter
    c = Counter(round(h, 1) for h in heights)
    return c.most_common(1)[0][0]


def parse_sections(parts: list[list]) -> list[Section]:
    """
    Convert raw parts from get_ref_lines(full_text=True) into structured sections.

    Strategy: linear scan across all lines (after dropping noise), splitting at
    every heading. This handles the common case where one "part" contains
    multiple actual paper sections (e.g. abstract body + "1. Introduction"),
    which the previous one-heading-per-part logic missed.

    Args:
        parts: list of list of PDFLine from get_ref_lines(full_text=True)

    Returns:
        list of Section objects with headings and paragraphs.
    """
    # Flatten + drop noise lines (masthead, footer, affiliation, axis labels).
    # We deliberately discard noise BEFORE any heading or paragraph logic so
    # downstream code never has to second-guess them.
    #
    # `parts` arrives in scrambled order (reference_parser scans pages
    # back-to-front and groups by column). Re-sort into forward document
    # reading order so heading detection sees sections in the natural
    # sequence — critical for the in-references gate below to fire AFTER
    # the body content, not before it.
    flat: list = []
    for part in parts:
        for ln in part:
            t = ln.text.strip()
            if not t:
                continue
            if _is_noise(t):
                continue
            flat.append(ln)

    if not flat:
        return []

    flat.sort(key=lambda l: (l.page_num, getattr(l, "column", 0), l.y, l.x))

    body_height = _mode_height([l.height for l in flat])

    sections: list[Section] = []
    current: Section | None = None
    buf: list = []  # body lines accumulating into current section
    in_references = False  # once True, suppress further heading splits

    def _flush_buf():
        nonlocal buf
        if current is None or not buf:
            buf = []
            return
        new_paras = _group_paragraphs(buf)
        current.paragraphs.extend(new_paras)
        pages = [l.page_num + 1 for l in buf]
        if pages:
            current.page_start = min(current.page_start, min(pages))
            current.page_end = max(current.page_end, max(pages))
        buf = []

    def _is_references_heading(t: str) -> bool:
        norm = t.lower().rstrip(".:").strip()
        if norm in {"references", "bibliography", "参考文献", "reference list"}:
            return True
        return bool(re.match(r"^(?:\d+\.\s+)?(?:references|bibliography)\b", norm))

    for ln in flat:
        text = ln.text.strip()
        is_head = _is_heading(text, ln.height, body_height, 1)

        # Once we've entered the references section we stop creating new
        # sections from numbered items ("1. Author, B...."). The whole
        # bibliography stays as one section's body — individual refs live
        # in the separate `references_` table.
        if in_references and is_head and not _is_references_heading(text):
            is_head = False

        if is_head:
            _flush_buf()
            if current is not None:
                sections.append(current)
            level = _determine_level(text, ln.height, body_height)
            current = Section(
                heading=text,
                level=level,
                paragraphs=[],
                page_start=ln.page_num + 1,
                page_end=ln.page_num + 1,
            )
            if _is_references_heading(text):
                in_references = True
        else:
            # Body line — buffer into current section. If no section is open
            # yet (everything before the first real heading), drop the lines;
            # they're typically frontmatter that survived the noise filter.
            if current is not None:
                buf.append(ln)

    _flush_buf()
    if current is not None:
        sections.append(current)

    # Merge adjacent sections with the same heading text (defensive — same
    # heading repeated across page breaks).
    merged: list[Section] = []
    for s in sections:
        if merged and s.heading == merged[-1].heading:
            merged[-1].paragraphs.extend(s.paragraphs)
            merged[-1].page_end = max(merged[-1].page_end, s.page_end)
        else:
            merged.append(s)

    return merged
