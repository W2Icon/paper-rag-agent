"""
PDFReferenceExtractor — unified API for extracting references from academic PDFs.

Usage:
    from extractor import PDFReferenceExtractor
    extractor = PDFReferenceExtractor()
    result = extractor.extract("paper.pdf")
    print(result.references)
"""

import json
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union

import fitz

from reference_parser import get_ref_lines, get_ref_number, merge_same_ref
from citation_finder import find_citations, CitationLocation, _extract_first_author_lastname
from utils import ref_text_to_info


# ── Public result types ──────────────────────────────────────────


@dataclass
class Reference:
    """A single extracted reference entry."""
    text: str                          # cleaned reference text
    raw_text: str                      # original text before prefix stripping
    title: str
    authors: list[str]
    year: Optional[str]
    identifiers: dict[str, str]        # e.g. {"DOI": "10.xxx/yyy"}
    url: Optional[str]
    type: str                          # "journalArticle" | "preprint"
    position: dict[str, float]         # {"x": ..., "y": ...} in PDF coordinates
    ref_number: Optional[int] = None   # e.g. 1 for "[1] Zhou P..."
    citations: list[CitationLocation] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["citations"] = [c.to_dict() for c in self.citations]
        return d


@dataclass
class ExtractResult:
    """Result returned by PDFReferenceExtractor.extract()."""
    references: list[Reference] = field(default_factory=list)
    total_pages: int = 0
    source: str = "pdf"                # "pdf" | "fallback" (always "pdf" for now)

    def to_json(self, pretty: bool = True) -> str:
        return json.dumps(
            {"references": [r.to_dict() for r in self.references], "total_pages": self.total_pages, "source": self.source},
            ensure_ascii=False,
            indent=2 if pretty else None,
        )

    def __repr__(self) -> str:
        return f"ExtractResult(references={len(self.references)}, total_pages={self.total_pages}, source={self.source})"


# ── Configuration ────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "min_preload_pages": 3,
    "ref_section_keywords": ["references", "bibliography", "参考文献"],
}

# ── Main extractor ───────────────────────────────────────────────


class PDFReferenceExtractor:
    """
    Extract reference lists from academic PDF papers.

    Accepts PDF input as a file path (str/Path) or raw bytes.
    Returns a structured ExtractResult with typed Reference objects.

    Example:
        >>> extractor = PDFReferenceExtractor()
        >>> result = extractor.extract("paper.pdf")
        >>> for ref in result.references:
        ...     print(ref.title, ref.year, ref.authors)
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    # ── Public API ────────────────────────────────────────────

    def extract(
        self,
        source: Union[str, Path, bytes],
        *,
        full_text: bool = False,
        from_page: int = 0,
        verbose: bool = False,
    ) -> ExtractResult:
        """
        Extract references from a PDF.

        Args:
            source: PDF file path (str/Path) or raw PDF bytes.
            full_text: If True, also return full text sections (not just references).
            from_page: 1-indexed page number to start scanning from (0 = auto-detect from end).
            verbose: Print progress to stderr.

        Returns:
            ExtractResult with list of Reference objects and metadata.
        """
        pdf_bytes = self._to_bytes(source)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            doc = fitz.open(tmp.name)

        try:
            return self._extract_from_doc(doc, full_text=full_text, from_page=from_page, verbose=verbose)
        finally:
            doc.close()

    # ── Internals ──────────────────────────────────────────────

    def _to_bytes(self, source: Union[str, Path, bytes]) -> bytes:
        if isinstance(source, (str, Path)):
            return Path(source).read_bytes()
        if isinstance(source, bytes):
            return source
        raise TypeError(f"source must be str, Path, or bytes, got {type(source).__name__}")

    def _extract_from_doc(
        self,
        doc: fitz.Document,
        *,
        full_text: bool,
        from_page: int,
        verbose: bool,
    ) -> ExtractResult:
        total_pages = len(doc)
        start_page = max(0, from_page - 1) if from_page > 0 else 0

        ref_lines = get_ref_lines(
            doc,
            from_current_page=from_page > 0,
            start_page=start_page,
            full_text=full_text,
            verbose=verbose,
        )

        if not ref_lines:
            return ExtractResult(total_pages=total_pages)

        references = merge_same_ref(ref_lines)

        if verbose:
            print(f"[PDFReferenceExtractor] Merged {len(references)} reference entries")

        # Determine body page range (pages before the first reference page)
        ref_page_nums = [r.page_num for r in references if hasattr(r, "page_num")]
        first_ref_page = min(ref_page_nums) if ref_page_nums else total_pages
        body_pages = range(0, first_ref_page)

        # Pre-parse reference metadata for author-year citation matching
        refs_info: list[dict] = []
        for ref_obj in references:
            import re as _re
            raw = ref_obj.text.strip()
            ref_num = get_ref_number(raw)
            if ref_num is None:
                continue
            # Strip number prefix for parsing
            clean = _re.sub(r"^[^0-9a-zA-Z]\s*\d+\s*[^0-9a-zA-Z]", "", raw)
            clean = _re.sub(r"^\d+[\.\s]?", "", clean)
            clean = clean.strip()
            info = ref_text_to_info(clean)
            first_author = _extract_first_author_lastname(info.get("authors", []))
            year = info.get("year")
            if first_author and year:
                refs_info.append({
                    "ref_number": ref_num,
                    "first_author": first_author,
                    "year": year,
                    "authors": info.get("authors", []),
                })

        # Find all in-text citations on body pages
        all_citations: dict[int, list[CitationLocation]] = {}
        if body_pages:
            if verbose:
                print(f"[PDFReferenceExtractor] Scanning pages 1-{first_ref_page} for citations")
            all_citations = find_citations(doc, body_pages, refs_info if refs_info else None)

        result_refs: list[Reference] = []
        for ref_obj in references:
            text = ref_obj.text.strip()
            raw_text = text
            ref_number = get_ref_number(raw_text)

            # Strip number prefixes: [1], 1., (1), ［1］
            import re
            text = re.sub(r"^[^0-9a-zA-Z]\s*\d+\s*[^0-9a-zA-Z]", "", text)
            text = re.sub(r"^\d+[\.\s]?", "", text)
            text = text.strip()

            info_dict = ref_text_to_info(text)

            ref_citations = all_citations.get(ref_number, []) if ref_number is not None else []
            reference = Reference(
                text=text,
                raw_text=raw_text,
                title=info_dict.get("title", ""),
                authors=info_dict.get("authors", []),
                year=info_dict.get("year"),
                identifiers=info_dict.get("identifiers", {}),
                url=ref_obj.url or info_dict.get("url"),
                type=info_dict.get("type", "journalArticle"),
                position={"x": float(ref_obj._x or ref_obj.x), "y": float(ref_obj.y + ref_obj.height)},
                ref_number=ref_number,
                citations=ref_citations,
            )
            result_refs.append(reference)

        if verbose:
            cited = sum(1 for r in result_refs if r.citations)
            total_cites = sum(len(r.citations) for r in result_refs)
            print(f"[PDFReferenceExtractor] Found {total_cites} citation occurrences for {cited} references")

        return ExtractResult(
            references=result_refs,
            total_pages=total_pages,
            source="pdf",
        )
