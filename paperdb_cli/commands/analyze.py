"""`paperdb analyze` — three-stage LLM analysis: summary, ref scoring, lit-review viewpoints."""

from __future__ import annotations

import sys

from db_connection import DatabaseConnection
from db.lit_review_repo import LitReviewRepo
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo


def cmd_analyze(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    lr_repo = LitReviewRepo(db)

    if args.all_papers:
        targets = paper_repo.list_papers(limit=10000)
    elif args.missing:
        targets = paper_repo.list_unanalyzed(limit=10000)
    else:
        p = paper_repo.get_by_id(args.paper_id)
        if not p:
            print(f"Paper not found: {args.paper_id}", file=sys.stderr)
            sys.exit(1)
        targets = [p]

    if not targets:
        print("No papers to analyze.")
        return

    if not args.force and not args.missing:
        original = len(targets)
        targets = [p for p in targets if not p.llm_analyzed_at]
        skipped = original - len(targets)
        if skipped:
            print(f"Skipping {skipped} already-analyzed paper(s). Use --force to re-run.")

    if not targets:
        print("Nothing to do.")
        return

    print(f"Analyzing {len(targets)} paper(s)...")

    if args.dry_run:
        for p in targets:
            print(f"  [{p.id}] {p.title[:70]}  (refs={ref_repo.count_references()})")
        print("(dry-run: no LLM calls made)")
        return

    from llm_interface import LLMConfig, get_llm_provider
    from llm_analyzer import PaperAnalyzer

    cfg = LLMConfig.from_env()
    if cfg.provider in ("none", ""):
        print(
            "LLM_PROVIDER is not set. Set LLM_PROVIDER=deepseek (or openai) and "
            "LLM_API_KEY before running `analyze`.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        llm = get_llm_provider(cfg)
    except Exception as e:
        print(f"Failed to initialise LLM provider: {e}", file=sys.stderr)
        sys.exit(2)

    analyzer = PaperAnalyzer(llm, cfg)
    if args.verbose:
        print("LLM stage configuration:")
        for line in analyzer.describe_configuration().splitlines():
            print(f"  {line}")
    successes = 0
    failures = 0

    for p in targets:
        try:
            print(f"\n→ [{p.id}] {p.title[:70]}")
            sections = paper_repo.get_sections(p.id)
            references = ref_repo.get_references_for_paper(p.id)
            citations = paper_repo.get_citation_locations(p.id)

            # Stage 1: paper summary (+ publication_year, as of schema v7)
            if not args.skip_summary:
                analysis = analyzer.analyze_paper(p, sections)
                paper_repo.update_llm_fields(
                    p.id,
                    summary=analysis.summary,
                    research_field=analysis.research_field,
                    methodology=analysis.methodology,
                    key_findings=analysis.key_findings,
                    publication_year=analysis.publication_year,
                )
                p.llm_research_field = analysis.research_field
                if analysis.publication_year is not None:
                    p.publication_year = analysis.publication_year
                print(f"   summary: {analysis.summary[:120]}{'…' if len(analysis.summary) > 120 else ''}")
                print(f"   field:   {analysis.research_field}")
                if analysis.publication_year:
                    print(f"   year:    {analysis.publication_year}")
                print(f"   findings: {len(analysis.key_findings)} item(s)")

            if not args.skip_refs and references:
                scores = analyzer.score_references(p, references)
                updates = [(s.ref_id, s.relevance_score, s.relationship) for s in scores]
                ref_repo.bulk_update_llm_fields(updates)
                print(f"   scored {len(updates)} reference(s)")

            if not args.skip_lit_review and references and citations:
                if args.force:
                    lr_repo.delete_by_paper(p.id)
                entries = analyzer.extract_lit_review(p.id, sections, references, citations)
                if entries:
                    lr_repo.insert_many(entries)
                print(f"   lit-review entries: {len(entries)}")

            successes += 1
        except Exception as e:
            failures += 1
            print(f"   ERROR: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()

    print(f"\nDone. success={successes} failed={failures}")
