"""
Core reference parsing algorithms ported from src/modules/pdf.ts.

Handles: page text reading → line merging → reference section detection →
multi-line reference merging → per-entry info extraction.
"""

import re
import copy
from collections import Counter
from typing import Optional

import fitz

from ref_types import PDFItem, PDFLine, ItemInfo
from pdf_reader import read_pdf_page
from utils import ref_text_to_info

# Reference-starting regex patterns (from pdf.ts L13-22)
REF_REGEX: list[list[re.Pattern]] = [
    [re.compile(r"^\(\d+\)\s?")],                                     # (1)
    [re.compile(r"^\[\d{0,3}\].+?[\,\.，．]?")],              # [10] Polygon
    [re.compile(r"^［\d{0,3}］.+?[\,\.，．]?")],      # ［1］
    [re.compile(r"^\d+[\,\.，．]")],                           # 1. Polygon
    [re.compile(r"^\d+[^\d\w]+?[\,\.，．]?")],                 # 1  Polygon
    [re.compile(r"^\[.+?\].+?[\,\.，．]?")],                   # [RCK+20]
    [re.compile(r"^\d+\s+")],                                          # 1 Polygon
    [                                                                    # Chinese author name
        re.compile(r"^[A-Z]\w.+?\(\d+[a-z]?\)"),
        re.compile(r"^[A-Z][A-Za-z]+[\,\.，．]?"),
        re.compile(r"^.+?,.+.,"),
        re.compile(r"^[一-龥]{1,4}[\,\.，．]?"),
    ],
]


def _abs(v: float) -> float:
    return v if v >= 0 else -v


def _deepcopy(obj):
    return copy.deepcopy(obj)


def get_ref_type(text: str) -> int:
    """
    Check if text starts like a reference entry.
    Returns the matching regex group index, or -1 if no match.
    Ported from pdf.ts L141-154 (getRefType).
    """
    for i, regex_group in enumerate(REF_REGEX):
        flags = set()
        for regex in regex_group:
            trimmed = text.strip()
            no_space = re.sub(r"\s+", "", text)
            flags.add(bool(regex.search(trimmed) or regex.search(no_space)))
        if True in flags:
            return i
    return -1


_NUMBER_REGEX = re.compile(r"\d+")


def get_ref_number(text: str) -> Optional[int]:
    """
    Extract the reference number from a reference entry prefix.
    Returns the integer number, or None for author-year styles.

    Examples:
        "[1] Zhou P..." -> 1
        "1. Zhou P..." -> 1
        "(12) Zhou..." -> 12
        "[RCK+20] ..." -> None
    """
    ref_type = get_ref_type(text)
    if ref_type == -1 or ref_type == 5:
        return None
    m = _NUMBER_REGEX.search(text.strip())
    if m:
        return int(m.group())
    return None


def merge_same_ref(ref_lines: list[PDFLine]) -> list[PDFLine]:
    """
    Merge multi-line entries into complete reference items.
    Ported from pdf.ts L161-224 (mergeSameRef).
    """
    _ref_lines = copy.deepcopy(ref_lines)
    ref_lines_copy = copy.deepcopy(ref_lines)

    first_line = ref_lines_copy[0]
    first_x = first_line.x

    # Detect first-line indent
    second_line = next(
        (
            line
            for line in ref_lines_copy[1:]
            if line.x != first_x and _abs(line.x - first_x) < 10 * first_line.height
        ),
        None,
    )
    indent = first_x - second_line.x if second_line else 0
    ref_type = get_ref_type(first_line.text)

    ref: Optional[PDFLine] = None

    for i, line in enumerate(ref_lines_copy):
        text = line.text
        line_ref_type = get_ref_type(text)

        is_new_ref = False
        if (
            (line_ref_type == ref_type and ref_type <= 2)
            or (
                indent == 0
                and line_ref_type != -1
                and line_ref_type == ref_type
                and _abs(first_x - line.x) < (_abs(indent) or line.height) * 0.5
            )
            or (
                indent != 0
                and line_ref_type == ref_type
                and any(
                    line != _line
                    and (line.x - _line.x) * indent > 0
                    and _abs(line.x - _line.x) >= _abs(indent)
                    and _abs(_abs(line.x - _line.x) - _abs(indent)) < 2 * line.height
                    for _line in _ref_lines
                )
            )
        ):
            is_new_ref = True

        if is_new_ref:
            ref = line
        elif ref is not None:
            # Noise removal near the end
            if (
                ref
                and i / len(ref_lines_copy) > 0.9
                and _abs(_abs(ref.x - line.x) - _abs(indent)) > 5 * line.height
            ):
                ref_lines_copy = ref_lines_copy[:i]
                break
            # Append continuation line, handle hyphen break
            ref.text = (
                ref.text.rstrip("-")
                + ("" if ref.text.endswith("-") else " ")
                + text
            )
            if line.url:
                ref.url = line.url
            ref_lines_copy[i] = False  # type: ignore

    return [e for e in ref_lines_copy if e is not False]


def get_ref_lines(
    doc: fitz.Document,
    from_current_page: bool = False,
    start_page: int = 0,
    full_text: bool = False,
    verbose: bool = False,
) -> list[PDFLine]:
    """
    Read PDF pages back-to-front to locate and extract reference lines.
    This is the core 320-line function ported from pdf.ts L286-607 (getRefLines).
    """
    total_pages = len(doc)
    if total_pages < 3:
        if verbose:
            print(f"PDF has only {total_pages} pages, skipping")
        return []

    # Pre-load config
    min_preload = 3
    offset = 0
    if from_current_page and start_page > 0:
        offset = total_pages - start_page

    effective_total = total_pages - offset
    preload_count = min(min_preload, effective_total)

    # Phase 1: pre-load last N pages
    page_lines: dict[int, list[PDFLine]] = {}
    max_width = 0.0
    max_height = 0.0

    for page_idx in range(total_pages - 1, total_pages - 1 - preload_count, -1):
        if page_idx < 0:
            break
        page = doc[page_idx]
        rect = page.rect
        max_width = max(max_width, rect.width)
        max_height = max(max_height, rect.height)
        lines = read_pdf_page(page)
        if lines:
            page_lines[page_idx] = lines
        if verbose:
            pct = (total_pages - page_idx) / preload_count * 100
            print(f"\r[{total_pages - page_idx}/{preload_count}] Pre-loading text... {pct:.0f}%", end="")

    if verbose:
        print()

    # Phase 2: scan pages backward to find reference section
    parts: list[list[PDFLine]] = []
    part: list[PDFLine] = []
    ref_part: list[PDFLine] = []
    _ref_part: dict = {"done": False, "parts": []}

    _stop_page = -1 if full_text else 0
    for page_idx in range(total_pages - 1, _stop_page, -1):
        page = doc[page_idx]
        max_width = max(max_width, page.rect.width)
        max_height = max(max_height, page.rect.height)

        # Get lines (from cache or read fresh)
        if page_idx in page_lines:
            lines = copy.deepcopy(page_lines[page_idx])
        else:
            lines = read_pdf_page(page)
            page_lines[page_idx] = copy.deepcopy(lines)

        if not lines:
            continue

        # Remove headers/footers: detect duplicate content across pages
        def _remove_number(text: str) -> str:
            if re.match(r"^[A-Z]{1,3}$", text):
                return ""
            return re.sub(r"\d+", "", re.sub(r"\s+", "", text))

        def _is_same_position(a: PDFLine, b: PDFLine) -> bool:
            return (
                round(a.x) == round(b.x)
                and round(a.y) == round(b.y)
                and round(a.width) == round(b.width)
                and round(a.height) == round(b.height)
            )

        def _is_same_text(a: PDFLine, b: PDFLine) -> bool:
            return _remove_number(a.text) == _remove_number(b.text)

        for line in lines:
            # 100% body text protection zone
            if (
                line.x / max(max_width, 1) > 0.2
                and line.y / max(max_height, 1) > 0.2
                and (line.x + line.width) / max(max_width, 1) < 0.8
                and (line.y + line.height) / max(max_height, 1) < 0.8
            ) or line.same:
                continue

            for _page_idx in page_lines:
                if _page_idx == page_idx:
                    continue
                for _line in page_lines[_page_idx]:
                    if _is_same_text(line, _line) and _is_same_position(line, _line):
                        line.same = _line
                        break
                if line.same:
                    break

        lines = [e for e in lines if not e.same]
        if not lines:
            continue

        # Remove figure/table captions
        def _is_figure_or_table(text: str) -> bool:
            t = re.sub(r"\s+", "", text)
            return bool(re.match(r"^(Table|Fig|Figure).*\d", t, re.IGNORECASE))

        lines = [e for e in lines if not _is_figure_or_table(e.text)]

        # All-figure / all-table pages have no remaining text. Skip — there's
        # nothing to scan for ref-section boundaries on this page.
        if not lines:
            continue

        # Column detection
        if lines:
            columns: list[list[PDFLine]] = [[lines[0]]]
            for i in range(1, len(lines)):
                line = lines[i]
                column = columns[-1]
                # Note: y comparison flipped for PyMuPDF (y-down vs PDF.js y-up)
                if (
                    line.y < column[-1].y
                    or sum(1 for _line in column if line.x > _line.x + _line.width) == len(column)
                    or sum(1 for _line in column if line.x + line.width < _line.x) == len(column)
                ):
                    columns.append([line])
                else:
                    column.append(line)

            for col_idx, column in enumerate(columns):
                for line in column:
                    line.column = col_idx
                    line.page_num = page_idx

        # Build parts (text sections) by scanning lines bottom-to-top within page
        is_start = False

        def _done_part(p: list[PDFLine]) -> list[PDFLine]:
            p.reverse()
            # Normalize indent within same column on same page
            col_groups: list[list[PDFLine]] = [[p[0]]]
            for i in range(1, len(p)):
                ln = p[i]
                last = col_groups[-1][-1]
                if ln.column == last.column and ln.page_num == last.page_num:
                    col_groups[-1].append(ln)
                else:
                    col_groups.append([ln])

            for group in col_groups:
                xs = sorted(ln.x for ln in group)
                offset_val = xs[0]
                for ln in group:
                    ln._x = ln.x
                    ln._offset = offset_val
                    ln.x = round(ln.x - offset_val, 1)

            parts.append(p)
            return p

        def _is_ref_break(text: str) -> bool:
            if full_text:
                return False
            t = re.sub(r"\s+", "", text)
            return bool(re.search(r"(参考文献|reference|bibliography)", t, re.IGNORECASE)) and len(t) < 20

        def _done_ref_part(p: list[PDFLine]) -> None:
            _done_part(p)
            _ref_part["parts"].append(p)
            res = re.match(r"^\d+", p[0].text.strip())
            if res and res.group() != "1":
                _ref_part["done"] = False
            else:
                _ref_part["done"] = True

        # Find bottom-right-most lines
        def _end_line_predicate(line: PDFLine) -> bool:
            return all(
                _line == line
                or (_line.x + _line.width < line.x + line.width or _line.y < line.y)
                for _line in lines
            )

        end_lines = [ln for ln in lines if _end_line_predicate(ln)]

        def _height_overlap(hh1: list[float], hh2: list[float]) -> bool:
            return any(
                any(h1 - h2 < min(h1, h2) * 0.3 for h2 in hh2) for h1 in hh1
            )

        end_line = end_lines[-1] if end_lines else lines[-1]

        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]

            # Skip non-content at the end (figures, etc.)
            if (
                not is_start
                and (
                    line != end_line
                    or bool(re.search(r"(图|fig|Fig|Figure).*\d+", re.sub(r"\s+", "", line.text)))
                )
            ):
                if part and page_idx == total_pages - 1:
                    _done_part(part)
                    part = []
                continue
            else:
                is_start = True

            # Height change → new section
            if (
                part
                and not _height_overlap(part[-1]._height, line._height)
            ):
                _done_part(part)
                part = [line]
                continue

            # Reference section header detected
            if _is_ref_break(line.text):
                _done_ref_part(part)
                part = []
                break

            part.append(line)

            # Page-internal section break
            if (
                i > 0
                and (
                    not _height_overlap(line._height, lines[i - 1]._height)
                    or line.column < lines[i - 1].column
                    or (
                        line.page_num == lines[i - 1].page_num
                        and line.column == lines[i - 1].column
                        and _abs(line.y - lines[i - 1].y) > line.height * 3
                    )
                )
            ):
                if _is_ref_break(lines[i - 1].text):
                    _done_ref_part(part)
                    part = []
                    break
                _done_part(part)
                part = []

        # Check if reference section is complete
        if _ref_part["done"]:
            for p in reversed(_ref_part["parts"]):
                ref_part.extend(p)
            break

    # Fallback: if no explicit reference section found, pick the part with most ref-like lines
    if not ref_part:
        part_ref_scores: list[tuple[int, int]] = []
        for idx, p in enumerate(parts):
            score = sum(1 for ln in p if get_ref_type(ln.text) != -1)
            part_ref_scores.append((idx, score))
        if part_ref_scores:
            best_idx = sorted(part_ref_scores, key=lambda x: x[1], reverse=True)[0][0]
            ref_part = parts[best_idx]

    if full_text:
        return parts  # type: ignore[return-value]
    return ref_part


def extract_references(
    pdf_path: str,
    from_current_page: bool = False,
    start_page: int = 0,
    verbose: bool = False,
) -> list[ItemInfo]:
    """
    Main entry point: extract references from a PDF file.
    Equivalent to pdf.ts getReferences() (L25-71).
    """
    doc = fitz.open(pdf_path)

    ref_lines = get_ref_lines(doc, from_current_page, start_page, verbose=verbose)

    if not ref_lines:
        if verbose:
            print("get_ref_lines: 0 ref_lines found")
        doc.close()
        return []

    # Merge continuation lines into complete references
    references = merge_same_ref(ref_lines)

    if verbose:
        print(f"Merged into {len(references)} references")

    # Clean each reference and extract structured info
    result: list[ItemInfo] = []
    for i, ref in enumerate(references):
        # Clone and clean text
        text = ref.text.strip()
        # Remove number prefixes like [1], 1., (1)
        text = re.sub(r"^[^0-9a-zA-Z]\s*\d+\s*[^0-9a-zA-Z]", "", text)
        text = re.sub(r"^\d+[\.\s]?", "", text)
        text = text.strip()

        info_dict = ref_text_to_info(text)
        info = ItemInfo(
            text=text,
            identifiers=info_dict.get("identifiers", {}),
            title=info_dict.get("title", ""),
            authors=info_dict.get("authors", []),
            year=info_dict.get("year"),
            url=ref.url or info_dict.get("url"),
            type=info_dict.get("type", "journalArticle"),
            x=float(ref._x or ref.x),
            y=float(ref.y + ref.height),
        )
        result.append(info)

    doc.close()
    return result
