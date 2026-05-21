from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from paperdb_api.deps import get_db, get_llm_provider_cached


router = APIRouter(prefix="/cross-analyze", tags=["cross-analyze"])


@router.post("/detect")
def detect(
    paper_id: Optional[int] = None,
    use_embedding: bool = False,
    embedding_threshold: float = 0.92,
    db=Depends(get_db),
):
    from cross_analysis import CitationCrossAnalyzer
    from db.shared_citations_repo import SharedCitationsRepo
    repo = SharedCitationsRepo(db)
    n = CitationCrossAnalyzer(db).detect_all_shared_citations(
        use_embedding=use_embedding,
        embedding_threshold=embedding_threshold,
        paper_id_filter=paper_id,
    )
    return {"inserted_rows": n, "total_rows": repo.count(),
            "total_pairs": repo.count_pairs()}


@router.post("/relate")
def relate(min_shared: int = 2, limit: int = 50, tier: str = "simple",
            db=Depends(get_db)):
    from cross_analysis import CitationCrossAnalyzer
    provider = get_llm_provider_cached()
    if provider is None:
        raise HTTPException(503, "Relate requires LLM_PROVIDER + LLM_API_KEY")
    ok, fail = CitationCrossAnalyzer(db).relate_all_pairs(
        provider, min_shared=min_shared, limit=limit, tier=tier
    )
    return {"success": ok, "failed": fail}


@router.get("/pair/{a}/{b}")
def get_pair(a: int, b: int, db=Depends(get_db)):
    from db.shared_citations_repo import SharedCitationsRepo
    from db.paper_repo import PaperRepo
    a, b = sorted([a, b])
    repo = SharedCitationsRepo(db)
    rows = repo.get_for_pair(a, b)
    if not rows:
        raise HTTPException(404, f"no shared citations between {a} and {b}")
    paper_repo = PaperRepo(db)
    pa = paper_repo.get_by_id(a); pb = paper_repo.get_by_id(b)
    rel = next((r.relationship for r in rows if r.relationship), None)
    sim = next((r.similarity_score for r in rows
                 if r.similarity_score is not None), None)
    expl = next((r.explanation for r in rows if r.explanation), None)
    return {
        "paper_a": {"id": a, "title": pa.title if pa else "?"},
        "paper_b": {"id": b, "title": pb.title if pb else "?"},
        "relationship": rel,
        "similarity_score": sim,
        "explanation": expl,
        "shared_refs": [
            {"title": r.shared_ref_title, "doi": r.shared_ref_doi,
             "confidence": r.confidence}
            for r in rows
        ],
    }


@router.get("/list")
def list_pairs(
    paper_id: Optional[int] = None,
    min_shared: int = 2,
    limit: int = 20,
    db=Depends(get_db),
):
    from db.shared_citations_repo import SharedCitationsRepo
    repo = SharedCitationsRepo(db)
    if paper_id:
        return repo.list_pairs_for_paper(paper_id, min_shared=min_shared)
    return repo.list_top_pairs(min_shared=min_shared, limit=limit)
