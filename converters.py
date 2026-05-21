import hashlib
from pathlib import Path

from paper_extractor import PaperExtractResult
from extractor import Reference
from citation_finder import CitationLocation
from metadata_extractor import PaperMeta
from section_parser import Section
from models import (
    PaperRecord, SectionRecord, ReferenceRecord, CitationLocationRecord,
)
from section_classifier import classify_section_type


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def paper_meta_to_record(
    meta: PaperMeta,
    pdf_path: Path,
    total_pages: int,
    pdf_sha256: str,
    file_size: int,
    parse_backend: str = "pymupdf",
) -> PaperRecord:
    return PaperRecord(
        title=meta.title,
        authors=meta.authors,
        affiliations=meta.affiliations,
        abstract=meta.abstract,
        keywords=meta.keywords,
        doi=meta.doi.strip() if meta.doi else None,
        total_pages=total_pages,
        pdf_path=str(pdf_path.resolve()),
        pdf_sha256=pdf_sha256,
        file_size_bytes=file_size,
        parse_backend=parse_backend,
    )


def sections_to_records(sections: list[Section], paper_id: int) -> list[SectionRecord]:
    records = []
    for idx, section in enumerate(sections):
        records.append(SectionRecord(
            paper_id=paper_id,
            heading=section.heading,
            level=section.level,
            body_text="\n\n".join(section.paragraphs),
            paragraphs=section.paragraphs,
            page_start=section.page_start,
            page_end=section.page_end,
            sort_order=idx,
            section_type=classify_section_type(section.heading),
            # Rich content from layout-aware backends (MinerU). PyMuPDF
            # backend leaves these as empty lists.
            tables=list(getattr(section, "tables", []) or []),
            formulas=list(getattr(section, "formulas", []) or []),
            images=list(getattr(section, "images", []) or []),
        ))
    return records


def reference_to_record(ref: Reference, paper_id: int) -> ReferenceRecord:
    return ReferenceRecord(
        paper_id=paper_id,
        ref_number=ref.ref_number,
        text=ref.text,
        raw_text=ref.raw_text,
        title=ref.title,
        authors=ref.authors,
        year=ref.year,
        identifiers=ref.identifiers,
        url=ref.url,
        type=ref.type,
        position_x=ref.position.get("x"),
        position_y=ref.position.get("y"),
    )


def citations_to_records(
    citations: list[CitationLocation],
    reference_id: int,
    paper_id: int,
) -> list[CitationLocationRecord]:
    return [
        CitationLocationRecord(
            reference_id=reference_id,
            paper_id=paper_id,
            page=c.page,
            x=c.x,
            y=c.y,
            text=c.text,
            sentence=c.sentence,
        )
        for c in citations
    ]


def convert_extract_result(
    result: PaperExtractResult,
    pdf_path: Path,
) -> tuple[
    PaperRecord,
    list[SectionRecord],
    list[tuple[ReferenceRecord, list[CitationLocationRecord]]],
]:
    sha256 = compute_sha256(pdf_path)
    file_size = pdf_path.stat().st_size

    paper = paper_meta_to_record(
        result.meta, pdf_path, result.total_pages, sha256, file_size,
        parse_backend=getattr(result, "parse_backend", "pymupdf"),
    )
    sections = sections_to_records(result.sections, paper_id=0)

    ref_groups = []
    for ref in result.references:
        ref_rec = reference_to_record(ref, paper_id=0)
        cit_recs = citations_to_records(ref.citations, reference_id=0, paper_id=0)
        ref_groups.append((ref_rec, cit_recs))

    return paper, sections, ref_groups
