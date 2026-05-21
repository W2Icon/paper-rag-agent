"""`paperdb ingest` — PDF → DB pipeline (extract + embed + analyze + archive)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from db_connection import DatabaseConnection
from batch_processor import BatchProcessor
from db.tag_repo import TagRepo


def cmd_ingest(args, db: DatabaseConnection) -> None:
    path = Path(args.path)

    # Resolve pipeline switches. Default = full pipeline.
    quick = getattr(args, "quick", False)
    no_embed = quick or getattr(args, "no_embed", False)
    no_analyze = quick or getattr(args, "no_analyze", False)
    no_cross = quick or getattr(args, "no_cross_analyze", False)
    no_archive = quick or getattr(args, "no_archive", False)
    no_kg = quick or getattr(args, "no_kg_update", False)

    backend = args.backend or os.environ.get("PAPER_PARSE_BACKEND") or "pymupdf"

    proc = BatchProcessor(
        db,
        verbose=args.verbose,
        from_page=args.from_page,
        with_embeddings=not no_embed,
        with_analysis=not no_analyze,
        with_cross_analyze=not no_cross,
        with_archive=not no_archive,
        with_kg_update=not no_kg,
        parse_backend=backend,
    )
    tag_repo = TagRepo(db)

    def _print_result(r) -> None:
        if r.status == "success":
            print(f"Ingested: [{r.paper_id}] {r.title}")
            bits = [
                f"Sections: {r.sections_count}",
                f"References: {r.references_count}",
                f"Citations: {r.citations_count}",
            ]
            if r.embeddings_count:
                bits.append(f"Embeddings: {r.embeddings_count}")
            if r.analyzed:
                bits.append(f"Viewpoints: {r.viewpoints_count}")
            if r.cross_pairs_detected or r.cross_pairs_related:
                bits.append(
                    f"Cross-edges: {r.cross_pairs_detected} detected, "
                    f"{r.cross_pairs_related} related"
                )
            if r.kg_updated:
                bits.append("KG synced" + ("→promoted" if r.kg_promoted else ""))
            print("  " + "  |  ".join(bits))
            if r.archive_path:
                print(f"  Archive: {r.archive_path}")
            for w in r.pipeline_warnings:
                print(f"  WARN: {w}", file=sys.stderr)
        elif r.status == "skipped":
            print(f"Skipped (duplicate): [{r.paper_id}] {r.title}")
        else:
            print(f"Error: {r.error_message}", file=sys.stderr)

    if path.is_file():
        r = proc.ingest_pdf(path)
        _print_result(r)
        if r.status == "error":
            sys.exit(1)
        if r.status == "success":
            for t in args.tag:
                tag_repo.tag_paper(r.paper_id, t)
                print(f"  Tagged: {t}")
    elif path.is_dir():
        batch = proc.ingest_directory(path, recursive=args.recursive)
        print(batch.summary())
        if args.tag:
            for r in batch.results:
                if r.status == "success" and r.paper_id:
                    for t in args.tag:
                        tag_repo.tag_paper(r.paper_id, t)
    else:
        print(f"Error: path not found: {args.path}", file=sys.stderr)
        sys.exit(1)
