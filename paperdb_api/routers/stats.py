from __future__ import annotations

from fastapi import APIRouter, Depends

from db.paper_repo import PaperRepo
from db.shared_citations_repo import SharedCitationsRepo
from paperdb_api.deps import get_db
from paperdb_api.schemas import StatsResponse


router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsResponse)
def stats(db=Depends(get_db)) -> StatsResponse:
    paper_repo = PaperRepo(db)
    base = paper_repo.get_stats()
    emb = paper_repo.get_embedding_stats()
    sc = SharedCitationsRepo(db)
    return StatsResponse(
        papers=base["papers"],
        sections=base["sections"],
        references=base["references"],
        citation_locations=base["citation_locations"],
        tags=base["tags"],
        embeddings=emb,
        shared_citation_rows=sc.count(),
        shared_citation_pairs=sc.count_pairs(),
    )
