from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from config import get_config
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.tag_repo import TagRepo
from paperdb_api.deps import get_db
from paperdb_api.schemas import (
    IngestResponse, PaperDetail, PaperSummary, ReferenceItem, SectionItem,
)


router = APIRouter(prefix="/papers", tags=["papers"])


@router.get("", response_model=list[PaperSummary])
def list_papers(
    limit: int = 50,
    offset: int = 0,
    tag: Optional[str] = None,
    db=Depends(get_db),
):
    paper_repo = PaperRepo(db)
    tag_repo = TagRepo(db)

    if tag:
        ids = tag_repo.get_papers_by_tag(tag)
        papers = [paper_repo.get_by_id(pid) for pid in ids]
        papers = [p for p in papers if p is not None][offset:offset + limit]
    else:
        papers = paper_repo.list_papers(limit=limit, offset=offset)

    return [
        PaperSummary(
            id=p.id,
            title=p.title,
            authors=p.authors,
            doi=p.doi,
            total_pages=p.total_pages,
            ingested_at=p.ingested_at,
            has_llm_analysis=p.llm_analyzed_at is not None,
            has_embedding=_has_embedding(db, p.id),
        )
        for p in papers
    ]


@router.get("/{paper_id}", response_model=PaperDetail)
def get_paper(paper_id: int, db=Depends(get_db)):
    p = PaperRepo(db).get_by_id(paper_id)
    if not p:
        raise HTTPException(404, f"paper {paper_id} not found")
    tags = TagRepo(db).get_tags_for_paper(paper_id)
    return PaperDetail(
        id=p.id,
        title=p.title,
        authors=p.authors,
        affiliations=p.affiliations,
        abstract=p.abstract,
        keywords=p.keywords,
        doi=p.doi,
        total_pages=p.total_pages,
        pdf_path=p.pdf_path,
        ingested_at=p.ingested_at,
        llm_summary=p.llm_summary,
        llm_research_field=p.llm_research_field,
        llm_methodology=p.llm_methodology,
        llm_key_findings=p.llm_key_findings or [],
        llm_analyzed_at=p.llm_analyzed_at,
        tags=[t.name for t in tags],
    )


@router.get("/{paper_id}/sections", response_model=list[SectionItem])
def get_sections(paper_id: int, db=Depends(get_db)):
    secs = PaperRepo(db).get_sections(paper_id)
    return [
        SectionItem(
            id=s.id, heading=s.heading, level=s.level,
            page_start=s.page_start, page_end=s.page_end,
            paragraphs=s.paragraphs,
        )
        for s in secs
    ]


@router.get("/{paper_id}/references", response_model=list[ReferenceItem])
def get_references(paper_id: int, db=Depends(get_db)):
    import json as _json
    rows = db.conn.execute(
        "SELECT id, ref_number, title, authors, year, identifiers, url, type, "
        "llm_relevance_score, llm_relationship "
        "FROM references_ WHERE paper_id = ? ORDER BY ref_number",
        (paper_id,),
    ).fetchall()
    out = []
    for r in rows:
        ids = _json.loads(r["identifiers"] or "{}")
        out.append(ReferenceItem(
            id=r["id"], ref_number=r["ref_number"], title=r["title"] or "",
            authors=_json.loads(r["authors"] or "[]"),
            year=r["year"], doi=ids.get("DOI"), url=r["url"],
            type=r["type"] or "journalArticle",
            llm_relevance_score=r["llm_relevance_score"],
            llm_relationship=r["llm_relationship"],
        ))
    return out


@router.delete("/{paper_id}")
def delete_paper(paper_id: int, db=Depends(get_db)):
    ok = PaperRepo(db).delete_paper(paper_id)
    if not ok:
        raise HTTPException(404, f"paper {paper_id} not found")
    kg_cleanup = None
    try:
        from knowledge_graph import KGBuilder
        kg_cleanup = KGBuilder(db).remove_paper(paper_id)
    except Exception:
        pass  # KG cleanup is best-effort; the paper itself is gone
    return {"status": "deleted", "paper_id": paper_id, "kg": kg_cleanup}


@router.post("/ingest", response_model=IngestResponse)
async def ingest_pdf(
    file: UploadFile = File(...),
    embed: bool = False,
    db=Depends(get_db),
):
    from batch_processor import BatchProcessor

    cfg = get_config()
    cfg.ensure_dirs()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Expected a .pdf file upload")

    target = cfg.pdf_dir / file.filename
    # Avoid clobbering; if same name exists, append a counter
    counter = 1
    while target.exists():
        target = cfg.pdf_dir / f"{Path(file.filename).stem}-{counter}.pdf"
        counter += 1

    content = await file.read()
    target.write_bytes(content)

    proc = BatchProcessor(db, verbose=False, with_embeddings=embed)
    result = proc.ingest_pdf(target)

    return IngestResponse(
        status=result.status,
        paper_id=result.paper_id,
        title=result.title,
        sections_count=result.sections_count,
        references_count=result.references_count,
        citations_count=result.citations_count,
        embeddings_count=result.embeddings_count,
        error_message=result.error_message,
    )


# ── helpers ───────────────────────────────────────────────────────


def _has_embedding(db, paper_id: int) -> bool:
    row = db.conn.execute(
        "SELECT abstract_embedding IS NOT NULL FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    return bool(row and row[0])
