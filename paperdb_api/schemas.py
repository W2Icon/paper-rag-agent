"""Pydantic response/request models for the REST API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Papers ────────────────────────────────────────────────────────


class PaperSummary(BaseModel):
    id: int
    title: str
    authors: list[str] = []
    doi: Optional[str] = None
    total_pages: int = 0
    ingested_at: Optional[str] = None
    has_embedding: bool = False
    has_llm_analysis: bool = False


class PaperDetail(BaseModel):
    id: int
    title: str
    authors: list[str] = []
    affiliations: list[str] = []
    abstract: str = ""
    keywords: list[str] = []
    doi: Optional[str] = None
    total_pages: int = 0
    pdf_path: str = ""
    ingested_at: Optional[str] = None
    llm_summary: Optional[str] = None
    llm_research_field: Optional[str] = None
    llm_methodology: Optional[str] = None
    llm_key_findings: list[str] = []
    llm_analyzed_at: Optional[str] = None
    tags: list[str] = []


class SectionItem(BaseModel):
    id: Optional[int] = None
    heading: str
    level: int
    page_start: int
    page_end: int
    paragraphs: list[str] = []


class ReferenceItem(BaseModel):
    id: Optional[int] = None
    ref_number: Optional[int] = None
    title: str
    authors: list[str] = []
    year: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    type: str = "journalArticle"
    llm_relevance_score: Optional[float] = None
    llm_relationship: Optional[str] = None


# ── Search ────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector|fts)$")
    top_k: int = Field(default=10, ge=1, le=100)
    rerank: bool = False
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    tag: Optional[str] = None


class SearchHitResponse(BaseModel):
    paper_id: int
    title: str
    abstract_snippet: str = ""
    score: float
    source_scores: dict[str, float] = {}
    rank_in_source: dict[str, int] = {}
    rerank_reason: str = ""


class SearchResponse(BaseModel):
    query: str
    mode: str
    rerank: bool
    hits: list[SearchHitResponse]
    timings_ms: dict[str, int] = {}
    hit_counts: dict[str, int] = {}


# ── Stats ─────────────────────────────────────────────────────────


class StatsResponse(BaseModel):
    papers: int
    sections: int
    references: int
    citation_locations: int
    tags: int
    embeddings: dict[str, int]
    shared_citation_rows: int
    shared_citation_pairs: int


# ── Ingest ────────────────────────────────────────────────────────


class IngestResponse(BaseModel):
    status: str  # "success" | "skipped" | "error"
    paper_id: Optional[int] = None
    title: str = ""
    sections_count: int = 0
    references_count: int = 0
    citations_count: int = 0
    embeddings_count: int = 0
    error_message: Optional[str] = None


# ── Agent ─────────────────────────────────────────────────────────


class AgentRequest(BaseModel):
    query: str
    task: str = Field(default="auto",
                       pattern="^(auto|lit-review|qa|compare|gap)$")
    no_synth: bool = False
