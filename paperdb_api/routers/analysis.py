"""Routes for Phase 2 (analyze) and Phase 3 (embed) — synchronous endpoints.

These can take 30-120 seconds; in a future iteration they should move to a
background task queue. For now we run them in the request thread.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.lit_review_repo import LitReviewRepo
from paperdb_api.deps import (
    get_db, get_embedder_cached, get_llm_provider_cached,
)


router = APIRouter(prefix="/papers", tags=["analysis"])


@router.post("/{paper_id}/analyze")
def analyze_paper(
    paper_id: int,
    skip_refs: bool = False,
    skip_lit_review: bool = False,
    force: bool = False,
    db=Depends(get_db),
):
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    lr_repo = LitReviewRepo(db)

    p = paper_repo.get_by_id(paper_id)
    if not p:
        raise HTTPException(404, f"paper {paper_id} not found")
    if p.llm_analyzed_at and not force:
        return {"status": "skipped", "reason": "already analyzed; use force=true"}

    provider = get_llm_provider_cached()
    if provider is None:
        raise HTTPException(503, "Analyze requires LLM_PROVIDER + LLM_API_KEY")

    from llm_interface import LLMConfig
    from llm_analyzer import PaperAnalyzer
    cfg = LLMConfig.from_env()
    analyzer = PaperAnalyzer(provider, cfg)

    sections = paper_repo.get_sections(paper_id)
    references = ref_repo.get_references_for_paper(paper_id)
    citations = paper_repo.get_citation_locations(paper_id)

    analysis = analyzer.analyze_paper(p, sections)
    paper_repo.update_llm_fields(
        paper_id,
        summary=analysis.summary, research_field=analysis.research_field,
        methodology=analysis.methodology, key_findings=analysis.key_findings,
    )
    p.llm_research_field = analysis.research_field

    scored = []
    if not skip_refs and references:
        scored = analyzer.score_references(p, references)
        ref_repo.bulk_update_llm_fields(
            [(s.ref_id, s.relevance_score, s.relationship) for s in scored]
        )

    entries = []
    if not skip_lit_review and references and citations:
        if force:
            lr_repo.delete_by_paper(paper_id)
        entries = analyzer.extract_lit_review(paper_id, sections, references, citations)
        if entries:
            lr_repo.insert_many(entries)

    return {
        "status": "success",
        "paper_id": paper_id,
        "summary": analysis.summary,
        "research_field": analysis.research_field,
        "key_findings_count": len(analysis.key_findings),
        "refs_scored": len(scored),
        "lit_review_entries": len(entries),
    }


@router.post("/{paper_id}/embed")
def embed_paper(paper_id: int, force: bool = False, db=Depends(get_db)):
    from batch_processor import BatchProcessor
    proc = BatchProcessor(db, verbose=False, with_embeddings=True)
    try:
        n = proc.embed_paper_by_id(paper_id, force=force)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(503, str(e))
    return {"status": "success" if n > 0 else "skipped",
            "paper_id": paper_id, "vectors_written": n}
