"""`paperdb embed` — compute embeddings for one paper / --all / --missing."""

from __future__ import annotations

import sys

from db_connection import DatabaseConnection
from batch_processor import BatchProcessor
from db.paper_repo import PaperRepo


def cmd_embed(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)

    if args.all_papers:
        targets = paper_repo.list_papers(limit=10000)
    elif args.missing:
        targets = paper_repo.list_papers_without_embeddings(limit=10000)
    else:
        p = paper_repo.get_by_id(args.paper_id)
        if not p:
            print(f"Paper not found: {args.paper_id}", file=sys.stderr)
            sys.exit(1)
        targets = [p]

    if not targets:
        print("No papers to embed.")
        return

    print(f"Embedding {len(targets)} paper(s)...")

    if args.dry_run:
        for p in targets:
            sec_count = len(paper_repo.get_sections(p.id))
            print(f"  [{p.id}] {p.title[:70]}  (sections={sec_count})")
        print("(dry-run: no API calls made)")
        return

    proc = BatchProcessor(db, verbose=args.verbose, with_embeddings=True)
    try:
        proc._get_embedder()  # eager init to fail fast on bad config
    except Exception as e:
        print(f"Embedder init failed: {e}", file=sys.stderr)
        sys.exit(2)

    successes = 0
    skipped = 0
    failures = 0
    total_vectors = 0
    for p in targets:
        try:
            n = proc.embed_paper_by_id(
                p.id,
                force=args.force,
                include_sections=not args.skip_sections,
                include_refs=not args.skip_refs,
            )
            if n == 0:
                skipped += 1
                print(f"  [{p.id}] already embedded, skipped (use --force)")
            else:
                successes += 1
                total_vectors += n
                print(f"  [{p.id}] {p.title[:60]} → {n} vectors")
        except Exception as e:
            failures += 1
            print(f"  [{p.id}] ERROR: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()

    print(f"\nDone. success={successes} skipped={skipped} failed={failures} "
          f"total_vectors={total_vectors}")
