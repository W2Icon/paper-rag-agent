import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PaperRecord:
    id: Optional[int] = None
    title: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    doi: Optional[str] = None
    total_pages: int = 0
    pdf_path: str = ""
    pdf_sha256: str = ""
    file_size_bytes: int = 0
    ingested_at: Optional[str] = None
    # Phase 2 LLM analysis fields
    llm_summary: Optional[str] = None
    llm_research_field: Optional[str] = None
    llm_methodology: Optional[str] = None
    llm_key_findings: Optional[list[str]] = None
    llm_analyzed_at: Optional[str] = None
    # v6: which backend produced this paper ("pymupdf" or "mineru")
    parse_backend: str = "pymupdf"
    # v7: actual publication year (LLM-extracted in analyze_paper). NULL until
    # analyzed. Used by retrieval year-range filtering — NOT the ingestion year.
    publication_year: Optional[int] = None

    def to_row(self) -> dict:
        return {
            "title": self.title,
            "authors": json.dumps(self.authors, ensure_ascii=False),
            "affiliations": json.dumps(self.affiliations, ensure_ascii=False),
            "abstract": self.abstract,
            "keywords": json.dumps(self.keywords, ensure_ascii=False),
            "doi": self.doi,
            "total_pages": self.total_pages,
            "pdf_path": self.pdf_path,
            "pdf_sha256": self.pdf_sha256,
            "file_size_bytes": self.file_size_bytes,
            "llm_summary": self.llm_summary,
            "llm_research_field": self.llm_research_field,
            "llm_methodology": self.llm_methodology,
            "llm_key_findings": json.dumps(self.llm_key_findings, ensure_ascii=False) if self.llm_key_findings else None,
            "llm_analyzed_at": self.llm_analyzed_at,
            "parse_backend": self.parse_backend,
            "publication_year": self.publication_year,
        }

    @classmethod
    def from_row(cls, row: dict) -> "PaperRecord":
        findings = row.get("llm_key_findings")
        return cls(
            id=row["id"],
            title=row["title"],
            authors=json.loads(row["authors"] or "[]"),
            affiliations=json.loads(row["affiliations"] or "[]"),
            abstract=row["abstract"] or "",
            keywords=json.loads(row["keywords"] or "[]"),
            doi=row.get("doi"),
            total_pages=row["total_pages"],
            pdf_path=row["pdf_path"],
            pdf_sha256=row["pdf_sha256"],
            file_size_bytes=row["file_size_bytes"],
            ingested_at=row.get("ingested_at"),
            llm_summary=row.get("llm_summary"),
            llm_research_field=row.get("llm_research_field"),
            llm_methodology=row.get("llm_methodology"),
            llm_key_findings=json.loads(findings) if findings else None,
            llm_analyzed_at=row.get("llm_analyzed_at"),
            parse_backend=row.get("parse_backend") or "pymupdf",
            publication_year=row.get("publication_year"),
        )


@dataclass
class SectionRecord:
    id: Optional[int] = None
    paper_id: int = 0
    heading: str = ""
    level: int = 1
    body_text: str = ""
    paragraphs: list[str] = field(default_factory=list)
    page_start: int = 0
    page_end: int = 0
    sort_order: int = 0
    section_type: Optional[str] = None
    # Rich content from layout-aware backends (MinerU). Persisted to
    # section_tables / section_formulas / section_images sibling tables, not
    # inline columns — these stay in-memory only on the dataclass for the
    # converter to fan out.
    tables: list[dict] = field(default_factory=list)
    formulas: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)

    def to_row(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "heading": self.heading,
            "level": self.level,
            "body_text": self.body_text,
            "paragraphs": json.dumps(self.paragraphs, ensure_ascii=False),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "sort_order": self.sort_order,
            "section_type": self.section_type,
        }

    @classmethod
    def from_row(cls, row: dict) -> "SectionRecord":
        return cls(
            id=row["id"],
            paper_id=row["paper_id"],
            heading=row["heading"],
            level=row["level"],
            body_text=row["body_text"],
            paragraphs=json.loads(row["paragraphs"] or "[]"),
            page_start=row["page_start"],
            page_end=row["page_end"],
            sort_order=row["sort_order"],
            section_type=row.get("section_type"),
        )


@dataclass
class SectionTableRecord:
    """A table belonging to a section (typically populated by MinerU)."""
    id: Optional[int] = None
    section_id: int = 0
    paper_id: int = 0
    html: str = ""
    markdown: str = ""
    caption: str = ""
    page: int = 0
    sort_order: int = 0

    def to_row(self) -> dict:
        return {
            "section_id": self.section_id,
            "paper_id": self.paper_id,
            "html": self.html,
            "markdown": self.markdown,
            "caption": self.caption,
            "page": self.page,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_row(cls, row: dict) -> "SectionTableRecord":
        return cls(
            id=row["id"],
            section_id=row["section_id"],
            paper_id=row["paper_id"],
            html=row["html"] or "",
            markdown=row["markdown"] or "",
            caption=row["caption"] or "",
            page=row["page"],
            sort_order=row["sort_order"],
        )


@dataclass
class SectionFormulaRecord:
    """A LaTeX formula belonging to a section (typically populated by MinerU)."""
    id: Optional[int] = None
    section_id: int = 0
    paper_id: int = 0
    latex: str = ""
    formula_type: str = "block"  # "inline" or "block"
    page: int = 0
    sort_order: int = 0

    def to_row(self) -> dict:
        return {
            "section_id": self.section_id,
            "paper_id": self.paper_id,
            "latex": self.latex,
            "formula_type": self.formula_type,
            "page": self.page,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_row(cls, row: dict) -> "SectionFormulaRecord":
        return cls(
            id=row["id"],
            section_id=row["section_id"],
            paper_id=row["paper_id"],
            latex=row["latex"] or "",
            formula_type=row["formula_type"] or "block",
            page=row["page"],
            sort_order=row["sort_order"],
        )


@dataclass
class SectionImageRecord:
    """An image/figure belonging to a section (typically populated by MinerU)."""
    id: Optional[int] = None
    section_id: int = 0
    paper_id: int = 0
    image_path: str = ""
    caption: str = ""
    page: int = 0
    sort_order: int = 0

    def to_row(self) -> dict:
        return {
            "section_id": self.section_id,
            "paper_id": self.paper_id,
            "image_path": self.image_path,
            "caption": self.caption,
            "page": self.page,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_row(cls, row: dict) -> "SectionImageRecord":
        return cls(
            id=row["id"],
            section_id=row["section_id"],
            paper_id=row["paper_id"],
            image_path=row["image_path"] or "",
            caption=row["caption"] or "",
            page=row["page"],
            sort_order=row["sort_order"],
        )


@dataclass
class SectionChunkRecord:
    id: Optional[int] = None
    section_id: int = 0
    paper_id: int = 0
    chunk_idx: int = 0
    text: str = ""
    char_start: int = 0
    char_end: int = 0

    def to_row(self) -> dict:
        return {
            "section_id": self.section_id,
            "paper_id": self.paper_id,
            "chunk_idx": self.chunk_idx,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }

    @classmethod
    def from_row(cls, row: dict) -> "SectionChunkRecord":
        return cls(
            id=row["id"],
            section_id=row["section_id"],
            paper_id=row["paper_id"],
            chunk_idx=row["chunk_idx"],
            text=row["text"],
            char_start=row["char_start"],
            char_end=row["char_end"],
        )


@dataclass
class ReferenceRecord:
    id: Optional[int] = None
    paper_id: int = 0
    ref_number: Optional[int] = None
    text: str = ""
    raw_text: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[str] = None
    identifiers: dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    type: str = "journalArticle"
    position_x: Optional[float] = None
    position_y: Optional[float] = None

    def to_row(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "ref_number": self.ref_number,
            "text": self.text,
            "raw_text": self.raw_text,
            "title": self.title,
            "authors": json.dumps(self.authors, ensure_ascii=False),
            "year": self.year,
            "identifiers": json.dumps(self.identifiers, ensure_ascii=False),
            "url": self.url,
            "type": self.type,
            "position_x": self.position_x,
            "position_y": self.position_y,
        }

    @classmethod
    def from_row(cls, row: dict) -> "ReferenceRecord":
        return cls(
            id=row["id"],
            paper_id=row["paper_id"],
            ref_number=row.get("ref_number"),
            text=row["text"],
            raw_text=row["raw_text"],
            title=row["title"],
            authors=json.loads(row["authors"] or "[]"),
            year=row.get("year"),
            identifiers=json.loads(row["identifiers"] or "{}"),
            url=row.get("url"),
            type=row["type"],
            position_x=row.get("position_x"),
            position_y=row.get("position_y"),
        )


@dataclass
class CitationLocationRecord:
    id: Optional[int] = None
    reference_id: int = 0
    paper_id: int = 0
    page: int = 0
    x: float = 0.0
    y: float = 0.0
    text: str = ""
    sentence: str = ""

    def to_row(self) -> dict:
        return {
            "reference_id": self.reference_id,
            "paper_id": self.paper_id,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "sentence": self.sentence,
        }

    @classmethod
    def from_row(cls, row: dict) -> "CitationLocationRecord":
        return cls(
            id=row["id"],
            reference_id=row["reference_id"],
            paper_id=row["paper_id"],
            page=row["page"],
            x=row["x"],
            y=row["y"],
            text=row["text"],
            sentence=row["sentence"],
        )


@dataclass
class TagRecord:
    id: Optional[int] = None
    name: str = ""
    source: str = "user"

    def to_row(self) -> dict:
        return {"name": self.name, "source": self.source}

    @classmethod
    def from_row(cls, row: dict) -> "TagRecord":
        return cls(id=row["id"], name=row["name"], source=row["source"])


@dataclass
class SharedCitationRecord:
    id: Optional[int] = None
    paper_a_id: int = 0
    paper_b_id: int = 0
    shared_ref_title: str = ""
    shared_ref_doi: Optional[str] = None
    relationship: Optional[str] = None         # paper-to-paper relationship label
    confidence: Optional[float] = None         # match confidence 0.0-1.0
    explanation: Optional[str] = None          # LLM-written pair relationship rationale
    similarity_score: Optional[float] = None   # paper abstract cosine
    created_at: Optional[str] = None

    def to_row(self) -> dict:
        return {
            "paper_a_id": self.paper_a_id,
            "paper_b_id": self.paper_b_id,
            "shared_ref_title": self.shared_ref_title,
            "shared_ref_doi": self.shared_ref_doi,
            "relationship": self.relationship,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "similarity_score": self.similarity_score,
        }

    @classmethod
    def from_row(cls, row: dict) -> "SharedCitationRecord":
        return cls(
            id=row["id"],
            paper_a_id=row["paper_a_id"],
            paper_b_id=row["paper_b_id"],
            shared_ref_title=row["shared_ref_title"],
            shared_ref_doi=row.get("shared_ref_doi"),
            relationship=row.get("relationship"),
            confidence=row.get("confidence"),
            explanation=row.get("explanation"),
            similarity_score=row.get("similarity_score"),
            created_at=row.get("created_at"),
        )


@dataclass
class LitReviewEntryRecord:
    id: Optional[int] = None
    paper_id: int = 0
    section_id: Optional[int] = None
    cited_ref_id: Optional[int] = None
    viewpoint: str = ""
    context: Optional[str] = None
    category: Optional[str] = None
    llm_model: Optional[str] = None
    created_at: Optional[str] = None

    def to_row(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "section_id": self.section_id,
            "cited_ref_id": self.cited_ref_id,
            "viewpoint": self.viewpoint,
            "context": self.context,
            "category": self.category,
            "llm_model": self.llm_model,
        }

    @classmethod
    def from_row(cls, row: dict) -> "LitReviewEntryRecord":
        return cls(
            id=row["id"],
            paper_id=row["paper_id"],
            section_id=row.get("section_id"),
            cited_ref_id=row.get("cited_ref_id"),
            viewpoint=row["viewpoint"],
            context=row.get("context"),
            category=row.get("category"),
            llm_model=row.get("llm_model"),
            created_at=row.get("created_at"),
        )

    @property
    def embedding_text(self) -> str:
        """Text representation used as input when embedding this viewpoint.
        Combines category (if any) + the viewpoint sentence — gives the
        vector some structural prior beyond raw natural-language."""
        prefix = f"[{self.category}] " if self.category else ""
        return (prefix + (self.viewpoint or "")).strip()
