"""`paperdb cross-analyze ...` — citation cross-analysis subcommands."""

from __future__ import annotations

import sys

from db_connection import DatabaseConnection
from db.paper_repo import PaperRepo


def cmd_cross_analyze(args, db: DatabaseConnection) -> None:
    from cross_analysis import CitationCrossAnalyzer
    from db.shared_citations_repo import SharedCitationsRepo

    analyzer = CitationCrossAnalyzer(db)
    repo = SharedCitationsRepo(db)
    action = args.cx_action or "list"

    if action == "detect":
        n = analyzer.detect_all_shared_citations(
            use_doi=not getattr(args, "no_doi", False),
            use_title=not getattr(args, "no_title", False),
            use_embedding=getattr(args, "with_embedding", False),
            embedding_threshold=getattr(args, "embedding_threshold", 0.92),
            paper_id_filter=getattr(args, "paper_id", None),
        )
        print(f"Detected and inserted {n} shared-citation row(s).")
        print(f"Total rows in shared_citations: {repo.count()}  "
              f"(distinct pairs: {repo.count_pairs()})")
        return

    if action == "relate":
        from llm_interface import LLMConfig, get_llm_provider
        try:
            cfg = LLMConfig.from_env()
            llm = get_llm_provider(cfg)
        except Exception as e:
            print(f"LLM init failed: {e}", file=sys.stderr)
            sys.exit(2)
        ok, fail = analyzer.relate_all_pairs(
            llm, min_shared=args.min_shared, limit=args.limit,
            tier=args.tier, verbose=args.verbose,
        )
        print(f"\nDone. relate ok={ok} failed={fail}")
        return

    if action == "all":
        n = analyzer.detect_all_shared_citations(
            use_embedding=getattr(args, "with_embedding", False),
        )
        print(f"Detected {n} shared-citation row(s).")
        from llm_interface import LLMConfig, get_llm_provider
        cfg = LLMConfig.from_env()
        llm = get_llm_provider(cfg)
        ok, fail = analyzer.relate_all_pairs(
            llm, min_shared=args.min_shared, verbose=args.verbose,
        )
        print(f"LLM relate ok={ok} failed={fail}")
        return

    if action == "pair":
        rows = repo.get_for_pair(args.paper_a, args.paper_b)
        if not rows:
            print(f"No shared citations between papers {args.paper_a} and {args.paper_b}.")
            return
        a, b = sorted([args.paper_a, args.paper_b])
        paper_repo = PaperRepo(db)
        pa = paper_repo.get_by_id(a)
        pb = paper_repo.get_by_id(b)
        print(f"Paper A [{a}]: {pa.title[:80] if pa else '?'}")
        print(f"Paper B [{b}]: {pb.title[:80] if pb else '?'}")
        rel = next((r.relationship for r in rows if r.relationship), None)
        sim = next((r.similarity_score for r in rows if r.similarity_score is not None), None)
        expl = next((r.explanation for r in rows if r.explanation), None)
        print(f"\nRelationship:    {rel or '(not analyzed yet — run `cross-analyze relate`)'}")
        if sim is not None:
            print(f"Abstract cosine: {sim:.3f}")
        if expl:
            print(f"Explanation:     {expl}")

        print(f"\nShared references ({len(rows)}):")
        for r in rows:
            doi = f"  doi:{r.shared_ref_doi}" if r.shared_ref_doi else ""
            print(f"  [{r.confidence:.2f}] {r.shared_ref_title[:80]}{doi}")
        return

    # default / "list"
    if getattr(args, "paper_id", None):
        rows = repo.list_pairs_for_paper(args.paper_id, min_shared=args.min_shared)
        if not rows:
            print(f"No pairs found for paper {args.paper_id} (min_shared={args.min_shared}).")
            return
        paper_repo = PaperRepo(db)
        anchor = paper_repo.get_by_id(args.paper_id)
        print(f"Pairs involving [{args.paper_id}] {anchor.title[:60] if anchor else '?'}:")
        for r in rows[:args.limit]:
            other = paper_repo.get_by_id(r["other_id"])
            rel = r["relationship"] or "(unrelated yet)"
            sim = f"  sim={r['similarity_score']:.3f}" if r["similarity_score"] else ""
            print(f"  [{r['other_id']:>4}] shared={r['shared_count']:>2}  {rel:<25}{sim}")
            print(f"         {other.title[:80] if other else '?'}")
        return

    rows = repo.list_top_pairs(min_shared=args.min_shared, limit=args.limit)
    if not rows:
        print(f"No paper pairs with ≥{args.min_shared} shared refs.")
        return
    paper_repo = PaperRepo(db)
    print(f"Top {len(rows)} paper pair(s) by shared-ref count:")
    for r in rows:
        a = paper_repo.get_by_id(r["paper_a_id"])
        b = paper_repo.get_by_id(r["paper_b_id"])
        rel = r["relationship"] or "(unrelated yet)"
        sim = f"  sim={r['similarity_score']:.3f}" if r["similarity_score"] else ""
        print(f"  [{r['paper_a_id']:>3}]↔[{r['paper_b_id']:>3}] shared={r['shared_count']:>2}  "
              f"{rel:<30}{sim}")
        if a and b:
            print(f"      A: {a.title[:70]}")
            print(f"      B: {b.title[:70]}")
