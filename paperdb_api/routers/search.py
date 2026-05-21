from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from db.fts_index import ensure_fts_index
from paperdb_api.deps import get_db, get_embedder_cached, get_llm_provider_cached
from paperdb_api.schemas import SearchHitResponse, SearchRequest, SearchResponse
from retrieval import Retriever
from reranker import LLMReranker


router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, db=Depends(get_db)) -> SearchResponse:
    timings: dict[str, int] = {}
    t_total = time.time()

    ensure_fts_index(db)
    retriever = Retriever(db)

    use_vector = req.mode in ("hybrid", "vector")
    use_fts = req.mode in ("hybrid", "fts")

    query_vec = None
    if use_vector:
        if retriever.embedded_paper_count == 0:
            raise HTTPException(
                400,
                "No papers have embeddings yet. POST /papers/{id}/embed first, "
                "or use mode='fts'.",
            )
        try:
            embedder = get_embedder_cached()
            t = time.time()
            query_vec = embedder.embed_query(req.query)
            timings["embed_ms"] = int((time.time() - t) * 1000)
        except Exception as e:
            raise HTTPException(503, f"Failed to embed query: {e}")

    t = time.time()
    hits = retriever.hybrid_search(
        req.query, query_vec,
        per_path_k=50,
        top_k=max(req.top_k, 10) if req.rerank else req.top_k,
        use_vector=use_vector, use_fts=use_fts,
        year_from=req.year_from, year_to=req.year_to, tag=req.tag,
    )
    timings["retrieval_ms"] = int((time.time() - t) * 1000)

    # Per-path hit counts (derived from rank_in_source, populated by RRF fusion)
    hit_counts: dict[str, int] = {}
    for h in hits:
        for src in (h.rank_in_source or {}):
            hit_counts[src] = hit_counts.get(src, 0) + 1

    if req.rerank and hits:
        provider = get_llm_provider_cached()
        if provider is None:
            raise HTTPException(503, "Rerank requires LLM_PROVIDER + LLM_API_KEY")
        t = time.time()
        hits = LLMReranker(provider).rerank(req.query, hits, top_k=req.top_k)
        timings["rerank_ms"] = int((time.time() - t) * 1000)
    else:
        hits = hits[:req.top_k]

    timings["total_ms"] = int((time.time() - t_total) * 1000)

    return SearchResponse(
        query=req.query, mode=req.mode, rerank=req.rerank,
        timings_ms=timings,
        hit_counts=hit_counts,
        hits=[
            SearchHitResponse(
                paper_id=h.paper_id,
                title=h.title,
                abstract_snippet=(h.abstract or "")[:300],
                score=h.score,
                source_scores={k: v for k, v in h.source_scores.items()
                               if isinstance(v, (int, float))},
                rank_in_source=h.rank_in_source,
                rerank_reason=h.rerank_reason,
            )
            for h in hits
        ],
    )
