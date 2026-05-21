"""
Citation finder — scan body text for in-text citation markers.

Supports:
- Numbered citations: [1], [2,3], [4-6], (1), (2,3), [1], [2,3]
- Unicode superscript digits: ⁰¹²³⁴⁵⁶⁷⁸⁹
- Author-year: (Author and Author, YYYY), (Author et al., YYYY), Author et al. (YYYY)
"""

import re
from dataclasses import dataclass

import fitz

# ── Numbered citation patterns ────────────────────────────────────

# Bracket/paren numbered: [1], [2,3], [4-6], (1), (2,3), [1], [2,3]
_NUMBERED_CITATION_RE = re.compile(
    r"\[(\d{1,3}(?:[-,]\d{1,3})*)\]"
    r"|"
    r"\((\d{1,3}(?:[-,]\d{1,3})*)\)"
    r"|"
    r"［(\d{1,3}(?:[-,]\d{1,3})*)］"
)

# Unicode superscript digits mapping
_SUPER_DIGITS: dict[str, str] = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3",
    "⁴": "4", "⁵": "5", "⁶": "6",
    "⁷": "7", "⁸": "8", "⁹": "9",
}
_SUPER_MINUS = "⁻"  # ⁻

# Match sequences of superscript digits, e.g., "¹²" or "³⁻⁵"
_SUPERSCRIPT_RE = re.compile(rf"[{''.join(_SUPER_DIGITS)}](?:[{_SUPER_MINUS},{''.join(_SUPER_DIGITS)}]*[{''.join(_SUPER_DIGITS)}])?")

# Sentence boundary
_SENTENCE_BOUNDARY = re.compile(r"[.!?]\s+[A-Z一-龥]|[.!?]\n|[.!?]$|\n\n")


# ── Helpers ───────────────────────────────────────────────────────

def _expand_number_group(s: str) -> list[int]:
    """Expand "1,3-5" into [1, 3, 4, 5]."""
    result: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a.strip()), int(b.strip())
                if end >= start:
                    result.extend(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return result


def _extract_sentence(text: str, match_start: int, match_end: int) -> str:
    """Extract the sentence containing the text at [match_start, match_end)."""
    # Find the previous sentence boundary
    prev = 0
    prefix = text[:match_start]
    for m in _SENTENCE_BOUNDARY.finditer(prefix):
        prev = m.end()
    if prev > 0:
        # The boundary matched a period+space+capital pattern;
        # start after the space (period stays with the sentence)
        if prev >= 2 and text[prev - 2:prev] == ". ":
            prev = prev
        elif prev >= 3 and text[prev - 3:prev] in (". \n", ".\n "):
            prev = prev
        # Strip leading whitespace
        while prev < match_start and text[prev] in " \n":
            prev += 1

    # Find the next sentence boundary
    suffix = text[match_end:]
    next_pos = len(text)
    m = _SENTENCE_BOUNDARY.search(suffix)
    if m:
        next_pos = match_end + m.start() + 1  # include the period

    sentence = text[prev:next_pos].strip()
    # Collapse internal newlines to spaces for readability
    sentence = re.sub(r"\s+", " ", sentence)
    return sentence


def _extract_first_author_lastname(authors: list[str]) -> str:
    """Extract the first author's last name from an authors list."""
    if not authors:
        return ""
    first = authors[0].strip()
    # Remove trailing period
    if first.endswith("."):
        first = first[:-1]
    # Take the first word (before space or comma)
    name = first.split(",")[0].split(" ")[0].strip()
    return name


# ── Author-year pattern builder ────────────────────────────────────

def _build_author_year_patterns(refs_info: list[dict]) -> dict[str, list[int]]:
    """
    Build regex patterns for author-year citations from reference metadata.

    Each refs_info entry should have: ref_number, first_author, year, authors
    Returns dict mapping regex pattern string -> list of ref_numbers.
    """
    patterns: dict[str, list[int]] = {}

    for ri in refs_info:
        ref_num = ri["ref_number"]
        author = re.escape(ri["first_author"])
        year = ri.get("year", "")
        if not author or not year:
            continue
        all_authors = ri.get("authors", [])

        # Build author variants
        author_variants = [author]
        # If 2 authors: "Author1 and Author2"
        if len(all_authors) > 1:
            a2 = _extract_first_author_lastname([all_authors[1]])
            if a2:
                author_variants.append(f"{author} and {re.escape(a2)}")
                author_variants.append(f"{author} & {re.escape(a2)}")
        # et al. variant
        author_variants.append(f"{author} et al\\.")

        for auth_pat in author_variants:
            # Parenthetical: (Author, YYYY) or (Author and Author2, YYYY) or (Author et al., YYYY)
            pat1 = rf"\({auth_pat},?\s*{year}\)"
            # Narrative: Author (YYYY) or Author et al. (YYYY)
            pat2 = rf"{auth_pat}\s*\({year}\)"

            for p in (pat1, pat2):
                patterns.setdefault(p, []).append(ref_num)

    return patterns


def _match_rect(page: fitz.Page, match_text: str) -> tuple[float, float]:
    """Get x, y position for a text match on the page."""
    rects = page.search_for(match_text)
    if rects:
        return float(rects[0].x0), float(rects[0].y0)
    return 0.0, 0.0


# ── Main API ──────────────────────────────────────────────────────

@dataclass
class CitationLocation:
    """A single in-text citation occurrence."""
    page: int
    x: float
    y: float
    text: str       # the matched citation text, e.g. "[1]" or "(Zhou et al., 2016)"
    sentence: str    # the sentence containing this citation

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "sentence": self.sentence,
        }


def find_citations(
    doc: fitz.Document,
    body_pages: range,
    refs_info: list[dict] | None = None,
) -> dict[int, list[CitationLocation]]:
    """
    Scan body pages for in-text citation markers.

    Args:
        doc: PyMuPDF Document.
        body_pages: Range of 0-indexed page numbers to scan.
        refs_info: Optional list of {ref_number, first_author, year, authors}
                   for author-year citation matching.

    Returns a dict mapping reference_number -> list of CitationLocation.
    """
    result: dict[int, list[CitationLocation]] = {}
    seen: set[tuple[int, int, int, int]] = set()

    # Author-year patterns
    author_year_patterns: dict[str, list[int]] = {}
    if refs_info:
        author_year_patterns = _build_author_year_patterns(refs_info)
    _AY_RE = re.compile("|".join(author_year_patterns)) if author_year_patterns else None

    for page_num in body_pages:
        if page_num < 0 or page_num >= len(doc):
            continue
        page = doc[page_num]
        text = page.get_text()
        if not text:
            continue

        # ── Numbered bracket/paren citations ──
        for m in _NUMBERED_CITATION_RE.finditer(text):
            group_text = m.group(0)
            inner = m.group(1) or m.group(2) or m.group(3) or ""
            if not inner:
                continue

            x, y = _match_rect(page, group_text)
            sentence = _extract_sentence(text, m.start(), m.end())

            for ref_num in _expand_number_group(inner):
                key = (page_num, round(x), round(y), ref_num)
                if key in seen:
                    continue
                seen.add(key)
                result.setdefault(ref_num, []).append(
                    CitationLocation(page=page_num + 1, x=x, y=y, text=group_text, sentence=sentence)
                )

        # ── Unicode superscript digits ──
        for m in _SUPERSCRIPT_RE.finditer(text):
            sup_text = m.group(0)
            # Convert superscript digits to regular numbers
            normal = ""
            has_digit = False
            for ch in sup_text:
                if ch in _SUPER_DIGITS:
                    normal += _SUPER_DIGITS[ch]
                    has_digit = True
                elif ch == _SUPER_MINUS:
                    normal += "-"
                elif ch == ",":
                    normal += ","
            if not has_digit or not normal:
                continue

            x, y = _match_rect(page, sup_text)
            sentence = _extract_sentence(text, m.start(), m.end())

            for ref_num in _expand_number_group(normal):
                key = (page_num, round(x), round(y), ref_num)
                if key in seen:
                    continue
                seen.add(key)
                result.setdefault(ref_num, []).append(
                    CitationLocation(page=page_num + 1, x=x, y=y, text=sup_text, sentence=sentence)
                )

        # ── Author-year citations ──
        if _AY_RE:
            for m in _AY_RE.finditer(text):
                match_text = m.group(0)
                ref_nums = author_year_patterns.get(match_text, [])
                if not ref_nums:
                    # Try with re.search to handle partial matches in combined regex
                    for pat, rns in author_year_patterns.items():
                        if re.search(pat, match_text):
                            ref_nums = rns
                            break

                x, y = _match_rect(page, match_text)
                sentence = _extract_sentence(text, m.start(), m.end())

                for ref_num in ref_nums:
                    key = (page_num, round(x), round(y), ref_num)
                    if key in seen:
                        continue
                    seen.add(key)
                    result.setdefault(ref_num, []).append(
                        CitationLocation(page=page_num + 1, x=x, y=y, text=match_text, sentence=sentence)
                    )

    return result
