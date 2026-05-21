"""
MinerU-backed paper extractor.

Wraps the `mineru` CLI (https://github.com/opendatalab/MinerU) as an optional
parsing backend. MinerU runs layout-aware deep-learning models (DocLayout-YOLO
for region detection, UniMERNet for formulas, RapidTable for tables) and
falls back to OCR for image-only PDFs — solving every shortcoming of the
PyMuPDF + heuristics backend in `paper_extractor.py`.

We invoke MinerU as a subprocess and consume its `*_content_list.json`
output (the structured, reading-order-sorted list of {type, text/html/latex,
page_idx, bbox} blocks). That JSON is then mapped onto our existing
`PaperMeta` / `Section` / `Reference` data classes so the rest of the
pipeline (DB write, embedding, agents) is unchanged.

Invocation:
    extractor = MinerUExtractor()
    result = extractor.extract("paper.pdf")  # → PaperExtractResult

Requires the `mineru` binary on PATH; install with:
    pip install -U "mineru[core]"     # CPU-only, no VLM
    pip install -U "mineru[all]"      # full stack
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import fitz

from citation_finder import find_citations, _extract_first_author_lastname
from extractor import Reference
from metadata_extractor import PaperMeta, extract_metadata
from paper_extractor import PaperExtractResult
from section_parser import Section
from utils import ref_text_to_info


# ── Public errors ───────────────────────────────────────────────────


class MinerUNotInstalled(RuntimeError):
    """`mineru` CLI is missing from PATH. Suggest install command."""


class MinerURunFailed(RuntimeError):
    """The `mineru` subprocess exited non-zero or produced no output."""


# ── Defaults ────────────────────────────────────────────────────────


_DEFAULT_BACKEND = "pipeline"       # CPU-friendly; "hybrid-auto-engine" for GPU
_DEFAULT_PARSE_METHOD = "auto"      # auto-decide text vs OCR
_DEFAULT_LANG = "ch"                # MinerU's OCR language hint (works for en too)
_DEFAULT_TIMEOUT_SEC = 600          # 10 min per PDF — generous for OCR


# ── The extractor ───────────────────────────────────────────────────


@dataclass
class MinerUConfig:
    """Tunable knobs for a MinerU run. Each maps directly to a `mineru` CLI flag."""
    backend: str = _DEFAULT_BACKEND
    parse_method: str = _DEFAULT_PARSE_METHOD
    language: str = _DEFAULT_LANG
    formula_enable: bool = True
    table_enable: bool = True
    cli_path: Optional[str] = None   # override `mineru` binary location
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC
    # If set, MinerU writes outputs here and we DON'T clean it up. Useful for
    # caching parses or letting users inspect intermediate JSON. None → tempdir.
    work_dir: Optional[Path] = None
    extra_args: list[str] = None     # forwarded verbatim to `mineru`

    def __post_init__(self):
        if self.extra_args is None:
            self.extra_args = []


class MinerUExtractor:
    """Layout-aware PDF extractor backed by MinerU."""

    def __init__(self, config: Optional[MinerUConfig] = None):
        self.config = config or MinerUConfig()

    def extract(
        self,
        source: Union[str, Path, bytes],
        *,
        from_page: int = 0,
        verbose: bool = False,
    ) -> PaperExtractResult:
        pdf_path = self._materialize_source(source)
        try:
            return self._extract_pdf(pdf_path, from_page=from_page, verbose=verbose)
        finally:
            if isinstance(source, bytes):
                pdf_path.unlink(missing_ok=True)

    # ── Source coercion ──────────────────────────────────────────

    def _materialize_source(self, source: Union[str, Path, bytes]) -> Path:
        if isinstance(source, (str, Path)):
            p = Path(source).expanduser().resolve()
            if not p.is_file():
                raise FileNotFoundError(f"PDF not found: {p}")
            return p
        if isinstance(source, bytes):
            # MinerU only consumes files, so write to a temp PDF.
            fd, name = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            tmp = Path(name)
            tmp.write_bytes(source)
            return tmp
        raise TypeError(f"source must be str/Path/bytes, got {type(source).__name__}")

    # ── Main pipeline ────────────────────────────────────────────

    def _extract_pdf(self, pdf_path: Path, *, from_page: int, verbose: bool) -> PaperExtractResult:
        # MinerU writes outputs under <out>/<pdf_stem>/<backend>/, so use a
        # per-call subdir to avoid collisions on concurrent runs.
        work_dir = self.config.work_dir or Path(tempfile.mkdtemp(prefix="mineru_"))
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = self.config.work_dir is None

        try:
            self._run_mineru(pdf_path, work_dir, verbose=verbose)
            content_list_path, _md_path = self._locate_outputs(work_dir, pdf_path)
            blocks = self._load_content_list(content_list_path)

            # Build sections + extract reference block strings.
            sections, ref_block_texts = self._blocks_to_sections(blocks)
            references = self._refs_from_blocks(ref_block_texts)

            # Citation locations still come from a PyMuPDF body scan — MinerU
            # gives clean text but no per-citation pixel coords. The scan is
            # cheap (text-only) and only runs once we know how many refs to
            # match.
            doc = fitz.open(str(pdf_path))
            try:
                total_pages = len(doc)
                meta = self._extract_meta_with_fallback(doc, blocks)
                self._attach_citations(doc, references, from_page=from_page)
            finally:
                doc.close()

            markdown = self._read_markdown(_md_path) if _md_path else None

            return PaperExtractResult(
                meta=meta,
                sections=sections,
                references=references,
                total_pages=total_pages,
                parse_backend="mineru",
                markdown=markdown,
            )
        finally:
            if cleanup:
                shutil.rmtree(work_dir, ignore_errors=True)

    # ── MinerU invocation ────────────────────────────────────────

    def _run_mineru(self, pdf_path: Path, out_dir: Path, *, verbose: bool) -> None:
        cli = self.config.cli_path or "mineru"
        if shutil.which(cli) is None:
            raise MinerUNotInstalled(
                f"`{cli}` not found on PATH. Install with:\n"
                f"  pip install -U 'mineru[core]'    # CPU only\n"
                f"  pip install -U 'mineru[all]'     # full stack incl. VLM\n"
                f"Or set MinerUConfig(cli_path=...)."
            )
        cmd = [
            cli,
            "-p", str(pdf_path),
            "-o", str(out_dir),
            "-b", self.config.backend,
            "-m", self.config.parse_method,
            "-l", self.config.language,
            "-f", "true" if self.config.formula_enable else "false",
            "-t", "true" if self.config.table_enable else "false",
        ]
        cmd.extend(self.config.extra_args)

        if verbose:
            print(f"[MinerU] $ {' '.join(cmd)}", file=sys.stderr)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise MinerURunFailed(
                f"mineru timed out after {self.config.timeout_sec}s on {pdf_path.name}"
            ) from e

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise MinerURunFailed(
                f"mineru exited {proc.returncode} on {pdf_path.name}\n--- stderr tail ---\n{tail}"
            )
        if verbose and proc.stdout:
            print(proc.stdout[-500:], file=sys.stderr)

    def _locate_outputs(self, out_dir: Path, pdf_path: Path) -> tuple[Path, Optional[Path]]:
        """MinerU writes:
            <out>/<stem>/<parse_method-or-backend>/<stem>_content_list.json
            <out>/<stem>/<parse_method-or-backend>/<stem>.md
        Path layout varies slightly across MinerU versions, so glob for it.
        """
        stem = pdf_path.stem
        candidates = list(out_dir.rglob(f"{stem}_content_list.json"))
        if not candidates:
            # Some versions name it just `content_list.json`.
            candidates = list(out_dir.rglob("content_list.json"))
        if not candidates:
            tree = "\n".join(str(p.relative_to(out_dir)) for p in out_dir.rglob("*"))
            raise MinerURunFailed(
                f"mineru produced no content_list.json under {out_dir}\n--- tree ---\n{tree[:2000]}"
            )
        content_list = candidates[0]

        md_candidates = list(content_list.parent.glob(f"{stem}.md"))
        if not md_candidates:
            md_candidates = list(content_list.parent.glob("*.md"))
        md_path = md_candidates[0] if md_candidates else None

        return content_list, md_path

    @staticmethod
    def _load_content_list(path: Path) -> list[dict]:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise MinerURunFailed(f"content_list.json must be a JSON array, got {type(data)}")
        return data

    @staticmethod
    def _read_markdown(path: Optional[Path]) -> Optional[str]:
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    # ── content_list → Sections ──────────────────────────────────

    # MinerU block schema (per current docs):
    #   {"type": "text" | "title" | "image" | "table" | "equation" | "interline_equation",
    #    "text": "...",                  # for text/title
    #    "text_level": 1|2|3,            # for title
    #    "img_path": "images/abc.jpg",   # for image
    #    "img_caption": ["..."],
    #    "img_footnote": ["..."],
    #    "table_body": "<table>...</table>",
    #    "table_caption": ["..."],
    #    "table_footnote": ["..."],
    #    "text_format": "latex",         # for equation
    #    "page_idx": 0 }

    def _blocks_to_sections(self, blocks: list[dict]) -> tuple[list[Section], list[str]]:
        sections: list[Section] = []
        current: Optional[Section] = None
        paragraph_buf: list[str] = []
        ref_block_texts: list[str] = []
        in_references = False

        def _flush_buf():
            nonlocal paragraph_buf
            if current is not None and paragraph_buf:
                text = " ".join(s.strip() for s in paragraph_buf if s.strip()).strip()
                if text:
                    current.paragraphs.append(text)
            paragraph_buf = []

        for blk in blocks:
            btype = blk.get("type", "")
            page = int(blk.get("page_idx", 0)) + 1  # MinerU is 0-indexed

            if btype == "title":
                text = (blk.get("text") or "").strip()
                if not text:
                    continue
                _flush_buf()
                if current is not None:
                    sections.append(current)
                current = Section(
                    heading=text,
                    level=int(blk.get("text_level") or 1),
                    paragraphs=[],
                    page_start=page,
                    page_end=page,
                )
                in_references = _is_references_heading(text)
                continue

            # Make sure each non-title block is attributed to *some* section so
            # nothing silently disappears.
            if current is None:
                current = Section(
                    heading="(frontmatter)",
                    level=1,
                    paragraphs=[],
                    page_start=page,
                    page_end=page,
                )

            current.page_end = max(current.page_end, page)

            if btype == "text":
                text = (blk.get("text") or "").strip()
                if not text:
                    continue
                # Inside the references section, MinerU emits each ref as one
                # text block. Pluck them out separately for the ref pipeline
                # and DON'T add them to the section paragraphs (they're noisy
                # for body text and lit-review embedding).
                if in_references:
                    ref_block_texts.append(text)
                    continue
                paragraph_buf.append(text)

            elif btype == "table":
                _flush_buf()
                html = (blk.get("table_body") or "").strip()
                caption_parts = blk.get("table_caption") or []
                caption = " ".join(c.strip() for c in caption_parts if c).strip()
                current.tables.append({
                    "html": html,
                    "markdown": _html_table_to_markdown(html),
                    "caption": caption,
                    "page": page,
                    "bbox": blk.get("bbox"),
                })

            elif btype in ("equation", "interline_equation"):
                latex = (blk.get("text") or "").strip()
                if not latex:
                    continue
                # MinerU sometimes wraps in $$...$$ — keep as-is, downstream
                # consumers can strip.
                current.formulas.append({
                    "latex": latex,
                    "type": "block",
                    "page": page,
                })

            elif btype == "image":
                _flush_buf()
                caption_parts = blk.get("img_caption") or []
                caption = " ".join(c.strip() for c in caption_parts if c).strip()
                current.images.append({
                    "path": (blk.get("img_path") or "").strip(),
                    "caption": caption,
                    "page": page,
                })

            else:
                # Unknown block type — keep its text if any, ignore otherwise.
                text = (blk.get("text") or "").strip()
                if text:
                    paragraph_buf.append(text)

        _flush_buf()
        if current is not None:
            sections.append(current)

        return sections, ref_block_texts

    # ── References ───────────────────────────────────────────────

    def _refs_from_blocks(self, ref_block_texts: list[str]) -> list[Reference]:
        """Convert MinerU reference text blocks into Reference objects.

        MinerU already segments references one-per-block, so we skip the
        line-merging step from reference_parser and go straight to per-entry
        parsing via the existing `ref_text_to_info` helper.
        """
        refs: list[Reference] = []
        for raw in ref_block_texts:
            raw = raw.strip()
            if len(raw) < 10:  # too short to be a real ref
                continue

            ref_number, body = _split_ref_prefix(raw)
            info = ref_text_to_info(body)

            refs.append(Reference(
                text=body,
                raw_text=raw,
                title=info.get("title", ""),
                authors=info.get("authors", []),
                year=info.get("year"),
                identifiers=info.get("identifiers", {}),
                url=info.get("url"),
                type=info.get("type", "journalArticle"),
                position={"x": 0.0, "y": 0.0},  # MinerU doesn't preserve bbox here
                ref_number=ref_number,
                citations=[],
            ))
        return refs

    def _attach_citations(
        self,
        doc: fitz.Document,
        references: list[Reference],
        *,
        from_page: int,
    ) -> None:
        """Run the existing citation finder over body pages and attach
        CitationLocation lists to each Reference by ref_number."""
        if not references:
            return

        refs_info: list[dict] = []
        for r in references:
            if r.ref_number is None:
                continue
            first_author = _extract_first_author_lastname(r.authors)
            if first_author and r.year:
                refs_info.append({
                    "ref_number": r.ref_number,
                    "first_author": first_author,
                    "year": r.year,
                    "authors": r.authors,
                })

        # Heuristic body-page range: the bibliography typically starts after
        # 60–80% of the document. Without bbox info from MinerU we fall back
        # to scanning the entire document; the finder filters out matches that
        # appear in the references section anyway.
        if from_page > 0:
            start = max(0, from_page - 1)
        else:
            start = 0
        body_pages = range(start, len(doc))

        citations_by_num = find_citations(
            doc, body_pages, refs_info if refs_info else None
        )
        by_num = {r.ref_number: r for r in references if r.ref_number is not None}
        for num, cits in citations_by_num.items():
            r = by_num.get(num)
            if r is not None:
                r.citations.extend(cits)

    # ── Metadata ────────────────────────────────────────────────

    def _extract_meta_with_fallback(
        self, doc: fitz.Document, blocks: list[dict]
    ) -> PaperMeta:
        """Try MinerU's first-page blocks first; fall back to the existing
        PyMuPDF heuristic extractor for anything missing."""
        title = _first_title_from_blocks(blocks)
        meta = extract_metadata(doc)  # heuristic baseline
        if title and (not meta.title or len(title) > len(meta.title)):
            # MinerU's title is usually cleaner — it skips the journal masthead.
            meta.title = title
        return meta


# ── Helpers ────────────────────────────────────────────────────────


_REFERENCES_HEADING_RE = re.compile(
    r"^\s*(?:\d+\.?\s+)?(?:references|bibliography|参考文献|reference\s+list)\b",
    re.IGNORECASE,
)


def _is_references_heading(text: str) -> bool:
    norm = text.strip().lower().rstrip(".:").strip()
    if norm in {"references", "bibliography", "参考文献", "reference list"}:
        return True
    return bool(_REFERENCES_HEADING_RE.match(text.strip()))


_REF_PREFIX_PATTERNS = [
    # [1] / [12]
    re.compile(r"^\[(\d+)\]\s*"),
    # 1. / 12.
    re.compile(r"^(\d+)\.\s+"),
    # (1) / (12)
    re.compile(r"^\((\d+)\)\s*"),
    # ［1］ fullwidth
    re.compile(r"^［(\d+)］\s*"),
]


def _split_ref_prefix(text: str) -> tuple[Optional[int], str]:
    """Pull a numeric ref prefix off the front of a reference string."""
    for pat in _REF_PREFIX_PATTERNS:
        m = pat.match(text)
        if m:
            try:
                return int(m.group(1)), text[m.end():].strip()
            except ValueError:
                continue
    return None, text


def _first_title_from_blocks(blocks: list[dict]) -> str:
    """The first top-level title block from page 1 is almost always the paper title."""
    for blk in blocks:
        if blk.get("page_idx", 0) > 0:
            break
        if blk.get("type") == "title" and (blk.get("text_level") in (None, 1)):
            t = (blk.get("text") or "").strip()
            if len(t) >= 8 and not _is_references_heading(t):
                return t
    return ""


_TABLE_TAG_RE = re.compile(r"<(/?)(table|thead|tbody|tr|td|th)([^>]*)>", re.IGNORECASE)


def _html_table_to_markdown(html: str) -> str:
    """Best-effort HTML → Markdown table conversion. Good enough for LLM
    consumption — we don't aim to round-trip complex tables losslessly."""
    if not html:
        return ""
    # Very small parser: walk <tr>...</tr> rows, split into <td>/<th> cells.
    rows: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
        cleaned = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip().replace("|", "\\|")
            for c in cells
        ]
        if any(cleaned):
            rows.append(cleaned)

    if not rows:
        # Strip tags as last resort.
        return re.sub(r"<[^>]+>", "", html).strip()

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    header = rows[0]
    sep = ["---"] * width
    body = rows[1:] if len(rows) > 1 else []
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def is_mineru_available(cli_path: Optional[str] = None) -> bool:
    """Cheap probe so callers can fall back gracefully when mineru isn't installed."""
    return shutil.which(cli_path or "mineru") is not None
