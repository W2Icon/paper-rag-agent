"""`paperdb archive` + `paperdb fix-metadata` — no-LLM bulk operations."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

from db_connection import DatabaseConnection
from db.paper_repo import PaperRepo


def cmd_archive(args, db: DatabaseConnection) -> None:
    """Render markdown files of the DB content to a browseable folder."""
    from config import get_config
    from archive_writer import write_all, write_index, write_paper

    if args.archive_dir:
        archive_dir = Path(args.archive_dir).expanduser()
    else:
        archive_dir = get_config().archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)

    if args.index_only:
        index, graph = write_index(db, archive_dir)
        print(f"  wrote {index}")
        print(f"  wrote {graph}")
        return

    if args.paper_id is not None:
        path = write_paper(db, args.paper_id, archive_dir)
        print(f"  wrote {path}")
        index, _ = write_index(db, archive_dir)
        print(f"  refreshed {index}")
        return

    # --all (or no arg given) → regenerate everything
    result = write_all(db, archive_dir)
    print(f"  wrote {result['papers']} paper file(s)")
    print(f"  wrote {result['index']}")
    print(f"  wrote {result['graph']}")
    print(f"\nArchive ready at: {archive_dir}")


def cmd_fix_metadata(args, db: DatabaseConnection) -> None:
    """Re-run metadata extraction on PDFs already in the library and write
    fresh title/authors/affiliations/abstract/keywords/doi values to the DB.
    Useful after a heuristic fix in metadata_extractor.py."""
    import fitz
    from metadata_extractor import extract_metadata

    paper_repo = PaperRepo(db)

    if args.all_papers:
        targets = paper_repo.list_papers(limit=10000)
    elif args.paper_id is not None:
        p = paper_repo.get_by_id(args.paper_id)
        if not p:
            print(f"Paper not found: {args.paper_id}", file=sys.stderr)
            sys.exit(1)
        targets = [p]
    else:
        print("Usage: paperdb fix-metadata <paper_id> | --all", file=sys.stderr)
        sys.exit(1)

    updated = 0
    skipped_missing = 0
    for p in targets:
        pdf_path = Path(p.pdf_path)
        if not pdf_path.exists():
            print(f"  [{p.id}] SKIP (PDF missing at {p.pdf_path})")
            skipped_missing += 1
            continue

        try:
            doc = fitz.open(pdf_path)
            new_meta = extract_metadata(doc)
        except Exception as e:
            print(f"  [{p.id}] ERROR: {e}", file=sys.stderr)
            continue

        changes = []
        if (new_meta.title or "").strip() != (p.title or "").strip():
            changes.append(("title", p.title, new_meta.title))
        old_authors = p.authors or []
        if new_meta.authors and new_meta.authors != old_authors:
            changes.append(("authors", f"{len(old_authors)} authors", f"{len(new_meta.authors)} authors"))
        old_doi = (p.doi or "").strip()
        new_doi = (new_meta.doi or "").strip()
        if new_doi and new_doi != old_doi:
            changes.append(("doi", old_doi or "(none)", new_doi))

        if not changes:
            print(f"  [{p.id}] no changes — {(p.title or '')[:60]}")
            continue

        print(f"  [{p.id}] changes:")
        for field_name, before, after in changes:
            before_s = str(before)[:80]
            after_s = str(after)[:80]
            print(f"      {field_name}:")
            print(f"        before: {before_s}")
            print(f"        after : {after_s}")

        if args.dry_run:
            continue

        with db.transaction() as cur:
            cur.execute(
                """
                UPDATE papers
                SET title = ?,
                    authors = ?,
                    affiliations = ?,
                    abstract = ?,
                    keywords = ?,
                    doi = COALESCE(NULLIF(?, ''), doi)
                WHERE id = ?
                """,
                (
                    new_meta.title or p.title,
                    _json.dumps(new_meta.authors or p.authors, ensure_ascii=False),
                    _json.dumps(new_meta.affiliations or p.affiliations, ensure_ascii=False),
                    new_meta.abstract or p.abstract,
                    _json.dumps(new_meta.keywords or p.keywords, ensure_ascii=False),
                    new_meta.doi or "",
                    p.id,
                ),
            )
        updated += 1

        if args.re_embed_title:
            try:
                from llm_interface import LLMConfig, get_llm_provider
                from embedder import PaperEmbedder, vec_to_bytes
                cfg = LLMConfig.from_env()
                emb = PaperEmbedder(get_llm_provider(cfg))
                vec = emb.embed_query(new_meta.title)
                paper_repo.update_paper_embeddings(p.id, title=vec_to_bytes(vec))
                print(f"      title_embedding re-computed")
            except Exception as e:
                print(f"      title_embedding skipped: {e}", file=sys.stderr)

    print(f"\nUpdated {updated} paper(s)."
          + (f"  Skipped (PDF missing): {skipped_missing}" if skipped_missing else ""))
