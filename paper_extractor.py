"""
Paper Extractor — unified API for full paper extraction.

Extracts metadata, structured sections with paragraphs, references,
and citation locations from academic PDFs.
"""

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import fitz

from metadata_extractor import extract_metadata, PaperMeta
from section_parser import parse_sections, Section
from reference_parser import get_ref_lines, get_ref_number, merge_same_ref
from citation_finder import find_citations, _extract_first_author_lastname
from utils import ref_text_to_info


@dataclass
class PaperExtractResult:
    """Complete paper extraction result."""
    meta: PaperMeta
    sections: list[Section]
    references: list   # list[Reference] from extractor
    total_pages: int
    # Which backend produced this result. "pymupdf" (default, heuristic) or
    # "mineru" (layout-aware DL model). Useful for downstream debugging and
    # for converters to know whether tables/formulas/images are populated.
    parse_backend: str = "pymupdf"
    # Full markdown rendering (only populated by MinerU backend). Optional
    # because the PyMuPDF path does not generate markdown.
    markdown: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "meta": self.meta.to_dict(),
            "sections": [s.to_dict() for s in self.sections],
            "references": [r.to_dict() for r in self.references],
            "total_pages": self.total_pages,
            "parse_backend": self.parse_backend,
            "markdown": self.markdown,
        }

    def to_json(self, pretty: bool = True) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2 if pretty else None,
        )

    def __repr__(self) -> str:
        return (
            f"PaperExtractResult(meta.title={self.meta.title[:50]}..., "
            f"sections={len(self.sections)}, "
            f"references={len(self.references)}, "
            f"total_pages={self.total_pages})"
        )


class PaperExtractor:
    """
    Extract full paper content: metadata, sections, paragraphs, references, citations.

    Backends:
        "pymupdf"  Fast, no models, heuristic. Default.
        "mineru"   Layout-aware DL (DocLayout-YOLO + UniMERNet + RapidTable + OCR).
                   Required for scanned PDFs; recovers tables/formulas/images.
                   Needs the `mineru` CLI installed (`pip install -U 'mineru[core]'`).
        "auto"     Sniff the PDF; switch to MinerU when PyMuPDF cannot recover
                   enough selectable text (i.e. scanned/image-only PDFs) and
                   MinerU is available, otherwise stay on PyMuPDF.

    Example:
        >>> extractor = PaperExtractor(backend="auto")
        >>> result = extractor.extract("paper.pdf")
        >>> print(result.parse_backend, result.meta.title)
        >>> for section in result.sections:
        ...     print(section.heading, len(section.paragraphs),
        ...           "tables=", len(section.tables),
        ...           "formulas=", len(section.formulas))
    """

    def __init__(
        self,
        config: Optional[dict] = None,
        *,
        backend: str = "pymupdf",
        mineru_config: Optional[dict] = None,
    ):
        self.config = {**(config or {})}
        if backend not in {"pymupdf", "mineru", "auto"}:
            raise ValueError(
                f"backend must be one of pymupdf|mineru|auto, got {backend!r}"
            )
        self.backend = backend
        self._mineru_config_kwargs = mineru_config or {}
        self._mineru = None  # lazy

    def extract(
        self,
        source: Union[str, Path, bytes],
        *,
        from_page: int = 0,
        verbose: bool = False,
    ) -> PaperExtractResult:
        """
        Extract full paper from a PDF.

        Args:
            source: PDF file path (str/Path) or raw PDF bytes.
            from_page: 1-indexed page to start reference scanning from (0 = auto).
            verbose: Print progress to stderr.

        Returns:
            PaperExtractResult with meta, sections, references, total_pages,
            and parse_backend ("pymupdf" | "mineru").
        """
        chosen = self._resolve_backend(source, verbose=verbose)

        if chosen == "mineru":
            if verbose:
                print("[PaperExtractor] backend=mineru", file=__import__("sys").stderr)
            try:
                return self._get_mineru_extractor().extract(
                    source, from_page=from_page, verbose=verbose,
                )
            except Exception as e:
                # Hard fall-back only if user said "auto" — explicit choices fail loudly.
                if self.backend != "auto":
                    raise
                if verbose:
                    print(
                        f"[PaperExtractor] MinerU failed ({e!r}), falling back to PyMuPDF",
                        file=__import__("sys").stderr,
                    )
                # fall through to pymupdf

        pdf_bytes = self._to_bytes(source)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            doc = fitz.open(tmp.name)

        try:
            result = self._extract_from_doc(doc, from_page=from_page, verbose=verbose)
            result.parse_backend = "pymupdf"
            return result
        finally:
            doc.close()

    # ── Backend selection ────────────────────────────────────────

    def _resolve_backend(self, source, *, verbose: bool) -> str:
        if self.backend in ("pymupdf", "mineru"):
            return self.backend

        # auto: sniff. Use PyMuPDF to estimate selectable-text density; if the
        # PDF is mostly images (likely scanned), prefer MinerU's OCR.
        from mineru_extractor import is_mineru_available

        if not is_mineru_available(self._mineru_config_kwargs.get("cli_path")):
            return "pymupdf"

        pdf_bytes = self._to_bytes(source)
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                tmp.write(pdf_bytes)
                tmp.flush()
                doc = fitz.open(tmp.name)
                try:
                    n_pages = len(doc)
                    sample = min(5, n_pages)  # 5 pages is more robust than 3
                    per_page = [
                        len(doc[i].get_text("text")) for i in range(sample)
                    ]
                finally:
                    doc.close()
        except Exception:
            return "pymupdf"

        if not per_page:
            return "pymupdf"

        avg = sum(per_page) / len(per_page)
        # % of sampled pages with almost no extractable text → direct "needs
        # OCR" proxy. Robust to mixed PDFs (cover page + scanned body) which
        # a mean-only signal misses.
        pct_empty = sum(1 for c in per_page if c < 100) / len(per_page) * 100

        # Composite signal — calibrated against an 11-PDF labelled corpus
        # (8 born-digital journal papers + 3 rasterised scans). Born-digital
        # papers showed mean ≥ 3566 chars/page in 5-page sampling, scanned
        # showed 0. Threshold of 500 is ~7× below the digital floor (safe
        # against figure-heavy pages) and 5× above the typical garbled-OCR
        # text-layer scan. The pct_empty arm catches mixed PDFs where the
        # cover-page char count alone pulls the mean above 500.
        # See scripts/calibrate_backend_threshold.py to re-run on your corpus.
        if avg < 500 or pct_empty > 50:
            if verbose:
                print(
                    f"[PaperExtractor] avg={avg:.0f} chars/page, "
                    f"{pct_empty:.0f}% pages < 100 chars → switching to MinerU",
                    file=__import__("sys").stderr,
                )
            return "mineru"
        return "pymupdf"

    def _get_mineru_extractor(self):
        if self._mineru is None:
            from mineru_extractor import MinerUExtractor, MinerUConfig
            cfg = MinerUConfig(**self._mineru_config_kwargs)
            self._mineru = MinerUExtractor(cfg)
        return self._mineru

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
        from_page: int,
        verbose: bool,
    ) -> PaperExtractResult:
        total_pages = len(doc)

        # ---- Metadata ----
        if verbose:
            print("[PaperExtractor] Extracting metadata...")
        meta = extract_metadata(doc)

        # ---- Sections ----
        if verbose:
            print("[PaperExtractor] Parsing sections...")
        parts = get_ref_lines(doc, full_text=True, verbose=False)
        sections = parse_sections(parts)

        # ---- References ----
        if verbose:
            print("[PaperExtractor] Extracting references...")
        start_page = max(0, from_page - 1) if from_page > 0 else 0
        ref_lines = get_ref_lines(
            doc,
            from_current_page=from_page > 0,
            start_page=start_page,
            full_text=False,
            verbose=False,
        )

        references = []
        all_citations: dict[int, list] = {}

        if ref_lines:
            merged_refs = merge_same_ref(ref_lines)
            if verbose:
                print(f"[PaperExtractor] Merged {len(merged_refs)} reference entries")

            # Body page range
            ref_page_nums = [r.page_num for r in merged_refs if hasattr(r, "page_num")]
            first_ref_page = min(ref_page_nums) if ref_page_nums else total_pages
            body_pages = range(0, first_ref_page)

            # Pre-parse for author-year matching
            refs_info: list[dict] = []
            for ref_obj in merged_refs:
                import re as _re
                raw = ref_obj.text.strip()
                ref_num = get_ref_number(raw)
                if ref_num is None:
                    continue
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

            # Find citations
            if body_pages:
                if verbose:
                    print(f"[PaperExtractor] Scanning pages 1-{first_ref_page} for citations")
                all_citations = find_citations(doc, body_pages, refs_info if refs_info else None)

            # Build Reference objects
            for ref_obj in merged_refs:
                import re as _re2
                text = ref_obj.text.strip()
                raw_text = text
                ref_number = get_ref_number(raw_text)

                text = _re2.sub(r"^[^0-9a-zA-Z]\s*\d+\s*[^0-9a-zA-Z]", "", text)
                text = _re2.sub(r"^\d+[\.\s]?", "", text)
                text = text.strip()

                info_dict = ref_text_to_info(text)
                ref_citations = all_citations.get(ref_number, []) if ref_number is not None else []

                from extractor import Reference
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
                references.append(reference)

            if verbose:
                cited = sum(1 for r in references if r.citations)
                total_cites = sum(len(r.citations) for r in references)
                print(f"[PaperExtractor] Found {total_cites} citation occurrences for {cited} references")

        return PaperExtractResult(
            meta=meta,
            sections=sections,
            references=references,
            total_pages=total_pages,
        )
