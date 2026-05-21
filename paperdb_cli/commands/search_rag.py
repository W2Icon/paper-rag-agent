"""`paperdb search-rag` — three-tier retrieval (multi-path → RRF → optional rerank)."""

from __future__ import annotations

import sys

from db_connection import DatabaseConnection


def cmd_search_rag(args, db: DatabaseConnection) -> None:
    from retrieval import Retriever
    from db.fts_index import ensure_fts_index
    from intent_classifier import classify_intent, get_profile

    ensure_fts_index(db)
    retriever = Retriever(db, embed_field=args.embed_field)

    # Resolve retrieval intent → section weights
    if args.intent == "auto":
        profile = classify_intent(args.query)
    else:
        profile = get_profile(args.intent)
    if args.show_intent or args.explain:
        pat = f" (matched /{profile.matched_pattern}/)" if profile.matched_pattern else ""
        print(f"  intent: {profile.intent} [{profile.matched_by}]{pat}")
        if profile.section_weights:
            weights_str = ", ".join(f"{k}={v}" for k, v in profile.section_weights.items())
            print(f"  section_weights: {weights_str}")

    # Resolve --mode auto → concrete mode via QueryRouter
    resolved_mode = args.mode
    router_decision = None
    if args.mode == "auto":
        from route_classifier import get_router
        llm = None
        try:
            from llm_interface import LLMConfig, get_llm_provider
            cfg = LLMConfig.from_env()
            llm = get_llm_provider(cfg)
        except Exception:
            llm = None  # rule-based fallback
        router_decision = get_router(db, llm=llm).route(args.query)
        resolved_mode = router_decision.mode
        if args.verbose or args.explain:
            print(f"  router  → mode={resolved_mode}  source={router_decision.source}  "
                  f"reason={router_decision.reason}")

    query_vec = None
    use_vector = resolved_mode in ("hybrid", "vector", "all")
    use_fts = resolved_mode in ("hybrid", "fts", "all")
    use_graph = resolved_mode in ("graph", "all")

    if use_vector:
        if retriever.embedded_paper_count == 0:
            print("No papers with embeddings yet. Run `paperdb embed --all` first, "
                  "or use --mode fts.", file=sys.stderr)
            sys.exit(1)
        try:
            from llm_interface import LLMConfig, get_llm_provider
            from embedder import PaperEmbedder
            cfg = LLMConfig.from_env()
            embedder = PaperEmbedder(get_llm_provider(cfg))
            query_vec = embedder.embed_query(args.query)
        except Exception as e:
            print(f"Failed to embed query: {e}", file=sys.stderr)
            sys.exit(2)

    hits = retriever.hybrid_search(
        args.query,
        query_vec,
        per_path_k=args.per_path_k,
        top_k=max(args.top_k, 10) if args.rerank else args.top_k,
        use_vector=use_vector,
        use_fts=use_fts,
        use_graph=use_graph,
        year_from=args.year_from,
        year_to=args.year_to,
        tag=args.tag,
        section_weights=profile.section_weights if not profile.is_default else None,
        use_viewpoints=profile.use_viewpoints,
    )

    if args.rerank and hits:
        from llm_interface import LLMConfig, get_llm_provider
        from reranker import LLMReranker
        cfg = LLMConfig.from_env()
        reranker = LLMReranker(get_llm_provider(cfg))
        hits = reranker.rerank(args.query, hits, top_k=args.top_k)
    else:
        hits = hits[:args.top_k]

    if args.as_json:
        import json as _json
        out = []
        for h in hits:
            out.append({
                "paper_id": h.paper_id,
                "title": h.title,
                "score": h.score,
                "doi": h.doi,
                "source_scores": {k: v for k, v in h.source_scores.items()
                                   if isinstance(v, (int, float))},
                "rank_in_source": h.rank_in_source,
                "rerank_reason": h.rerank_reason,
            })
        print(_json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not hits:
        print(f"No results for: {args.query!r}")
        return

    print(f"Top {len(hits)} result(s) for: {args.query!r}")
    mode_disp = (f"{args.mode}→{resolved_mode}" if args.mode == "auto" and resolved_mode != "auto"
                 else args.mode)
    print(f"  mode={mode_disp}  rerank={args.rerank}  embed_field={args.embed_field}")
    print()
    for i, h in enumerate(hits, 1):
        print(f"  {i:>2}. [{h.paper_id}] {h.title[:80]}")
        meta_bits = []
        if h.doi:
            meta_bits.append(f"doi={h.doi}")
        if h.score:
            meta_bits.append(f"score={h.score:.3f}")
        if args.explain:
            meta_bits.append(f"source: {h.explain()}")
        if meta_bits:
            print(f"       {'  '.join(meta_bits)}")
        if h.rerank_reason:
            print(f"       why: {h.rerank_reason}")
        if args.explain and h.abstract:
            print(f"       {h.abstract[:140]}...")
