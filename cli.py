#!/usr/bin/env python3
"""
Academic paper database CLI.

Usage:
    python cli.py ingest paper.pdf [-v] [--tag TAG]
    python cli.py ingest ./papers/ --recursive
    python cli.py list [--limit N] [--tag TAG]
    python cli.py show <paper_id> [--sections] [--references] [--citations]
    python cli.py search "query" [--field all|title|abstract|author]
    python cli.py delete <paper_id> [--force]
    python cli.py stats
    python cli.py export [-o file.json] [--paper-id N]
    python cli.py tag add <paper_id> <tag>
    python cli.py tag remove <paper_id> <tag>
    python cli.py tag list
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from db_connection import DatabaseConnection
from schema import init_schema
from batch_processor import BatchProcessor
from db.paper_repo import PaperRepo
from db.reference_repo import ReferenceRepo
from db.tag_repo import TagRepo
from db.lit_review_repo import LitReviewRepo


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--db", default=None, help="Path to SQLite database (overrides workspace)")
    parent.add_argument(
        "--workspace", default=None,
        help="Path to paperdb workspace directory (default: ~/paperdb/, or "
             "PAPERDB_WORKSPACE env var, or nearest paperdb.workspace marker)",
    )
    parent.add_argument("--verbose", "-v", action="store_true")

    parser = argparse.ArgumentParser(
        prog="paperdb",
        description="Academic paper database — ingest, search, and manage papers.",
        parents=[parent],
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── init: bootstrap a new workspace ─────────────────────────
    p = sub.add_parser("init", parents=[parent],
                       help="Create a new paperdb workspace at the given path "
                            "(default: ~/paperdb/) with config.toml template")
    p.add_argument("path", nargs="?", default=None,
                   help="Workspace path (default: ~/paperdb)")
    p.add_argument("--force-config", action="store_true",
                   help="Overwrite an existing config.toml (use with care; "
                        "wipes any API keys you've already set)")

    # ── workspace: introspection + migration ────────────────────
    p = sub.add_parser("workspace", parents=[parent],
                       help="Inspect or migrate the workspace")
    ws_sub = p.add_subparsers(dest="workspace_action")
    ws_show = ws_sub.add_parser("show", parents=[parent],
                                help="Print the active workspace location and contents")
    ws_show.add_argument("--json", action="store_true", dest="as_json")
    ws_mig = ws_sub.add_parser("migrate", parents=[parent],
                               help="Move legacy OS-dir data into a unified workspace")
    ws_mig.add_argument("--to", default=None,
                        help="Target workspace path (default: ~/paperdb)")
    ws_mig.add_argument("--dry-run", action="store_true",
                        help="Print the move plan without doing anything")
    ws_mig.add_argument("--no-archive", action="store_true",
                        help="Don't rename legacy directories to *.backup-DATE")
    ws_mig.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt")

    # ingest — one-stop pipeline:
    # extract → embed → LLM analyze → cross-analyze → write markdown archive.
    # All steps default-on; use --no-<step> flags to opt out.
    p = sub.add_parser("ingest", parents=[parent],
                       help="Ingest PDF(s): extract + embed + analyze + cross-analyze + archive")
    p.add_argument("path", help="PDF file or directory path")
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("--from-page", type=int, default=0)
    p.add_argument("--tag", action="append", default=[])
    # Parsing backend (extraction layer)
    p.add_argument(
        "--backend",
        choices=["pymupdf", "mineru", "auto"],
        default=None,
        help="PDF parsing backend. pymupdf=fast heuristic (default), "
             "mineru=layout-aware DL with OCR/formula/table, "
             "auto=sniff scanned-vs-digital. "
             "Defaults to env PAPER_PARSE_BACKEND or 'pymupdf'.",
    )
    # Opt-outs for the pipeline (default = run everything)
    p.add_argument("--no-embed", action="store_true",
                   help="Skip vector embedding (also disables analyze/archive)")
    p.add_argument("--no-analyze", action="store_true",
                   help="Skip LLM analysis (summary, ref scoring, viewpoints)")
    p.add_argument("--no-cross-analyze", action="store_true",
                   help="Skip cross-paper relationship detection + LLM relate")
    p.add_argument("--no-archive", action="store_true",
                   help="Skip writing markdown archive files")
    p.add_argument("--no-kg-update", action="store_true",
                   help="Skip knowledge-graph sync (use `kg build` once after bulk import)")
    p.add_argument("--quick", action="store_true",
                   help="Shorthand: extract + persist only, skip ALL LLM/embed/archive steps")
    # Legacy alias: --embed used to be opt-in. Keep as no-op for back-compat.
    p.add_argument("--embed", action="store_true", help=argparse.SUPPRESS)

    # list
    p = sub.add_parser("list", parents=[parent], help="List papers")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--tag", help="Filter by tag")
    p.add_argument("--json", action="store_true", dest="as_json")

    # show
    p = sub.add_parser("show", parents=[parent], help="Show paper details")
    p.add_argument("paper_id", type=int)
    p.add_argument("--sections", action="store_true")
    p.add_argument("--references", action="store_true")
    p.add_argument("--citations", action="store_true")
    p.add_argument("--llm", action="store_true", help="Show LLM analysis (Phase 2)")
    p.add_argument("--json", action="store_true", dest="as_json")

    # search
    p = sub.add_parser("search", parents=[parent], help="Search papers by keyword")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--field", choices=["all", "title", "abstract", "author"], default="all")
    p.add_argument("--json", action="store_true", dest="as_json")

    # delete
    p = sub.add_parser("delete", parents=[parent], help="Delete a paper")
    p.add_argument("paper_id", type=int)
    p.add_argument("--force", "-f", action="store_true")

    # stats
    p = sub.add_parser("stats", parents=[parent], help="Database statistics")
    p.add_argument("--json", action="store_true", dest="as_json")

    # export
    p = sub.add_parser("export", parents=[parent], help="Export papers to JSON")
    p.add_argument("-o", "--output", help="Output file (default: stdout)")
    p.add_argument("--paper-id", type=int)
    p.add_argument("--tag", help="Export papers with tag")
    p.add_argument("--compact", action="store_true")

    # tag
    p = sub.add_parser("tag", parents=[parent], help="Manage tags")
    tag_sub = p.add_subparsers(dest="tag_action")
    pa = tag_sub.add_parser("add", parents=[parent], help="Add tag to paper")
    pa.add_argument("paper_id", type=int)
    pa.add_argument("tag_name")
    pr = tag_sub.add_parser("remove", parents=[parent], help="Remove tag from paper")
    pr.add_argument("paper_id", type=int)
    pr.add_argument("tag_name")
    tag_sub.add_parser("list", parents=[parent], help="List all tags")

    # init-db
    sub.add_parser("init-db", parents=[parent], help="Initialize the database schema")

    # config — manage the TOML config file
    p = sub.add_parser("config", parents=[parent], help="Manage configuration file")
    cfg_sub = p.add_subparsers(dest="config_action")
    pi = cfg_sub.add_parser("init", parents=[parent],
                             help="Write a template TOML config (no overwrite without --force)")
    pi.add_argument("--force", action="store_true",
                     help="Overwrite an existing config file")
    pi.add_argument("--path", default=None,
                     help="Custom config path (default: OS-standard location)")
    cfg_sub.add_parser("show", parents=[parent],
                        help="Print the path and effective LLM config")
    cfg_sub.add_parser("path", parents=[parent],
                        help="Print just the config file path (useful for `$(paperdb config path)`)")

    # start — one-shot launcher for API + UI
    p = sub.add_parser("start", parents=[parent],
                        help="Launch paperdb-api and paperdb-ui together")
    p.add_argument("--api-port", type=int, default=8765)
    p.add_argument("--ui-port", type=int, default=8501)
    p.add_argument("--host", default="localhost",
                     help="Bind host for both API and UI (use 0.0.0.0 for LAN)")
    p.add_argument("--no-ui", action="store_true",
                     help="Only start the API")

    # agent (Phase 4 multi-agent assistant)
    p = sub.add_parser("agent", parents=[parent],
                       help="Ask the multi-agent assistant a question")
    p.add_argument("query", help="Natural-language question or task")
    p.add_argument("--task",
                   choices=["auto", "lit-review", "qa", "compare", "gap"],
                   default="auto",
                   help="auto = orchestrator dispatches; otherwise call one specialist directly")
    p.add_argument("--no-synth", action="store_true",
                   help="Skip the synth step; print specialist JSON output as-is")
    p.add_argument("--show-plan", action="store_true",
                   help="Print orchestrator plan before specialist execution")
    p.add_argument("--show-trace", action="store_true",
                   help="Print each specialist's tool-call trace")

    # cross-analyze (Phase 3 citation cross-analysis)
    p = sub.add_parser("cross-analyze", parents=[parent],
                       help="Detect shared references between paper pairs and LLM-characterize relationships")
    cx_sub = p.add_subparsers(dest="cx_action")

    pa = cx_sub.add_parser("detect", parents=[parent],
                            help="Find shared refs across the library (no LLM)")
    pa.add_argument("--paper-id", type=int,
                    help="Only find pairs involving this paper (faster)")
    pa.add_argument("--no-doi", action="store_true", help="Skip DOI matching")
    pa.add_argument("--no-title", action="store_true", help="Skip normalized-title matching")
    pa.add_argument("--with-embedding", action="store_true",
                    help="Also use ref_embedding cosine matching (slower)")
    pa.add_argument("--embedding-threshold", type=float, default=0.92)

    pr = cx_sub.add_parser("relate", parents=[parent],
                            help="LLM-characterize paper-pair relationships")
    pr.add_argument("--min-shared", type=int, default=2,
                    help="Minimum shared refs to consider a pair (default: 2)")
    pr.add_argument("--limit", type=int, default=50)
    pr.add_argument("--tier", choices=["complex", "simple"], default="simple")

    pal = cx_sub.add_parser("all", parents=[parent],
                             help="Detect then relate in one shot")
    pal.add_argument("--min-shared", type=int, default=2)
    pal.add_argument("--with-embedding", action="store_true")

    pp = cx_sub.add_parser("pair", parents=[parent],
                            help="Show shared refs + relationship for a pair")
    pp.add_argument("paper_a", type=int)
    pp.add_argument("paper_b", type=int)

    plist = cx_sub.add_parser("list", parents=[parent],
                               help="List paper pairs ranked by shared-ref count")
    plist.add_argument("--paper-id", type=int,
                       help="Restrict to pairs involving this paper")
    plist.add_argument("--min-shared", type=int, default=2)
    plist.add_argument("--limit", type=int, default=20)

    # kg (Phase 5 knowledge graph)
    p = sub.add_parser("kg", parents=[parent],
                       help="Knowledge graph: keyword / research_field / paper nodes + edges")
    kg_sub = p.add_subparsers(dest="kg_action")

    kb = kg_sub.add_parser("build", parents=[parent],
                            help="(Re)build the graph from existing papers")
    kb.add_argument("--rebuild", action="store_true",
                    help="Wipe kg_nodes/kg_edges first (full rebuild)")
    kb.add_argument("--paper-id", type=int,
                    help="Only sync this paper (incremental)")

    kg_sub.add_parser("stats", parents=[parent],
                      help="Node/edge counts per type (local vs external papers split)")

    kn = kg_sub.add_parser("neighbors", parents=[parent],
                            help="Inspect a node's neighbours in the graph")
    grp = kn.add_mutually_exclusive_group(required=True)
    grp.add_argument("term", nargs="?", default=None,
                     help="Keyword or research_field name (case-insensitive)")
    grp.add_argument("--paper-id", type=int,
                     help="Inspect neighbours of a paper_local node")
    kn.add_argument("--depth", type=int, default=1, choices=[1, 2],
                    help="Traversal depth (default: 1)")
    kn.add_argument("--top", type=int, default=10,
                    help="Top-N items per category (default: 10)")

    kg_sub.add_parser("promote", parents=[parent],
                      help="Scan paper_external nodes and promote any that "
                           "now match an existing paper_local")

    ks = kg_sub.add_parser("search", parents=[parent],
                            help="Graph-only retrieval (debug / preview path)")
    ks.add_argument("query")
    ks.add_argument("--top", type=int, default=10)
    ks.add_argument("--depth", type=int, default=2, choices=[1, 2])
    ks.add_argument("--include-external", action="store_true",
                    help="Also surface paper_external recommendations")
    ks.add_argument("--explain", action="store_true",
                    help="Show resolved anchors before ranking")

    # search-rag (Phase 3 three-tier retrieval)
    p = sub.add_parser("search-rag", parents=[parent],
                       help="Multi-path retrieval with RRF fusion and optional LLM rerank")
    p.add_argument("query", help="Natural-language query (any language)")
    p.add_argument("--mode",
                   choices=["hybrid", "vector", "fts", "graph", "all", "auto"],
                   default="hybrid",
                   help="Recall paths: hybrid=vector+fts (default), graph=KG only, "
                        "all=vector+fts+graph, auto=LLM picks one of "
                        "{vector,fts,graph,all}")
    p.add_argument("--rerank", action="store_true", help="LLM rerank top candidates")
    p.add_argument("--top-k", type=int, default=10, help="Number of results (default: 10)")
    p.add_argument("--per-path-k", type=int, default=50,
                   help="Pre-fusion candidates per recall path (default: 50)")
    p.add_argument("--embed-field", choices=["title_embedding", "abstract_embedding",
                                              "fulltext_embedding"],
                   default="abstract_embedding",
                   help="Which paper-level embedding to compare against")
    p.add_argument("--year-from", type=int, help="Filter by ingestion year >= (papers don't store publication year)")
    p.add_argument("--year-to", type=int)
    p.add_argument("--tag", help="Restrict to papers with this tag")
    p.add_argument("--explain", action="store_true", help="Show per-source rank/score breakdown")
    p.add_argument("--intent",
                   choices=["auto", "summary", "methodology", "experiments_results",
                            "related_work", "gap", "definitions", "default"],
                   default="auto",
                   help="Section-weighted retrieval profile. 'auto' = classify "
                        "from query (rules); 'default' = abstract+FTS only "
                        "(disables section path).")
    p.add_argument("--show-intent", action="store_true",
                   help="Print the resolved intent before running retrieval")
    p.add_argument("--json", action="store_true", dest="as_json")

    # embed (Phase 3 embeddings)
    p = sub.add_parser("embed", parents=[parent], help="Compute embeddings for paper(s)")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("paper_id", nargs="?", type=int, default=None,
                     help="Embed a single paper by ID")
    grp.add_argument("--all", action="store_true", dest="all_papers",
                     help="Embed every paper in the database")
    grp.add_argument("--missing", action="store_true",
                     help="Embed only papers without title/abstract embedding")
    p.add_argument("--skip-sections", action="store_true",
                   help="Don't embed section bodies")
    p.add_argument("--skip-refs", action="store_true",
                   help="Don't embed individual references")
    p.add_argument("--force", action="store_true",
                   help="Re-embed even if already populated")
    p.add_argument("--dry-run", action="store_true",
                   help="Show which papers would be embedded without calling the API")

    # archive — write per-paper markdown files + INDEX.md (no LLM, reads DB only)
    p = sub.add_parser("archive", parents=[parent],
                       help="Write markdown archive (one .md per paper + INDEX.md)")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("paper_id", nargs="?", type=int, default=None,
                     help="Archive one paper")
    grp.add_argument("--all", action="store_true", dest="all_papers",
                     help="Re-render the whole archive")
    grp.add_argument("--index-only", action="store_true",
                     help="Just regenerate INDEX.md + _graph.json")
    p.add_argument("--archive-dir", default=None,
                   help="Override archive output directory (default: from config)")

    # fix-metadata — re-run metadata extraction on existing rows (no LLM)
    p = sub.add_parser("fix-metadata", parents=[parent],
                       help="Re-extract title/authors/abstract/DOI for existing paper(s)")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("paper_id", nargs="?", type=int, default=None,
                     help="Re-extract one paper")
    grp.add_argument("--all", action="store_true", dest="all_papers",
                     help="Re-extract every paper in the library")
    p.add_argument("--dry-run", action="store_true",
                   help="Show before/after diff without writing")
    p.add_argument("--re-embed-title", action="store_true",
                   help="Also recompute the title_embedding (costs 1 embed call per paper)")

    # classify-sections — rule-based section_type backfill (no LLM)
    p = sub.add_parser("classify-sections", parents=[parent],
                       help="Rule-classify sections.section_type (no LLM)")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("paper_id", nargs="?", type=int, default=None,
                     help="Classify sections for a single paper")
    grp.add_argument("--all", action="store_true", dest="all_papers",
                     help="Classify every section in the database")
    grp.add_argument("--missing", action="store_true",
                     help="Only classify rows where section_type IS NULL (default)")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing section_type values")
    p.add_argument("--dry-run", action="store_true",
                   help="Print breakdown without writing")
    p.add_argument("--show-other", action="store_true",
                   help="Print headings classified as 'other' (rule-coverage debug)")

    # analyze (Phase 2 LLM)
    p = sub.add_parser("analyze", parents=[parent], help="Run LLM analysis on paper(s)")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("paper_id", nargs="?", type=int, default=None,
                     help="Analyze a single paper by ID")
    grp.add_argument("--all", action="store_true", dest="all_papers",
                     help="Analyze every paper in the database")
    grp.add_argument("--missing", action="store_true",
                     help="Analyze only papers without llm_analyzed_at")
    p.add_argument("--skip-summary", action="store_true",
                   help="Skip paper-level summary generation")
    p.add_argument("--skip-refs", action="store_true",
                   help="Skip per-reference relevance scoring")
    p.add_argument("--skip-lit-review", action="store_true",
                   help="Skip literature-review viewpoint extraction")
    p.add_argument("--force", action="store_true",
                   help="Re-run even if already analyzed; replaces lit_review_entries")
    p.add_argument("--dry-run", action="store_true",
                   help="Print intended work without calling the LLM")

    return parser


# ── Command handlers ─────────────────────────────────────────────


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


def cmd_list(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    tag_repo = TagRepo(db)

    if args.tag:
        paper_ids = tag_repo.get_papers_by_tag(args.tag)
        papers = [paper_repo.get_by_id(pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]
    else:
        papers = paper_repo.list_papers(limit=args.limit, offset=args.offset)

    if args.as_json:
        data = [_paper_to_dict(p) for p in papers]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if not papers:
        print("No papers found.")
        return

    for p in papers:
        tags = tag_repo.get_tags_for_paper(p.id)
        tag_str = f" [{', '.join(t.name for t in tags)}]" if tags else ""
        year = ""
        if p.doi:
            year = f" DOI:{p.doi}"
        authors = ", ".join(p.authors[:3])
        if len(p.authors) > 3:
            authors += " et al."
        print(f"  [{p.id:>4}] {p.title[:70]}")
        print(f"         {authors}{year}{tag_str}")


def cmd_show(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    tag_repo = TagRepo(db)

    paper = paper_repo.get_by_id(args.paper_id)
    if not paper:
        print(f"Paper not found: {args.paper_id}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        data = _paper_to_dict(paper)
        if args.sections:
            secs = paper_repo.get_sections(paper.id)
            data["sections"] = [_section_to_dict(s) for s in secs]
        if args.references:
            refs = ref_repo.get_references_for_paper(paper.id)
            data["references"] = [_ref_to_dict(r) for r in refs]
        if args.citations:
            cits = paper_repo.get_citation_locations(paper.id)
            data["citation_locations"] = [_cit_to_dict(c) for c in cits]
        if args.llm:
            data["llm"] = {
                "analyzed_at": paper.llm_analyzed_at,
                "summary": paper.llm_summary,
                "research_field": paper.llm_research_field,
                "methodology": paper.llm_methodology,
                "key_findings": paper.llm_key_findings or [],
            }
            lr_repo = LitReviewRepo(db)
            entries = lr_repo.get_by_paper(paper.id)
            data["lit_review_entries"] = [
                {
                    "cited_ref_id": e.cited_ref_id,
                    "viewpoint": e.viewpoint,
                    "category": e.category,
                    "context": e.context,
                }
                for e in entries
            ]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    tags = tag_repo.get_tags_for_paper(paper.id)
    print(f"ID:           {paper.id}")
    print(f"Title:        {paper.title}")
    print(f"Authors:      {', '.join(paper.authors)}")
    print(f"DOI:          {paper.doi or 'N/A'}")
    print(f"Pages:        {paper.total_pages}")
    print(f"Abstract:     {paper.abstract[:200]}{'...' if len(paper.abstract) > 200 else ''}")
    print(f"Keywords:     {', '.join(paper.keywords)}")
    if tags:
        print(f"Tags:         {', '.join(t.name for t in tags)}")
    print(f"PDF:          {paper.pdf_path}")
    print(f"Ingested:     {paper.ingested_at}")

    if args.sections:
        secs = paper_repo.get_sections(paper.id)
        print(f"\n--- Sections ({len(secs)}) ---")
        for s in secs:
            indent = "  " * s.level
            print(f"  {indent}[L{s.level}] {s.heading} (pp.{s.page_start}-{s.page_end}, {len(s.paragraphs)} para)")

    if args.references:
        refs = ref_repo.get_references_for_paper(paper.id)
        print(f"\n--- References ({len(refs)}) ---")
        for r in refs:
            num = f"[{r.ref_number}]" if r.ref_number else "[-]"
            doi = f" DOI:{r.identifiers.get('DOI', '')}" if r.identifiers.get("DOI") else ""
            print(f"  {num} {r.title[:70]}{doi}")

    if args.citations:
        cits = paper_repo.get_citation_locations(paper.id)
        print(f"\n--- Citation Locations ({len(cits)}) ---")
        for c in cits:
            print(f"  p.{c.page} ({c.x:.0f},{c.y:.0f}) {c.text}: {c.sentence[:80]}...")

    if args.llm:
        print("\n--- LLM Analysis ---")
        if not paper.llm_analyzed_at:
            print("  (not analyzed yet — run `python cli.py analyze {}`)".format(paper.id))
        else:
            print(f"  Analyzed at:   {paper.llm_analyzed_at}")
            print(f"  Research field: {paper.llm_research_field or '-'}")
            print(f"  Summary:       {paper.llm_summary or '-'}")
            print(f"  Methodology:   {paper.llm_methodology or '-'}")
            findings = paper.llm_key_findings or []
            if findings:
                print(f"  Key findings ({len(findings)}):")
                for f in findings:
                    print(f"    • {f}")

        lr_repo = LitReviewRepo(db)
        entries = lr_repo.get_by_paper(paper.id)
        print(f"\n--- Lit Review Entries ({len(entries)}) ---")
        for e in entries:
            ref = next((r for r in ref_repo.get_references_for_paper(paper.id) if r.id == e.cited_ref_id), None)
            ref_title = ref.title[:55] if ref and ref.title else f"ref#{e.cited_ref_id}"
            print(f"  [{e.category}] → {ref_title}")
            print(f"     {e.viewpoint}")


def cmd_search(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)

    papers = paper_repo.search_papers(args.query, limit=args.limit, field=args.field)

    if args.as_json:
        print(json.dumps([_paper_to_dict(p) for p in papers], ensure_ascii=False, indent=2))
        return

    if not papers:
        print(f'No results for "{args.query}"')
        return

    print(f'Found {len(papers)} result(s) for "{args.query}":')
    for p in papers:
        authors = ", ".join(p.authors[:2])
        if len(p.authors) > 2:
            authors += " et al."
        print(f"  [{p.id:>4}] {p.title[:70]}")
        print(f"         {authors}")


def cmd_delete(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    paper = paper_repo.get_by_id(args.paper_id)
    if not paper:
        print(f"Paper not found: {args.paper_id}", file=sys.stderr)
        sys.exit(1)

    if not args.force:
        answer = input(f"Delete [{paper.id}] {paper.title[:60]}? [y/N] ")
        if answer.lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    paper_repo.delete_paper(args.paper_id)
    print(f"Deleted paper {args.paper_id}.")
    # Knowledge graph: cascade has already dropped the paper_local node;
    # this call sweeps now-orphaned keyword / research_field / paper_external nodes.
    try:
        from knowledge_graph import KGBuilder
        res = KGBuilder(db).remove_paper(args.paper_id)
        orph = res.get("orphans_removed") or {}
        if any(orph.values()):
            print(f"  KG: swept orphans {orph}")
    except Exception as e:
        print(f"  WARN: kg cleanup failed: {e}", file=sys.stderr)


def cmd_stats(args, db: DatabaseConnection) -> None:
    from db.shared_citations_repo import SharedCitationsRepo
    paper_repo = PaperRepo(db)
    stats = paper_repo.get_stats()
    emb_stats = paper_repo.get_embedding_stats()
    stats.update({f"emb_{k}": v for k, v in emb_stats.items()})
    sc_repo = SharedCitationsRepo(db)
    stats["shared_citation_rows"] = sc_repo.count()
    stats["shared_citation_pairs"] = sc_repo.count_pairs()

    from knowledge_graph import KGBuilder
    kg_stats = KGBuilder(db).stats()
    stats["kg_nodes"] = kg_stats["nodes"]
    stats["kg_edges"] = kg_stats["edges"]

    if args.as_json:
        print(json.dumps(stats, indent=2))
        return

    print("Database Statistics:")
    print(f"  Papers:             {stats['papers']}")
    print(f"  Sections:           {stats['sections']}")
    print(f"  References:         {stats['references']}")
    print(f"  Citation Locations: {stats['citation_locations']}")
    print(f"  Tags:               {stats['tags']}")
    print(f"  Embeddings:")
    print(f"    Title       : {emb_stats['papers_with_title_emb']}/{stats['papers']}")
    print(f"    Abstract    : {emb_stats['papers_with_abstract_emb']}/{stats['papers']}")
    print(f"    Fulltext    : {emb_stats['papers_with_fulltext_emb']}/{stats['papers']}")
    print(f"    Sections    : {emb_stats['sections_with_emb']}/{stats['sections']}")
    print(f"    Chunks      : {emb_stats['section_chunks_with_emb']}/{emb_stats['section_chunks_total']}")
    print(f"    References  : {emb_stats['references_with_emb']}/{stats['references']}")
    print(f"  Shared citations: {stats['shared_citation_rows']} rows across "
          f"{stats['shared_citation_pairs']} pair(s)")
    kg_nodes = stats.get("kg_nodes") or {}
    kg_edges = stats.get("kg_edges") or {}
    if sum(kg_nodes.values()) or sum(kg_edges.values()):
        print(f"  Knowledge graph:")
        print(f"    Paper nodes  : local={kg_nodes.get('paper_local', 0)}, "
              f"external={kg_nodes.get('paper_external', 0)}")
        print(f"    Keyword nodes: {kg_nodes.get('keyword', 0)}")
        print(f"    Field nodes  : {kg_nodes.get('research_field', 0)}")
        print(f"    Edges        : "
              f"has_keyword={kg_edges.get('has_keyword', 0)}, "
              f"in_field={kg_edges.get('in_field', 0)}, "
              f"cites={kg_edges.get('cites', 0)}, "
              f"cross_cites={kg_edges.get('cross_cites', 0)}")
    else:
        print(f"  Knowledge graph: empty  (run `kg build` to populate)")


def cmd_export(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    tag_repo = TagRepo(db)

    if args.paper_id:
        paper = paper_repo.get_by_id(args.paper_id)
        if not paper:
            print(f"Paper not found: {args.paper_id}", file=sys.stderr)
            sys.exit(1)
        papers = [paper]
    elif args.tag:
        paper_ids = tag_repo.get_papers_by_tag(args.tag)
        papers = [paper_repo.get_by_id(pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]
    else:
        papers = paper_repo.list_papers(limit=10000)

    data = []
    for p in papers:
        d = _paper_to_dict(p)
        d["sections"] = [_section_to_dict(s) for s in paper_repo.get_sections(p.id)]
        d["references"] = [_ref_to_dict(r) for r in ref_repo.get_references_for_paper(p.id)]
        d["tags"] = [t.name for t in tag_repo.get_tags_for_paper(p.id)]
        data.append(d)

    json_str = json.dumps(data, ensure_ascii=False, indent=None if args.compact else 2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Exported {len(data)} paper(s) to {args.output}")
    else:
        print(json_str)


def cmd_tag(args, db: DatabaseConnection) -> None:
    tag_repo = TagRepo(db)

    if args.tag_action == "add":
        tag_repo.tag_paper(args.paper_id, args.tag_name)
        print(f"Tagged paper {args.paper_id} with '{args.tag_name}'")
    elif args.tag_action == "remove":
        tag_repo.untag_paper(args.paper_id, args.tag_name)
        print(f"Removed tag '{args.tag_name}' from paper {args.paper_id}")
    elif args.tag_action == "list":
        tags = tag_repo.list_tags()
        if not tags:
            print("No tags.")
            return
        for t in tags:
            paper_ids = tag_repo.get_papers_by_tag(t.name)
            print(f"  {t.name} ({len(paper_ids)} papers)")
    else:
        print("Usage: paperdb tag {add|remove|list}", file=sys.stderr)


def cmd_init_db(args, db: DatabaseConnection) -> None:
    init_schema(db)
    print(f"Database initialized at {db.db_path}")


def cmd_archive(args, db: DatabaseConnection) -> None:
    """Render markdown files of the DB content to a browseable folder."""
    from config import get_config
    from archive_writer import (
        paper_filename, write_all, write_index, write_paper,
    )

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
        # also refresh index since paper changed
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
    import json as _json
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

        # Write the updated metadata row
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


def cmd_classify_sections(args, db: DatabaseConnection) -> None:
    """Rule-based backfill for sections.section_type. No LLM involved."""
    from section_classifier import classify_section_type, SECTION_TYPES

    # Default mode is --missing if no scope flag given
    if not args.paper_id and not args.all_papers and not args.missing:
        args.missing = True

    where_clauses = []
    params: list = []
    if args.paper_id is not None:
        where_clauses.append("paper_id = ?")
        params.append(args.paper_id)
    if args.missing and not args.force:
        where_clauses.append("section_type IS NULL")
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    rows = db.conn.execute(
        f"SELECT id, heading FROM sections{where_sql} ORDER BY paper_id, sort_order",
        params,
    ).fetchall()

    if not rows:
        print("No sections to classify.")
        return

    counts: dict[str, int] = {t: 0 for t in SECTION_TYPES}
    other_headings: list[str] = []
    updates: list[tuple[str, int]] = []
    for r in rows:
        st = classify_section_type(r["heading"] or "")
        counts[st] += 1
        if st == "other":
            other_headings.append(r["heading"] or "")
        updates.append((st, r["id"]))

    total = len(updates)
    matched = total - counts["other"]
    coverage_pct = (matched / total * 100) if total else 0.0

    print(f"Scanned {total} section(s). Coverage: {matched}/{total} = {coverage_pct:.1f}%")
    print("Breakdown:")
    for t in SECTION_TYPES:
        n = counts[t]
        if n:
            print(f"  {t:<16} {n}")

    if args.show_other and other_headings:
        print(f"\nHeadings classified as 'other' ({len(other_headings)}):")
        for h in other_headings[:50]:
            print(f"  {h!r}")
        if len(other_headings) > 50:
            print(f"  ... and {len(other_headings) - 50} more")

    if args.dry_run:
        print("\n[dry-run] no changes written")
        return

    with db.transaction() as cur:
        cur.executemany(
            "UPDATE sections SET section_type = ? WHERE id = ?",
            updates,
        )
    print(f"\nWrote section_type for {total} section(s).")


# ── config sub-commands ─────────────────────────────────────────


_CONFIG_TEMPLATE = '''\
# paperdb configuration
# Generated by `paperdb config init`. Edit and save.
#
# Real environment variables (LLM_API_KEY etc.) always override values here.
# File permission is 0600 (owner read/write only) — your API keys stay private.

[paths]
# Override default data directory (auto-derived from OS conventions otherwise).
# data_dir = "~/Library/Application Support/paperdb"
# db_path  = "~/Library/Application Support/paperdb/papers.db"
# pdf_dir  = "~/Library/Application Support/paperdb/pdfs"

[runtime]
# verbose = false

[llm]
# REQUIRED if you want LLM features (analyze / embed / agent / search rerank).
provider = "deepseek"
api_key = ""                    # ← paste your DeepSeek API key here
embedding_api_key = ""          # ← paste your DashScope (Qwen) API key here

# Optional — defaults shown commented out
# base_url = "https://api.deepseek.com/v1"
# complex_model = "deepseek-v4-pro"        # complex tier (thinking ON)
# simple_model  = "deepseek-v4-flash"      # simple tier (thinking OFF)
# complex_thinking = "enabled"
# simple_thinking  = "disabled"
# complex_reasoning_effort = "high"        # "high" | "max"

# Embedding (Qwen) — defaults route to DashScope International
# embedding_model = "text-embedding-v4"
# embedding_base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
# embedding_dimensions = 1024              # 64 | 128 | 256 | 512 | 768 | 1024 | 1536 | 2048
# embedding_batch_size = 10                # Qwen v4 caps at 10

# Per-agent tier routing (which agent uses complex vs simple)
# tier_analyze_paper = "complex"
# tier_score_refs    = "simple"
# tier_lit_review    = "complex"
# tier_rerank        = "simple"
'''


def cmd_config(args, db=None) -> None:
    from pathlib import Path as _Path
    from config import (
        default_config_path, default_data_dir, default_db_path, default_pdf_dir,
        bootstrap_env_from_config_file,
    )
    import os as _os

    action = getattr(args, "config_action", None)

    if action == "path":
        print(default_config_path())
        return

    if action == "init":
        path = _Path(args.path) if args.path else default_config_path()
        if path.exists() and not args.force:
            print(f"Config file already exists: {path}", file=sys.stderr)
            print("Use --force to overwrite, or edit it directly.", file=sys.stderr)
            sys.exit(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass  # Windows / non-POSIX
        print(f"Wrote template config to:  {path}")
        print()
        print("Next steps:")
        print(f"  1. Open in your editor:    open '{path}'   (or use vim/nano/etc.)")
        print("  2. Fill in api_key and embedding_api_key")
        print("  3. Save the file")
        print("  4. Verify with:            paperdb config show")
        print("  5. Launch the app:         paperdb start")
        return

    if action == "show":
        from config import get_env_vars_from_config_file, resolve_workspace, get_config
        ws, ws_source = resolve_workspace()
        cfg = get_config()
        path = cfg.config_file_path()
        injected = get_env_vars_from_config_file()
        print("══════════════════════════════════════════════════════════════════")
        print(f"  Active workspace : {ws}")
        print(f"                     (source: {ws_source})")
        print(f"  Config file      : {path}")
        print(f"                     {'(exists)' if path.is_file() else '(MISSING — run: paperdb init)'}")
        print("══════════════════════════════════════════════════════════════════")
        print(f"Database         : {cfg.db_path}")
        print(f"PDF dir          : {cfg.pdf_dir}")
        print(f"Archive dir      : {cfg.archive_dir}")
        print(f"Cache dir        : {cfg.cache_dir}")
        print()
        print("LLM environment (after config file applied):")
        sensitive = {"LLM_API_KEY", "LLM_EMBEDDING_API_KEY"}
        env_keys = [
            "LLM_PROVIDER", "LLM_BASE_URL",
            "LLM_API_KEY", "LLM_EMBEDDING_API_KEY",
            "LLM_COMPLEX_MODEL", "LLM_SIMPLE_MODEL",
            "LLM_COMPLEX_THINKING", "LLM_SIMPLE_THINKING",
            "LLM_COMPLEX_REASONING_EFFORT",
            "LLM_EMBEDDING_MODEL", "LLM_EMBEDDING_DIMENSIONS",
        ]
        for k in env_keys:
            v = _os.environ.get(k, "")
            tag = "(from config file)" if k in injected else (
                "(from real env)" if v else ""
            )
            display = v
            if k in sensitive and v:
                display = v[:6] + "…" + v[-4:] if len(v) > 12 else "(set)"
            print(f"  {k:<32} = {display or '(unset)'}  {tag}")
        return

    print("Usage: paperdb config {init|show|path}", file=sys.stderr)
    sys.exit(1)


def cmd_start(args, db=None) -> None:
    """Launch paperdb-api + paperdb-ui together. Ctrl-C kills both."""
    import shutil
    import signal
    import socket
    import subprocess
    import time

    # Find executables — when installed via console_scripts they live on PATH
    api_exe = shutil.which("paperdb-api")
    ui_exe = shutil.which("paperdb-ui") if not args.no_ui else None
    if api_exe is None:
        # Fallback to module invocation
        api_cmd = [sys.executable, "-m", "paperdb_api.server"]
    else:
        api_cmd = [api_exe]
    api_cmd += ["--host", args.host, "--port", str(args.api_port)]

    if not args.no_ui:
        if ui_exe is None:
            ui_cmd = [sys.executable, "-m", "paperdb_ui.launcher"]
        else:
            ui_cmd = [ui_exe]
        ui_cmd += ["--host", args.host, "--port", str(args.ui_port),
                    "--api-url", f"http://localhost:{args.api_port}"]
    else:
        ui_cmd = None

    print(f"Starting paperdb-api on {args.host}:{args.api_port} ...")
    api_proc = subprocess.Popen(api_cmd)

    # Wait for /healthz to come up (up to 20s)
    ok = False
    for _ in range(40):
        if api_proc.poll() is not None:
            print(f"\nAPI process exited prematurely (code {api_proc.returncode}). "
                   "Check the logs above.", file=sys.stderr)
            sys.exit(1)
        try:
            with socket.create_connection(("localhost", args.api_port), timeout=0.4):
                # connect succeeded; still wait one extra cycle so uvicorn binds
                ok = True
                break
        except OSError:
            time.sleep(0.5)
    if not ok:
        api_proc.terminate()
        print("\nAPI did not start within 20s. Aborting.", file=sys.stderr)
        sys.exit(1)
    print("✓ API is up.")

    ui_proc = None
    if ui_cmd is not None:
        print(f"Starting paperdb-ui on {args.host}:{args.ui_port} ...")
        ui_proc = subprocess.Popen(ui_cmd)
        print(f"\nOpen in browser:  http://localhost:{args.ui_port}\n")
    else:
        print(f"\nAPI ready at:     http://localhost:{args.api_port}\n")

    # ── Shutdown handling ────────────────────────────────────────
    stop = {"flag": False}

    def handle_sigint(signum, frame):
        if stop["flag"]:
            return
        stop["flag"] = True
        print("\nShutting down ...")
        for proc in (ui_proc, api_proc):
            if proc and proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    # Block until either dies; if one dies, kill the other
    try:
        while True:
            time.sleep(0.5)
            if api_proc.poll() is not None:
                print(f"\nAPI exited (code {api_proc.returncode}). Stopping UI.",
                      file=sys.stderr)
                if ui_proc and ui_proc.poll() is None:
                    ui_proc.terminate()
                break
            if ui_proc is not None and ui_proc.poll() is not None:
                print(f"\nUI exited (code {ui_proc.returncode}). Stopping API.",
                      file=sys.stderr)
                api_proc.terminate()
                break
    finally:
        for proc in (ui_proc, api_proc):
            if proc and proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("Done.")


def cmd_agent(args, db: DatabaseConnection) -> None:
    from llm_interface import LLMConfig, get_llm_provider, OpenAICompatibleProvider
    from embedder import PaperEmbedder
    from agents.tools import ToolContext
    from agents.orchestrator import Orchestrator

    try:
        cfg = LLMConfig.from_env()
        provider = get_llm_provider(cfg)
    except Exception as e:
        print(f"LLM init failed: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(provider, OpenAICompatibleProvider):
        print("Agent requires LLM_PROVIDER=deepseek (or openai-compatible). "
              f"Got: {cfg.provider}", file=sys.stderr)
        sys.exit(2)

    ctx = ToolContext(db=db, llm=provider, embedder=PaperEmbedder(provider))
    orch = Orchestrator(ctx)

    task = args.task
    if task in ("lit-review", "qa", "compare", "gap"):
        agent_name = {"lit-review": "lit_review", "qa": "qa",
                       "compare": "compare", "gap": "gap"}[task]
        print(f"Running specialist directly: {agent_name}")
        result = orch.run_single_specialist(agent_name, args.query, verbose=args.verbose)
        if args.show_trace:
            _print_trace(result)
        print("\n--- specialist output ---\n")
        print(result.output_text)
        return

    # auto: full orchestration
    print(f"Orchestrating: {args.query!r}")
    out = orch.run(args.query, verbose=args.verbose)

    if args.show_plan:
        print("\n--- orchestrator plan ---")
        for i, step in enumerate(out.plan, 1):
            print(f"  {i}. {step['agent']:<11} → {step['sub_query']}")
        print(f"  synth_instruction: {out.synth_instruction}")

    if args.show_trace:
        for s in out.specialists:
            _print_trace(s)

    if args.no_synth:
        print("\n--- specialist outputs (no synth) ---")
        for s in out.specialists:
            print(f"\n=== {s.agent_name} ({s.sub_query[:60]}) {'✓' if s.ok else '✗'} ===")
            print(s.output_text)
        return

    print("\n" + "=" * 70)
    print(out.final_answer)
    print("=" * 70)


def _print_trace(specialist_result) -> None:
    t = specialist_result.trace
    print(f"\n--- trace: {specialist_result.agent_name} ---")
    print(f"  iterations: {t.iterations}")
    print(f"  tool calls: {len(t.tool_calls)}")
    for tc in t.tool_calls:
        arg_brief = json.dumps(tc["args"], ensure_ascii=False)[:60]
        print(f"    • {tc['name']}({arg_brief})  {tc['ms']}ms")
    if t.error:
        print(f"  error: {t.error}")


def cmd_kg(args, db: DatabaseConnection) -> None:
    from knowledge_graph import KGBuilder, _normalize_keyword
    kg = KGBuilder(db)
    action = args.kg_action or "stats"

    if action == "build":
        if args.paper_id:
            res = kg.add_paper(args.paper_id)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return
        if args.verbose and not args.rebuild:
            print("Syncing all papers into the knowledge graph (incremental)...")
        res = kg.build_all(rebuild=args.rebuild)
        print(f"Processed {res['papers']} paper(s).")
        orph = res["orphans_removed"]
        if any(orph.values()):
            print(f"Swept orphans: {orph}")
        print()
        _print_kg_stats(kg.stats())
        return

    if action == "stats":
        _print_kg_stats(kg.stats())
        return

    if action == "promote":
        res = kg.promote_externals()
        print(f"Promoted {res['promoted']} paper_external node(s) to paper_local.")
        return

    if action == "neighbors":
        if args.paper_id:
            _show_paper_neighbors(db, args.paper_id, top=args.top)
        else:
            _show_term_neighbors(db, args.term, depth=args.depth, top=args.top)
        return

    if action == "search":
        from graph_retrieval import GraphRetriever
        gr = GraphRetriever(db)
        if args.explain:
            exp = gr.explain(args.query)
            print(f"normalized query: {exp['query_normalized']!r}")
            if not exp["anchors"]:
                print("no anchors found — graph won't return anything")
                return
            print(f"anchors ({len(exp['anchors'])}):")
            for a in exp["anchors"]:
                print(f"  [{a['node_type']}] {a['display_name']:<40} "
                      f"relevance={a['relevance']:.2f} "
                      f"papers={len(a['paper_ids'])}")
            print()
        hits = gr.search(args.query, k=args.top, depth=args.depth)
        if not hits:
            print("(no matches)")
        else:
            paper_repo = PaperRepo(db)
            print(f"Graph hits (top {len(hits)}):")
            for pid, score in hits:
                p = paper_repo.get_by_id(pid)
                title = (p.title if p else "?")[:80]
                print(f"  [{pid}] score={score:.3f}  {title}")
        if args.include_external:
            recs = gr.search_external_recommendations(args.query, k=args.top)
            print()
            if not recs:
                print("(no external recommendations)")
            else:
                print(f"External recommendations (top {len(recs)} most-cited by anchor matches):")
                for r in recs:
                    doi = f"  doi:{r['doi']}" if r["doi"] else ""
                    print(f"  cited {r['cite_count']}x  {r['title'][:70]}{doi}")
        return

    print("Use: kg {build|stats|neighbors|promote|search}", file=sys.stderr)
    sys.exit(2)


def _print_kg_stats(stats: dict) -> None:
    nodes = stats.get("nodes", {})
    edges = stats.get("edges", {})
    total_nodes = sum(nodes.values())
    total_edges = sum(edges.values())
    print("Knowledge Graph:")
    print(f"  Nodes ({total_nodes} total):")
    # Show in a stable order; paper_local / paper_external first to make the
    # local-vs-external split visible at a glance.
    order = ["paper_local", "paper_external", "keyword", "research_field", "author"]
    for k in order:
        if k in nodes:
            print(f"    {k:<16} {nodes[k]}")
    for k, v in nodes.items():
        if k not in order:
            print(f"    {k:<16} {v}")
    print(f"  Edges ({total_edges} total):")
    for k, v in sorted(edges.items()):
        print(f"    {k:<16} {v}")


def _show_term_neighbors(db: DatabaseConnection, term: str, *, depth: int, top: int) -> None:
    from knowledge_graph import _normalize_keyword
    norm = _normalize_keyword(term)
    if not norm:
        print(f"Empty term.", file=sys.stderr)
        sys.exit(2)
    rows = db.conn.execute(
        "SELECT id, node_type, display_name FROM kg_nodes "
        "WHERE node_type IN ('keyword', 'research_field') AND name = ?",
        (norm,),
    ).fetchall()
    if not rows:
        print(f"No node found for '{term}'.")
        return
    for r in rows:
        _print_term_node_neighbors(db, r["id"], r["node_type"], r["display_name"],
                                    depth=depth, top=top)


def _print_term_node_neighbors(db: DatabaseConnection, node_id: int, node_type: str,
                                display: str, *, depth: int, top: int) -> None:
    edge_type = "has_keyword" if node_type == "keyword" else "in_field"

    # Papers having this term (paper_local nodes pointing here)
    paper_rows = db.conn.execute(f"""
        SELECT n.paper_id, n.display_name FROM kg_edges e
        JOIN kg_nodes n ON n.id = e.src_id
        WHERE e.dst_id = ? AND e.edge_type = '{edge_type}'
        ORDER BY n.paper_id
        LIMIT ?
    """, (node_id, top)).fetchall()
    total_papers = db.conn.execute(
        f"SELECT COUNT(*) AS n FROM kg_edges WHERE dst_id=? AND edge_type='{edge_type}'",
        (node_id,),
    ).fetchone()["n"]

    print(f"{node_type}: {display}  ({total_papers} paper(s))")
    print(f"  papers (top {min(top, total_papers)}):")
    for r in paper_rows:
        print(f"    [{r['paper_id']}] {r['display_name'][:80]}")

    if depth >= 1 and node_type == "keyword":
        # Co-occurring keywords (derived from has_keyword JOIN)
        co_rows = db.conn.execute("""
            SELECT n.display_name AS kw, COUNT(*) AS cnt
            FROM kg_edges e1
            JOIN kg_edges e2 ON e1.src_id = e2.src_id
            JOIN kg_nodes n ON n.id = e2.dst_id
            WHERE e1.dst_id = ? AND e1.edge_type='has_keyword'
              AND e2.edge_type='has_keyword' AND e2.dst_id != ?
            GROUP BY e2.dst_id
            ORDER BY cnt DESC, n.display_name
            LIMIT ?
        """, (node_id, node_id, top)).fetchall()
        if co_rows:
            print(f"  co-occurring keywords (top {len(co_rows)}):")
            for r in co_rows:
                print(f"    {r['kw']:<30} ({r['cnt']} co-occurrence(s))")

    if depth >= 1 and node_type == "research_field":
        kw_rows = db.conn.execute("""
            SELECT n.display_name AS kw, COUNT(*) AS cnt
            FROM kg_edges e1
            JOIN kg_edges e2 ON e1.src_id = e2.src_id
            JOIN kg_nodes n ON n.id = e2.dst_id
            WHERE e1.dst_id = ? AND e1.edge_type='in_field'
              AND e2.edge_type='has_keyword'
            GROUP BY e2.dst_id
            ORDER BY cnt DESC, n.display_name
            LIMIT ?
        """, (node_id, top)).fetchall()
        if kw_rows:
            print(f"  keywords in this field (top {len(kw_rows)}):")
            for r in kw_rows:
                print(f"    {r['kw']:<30} ({r['cnt']} paper(s))")


def _show_paper_neighbors(db: DatabaseConnection, paper_id: int, *, top: int) -> None:
    local = db.conn.execute(
        "SELECT id, display_name FROM kg_nodes "
        "WHERE node_type='paper_local' AND paper_id = ?",
        (paper_id,),
    ).fetchone()
    if not local:
        print(f"No paper_local node for paper {paper_id}. Run `kg build --paper-id {paper_id}`.")
        return
    print(f"paper_local [{paper_id}]: {local['display_name']}")

    def fetch_dst(edge_type: str, dst_type: Optional[str] = None) -> list:
        sql = ("SELECT n.display_name AS name, n.node_type AS t, n.paper_id AS pid "
               "FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst_id "
               "WHERE e.src_id=? AND e.edge_type=?")
        params = [local["id"], edge_type]
        if dst_type:
            sql += " AND n.node_type=?"
            params.append(dst_type)
        sql += f" ORDER BY n.display_name LIMIT ?"
        params.append(top)
        return db.conn.execute(sql, params).fetchall()

    kws = fetch_dst("has_keyword")
    if kws:
        print(f"  keywords: {', '.join(k['name'] for k in kws)}")
    fields = fetch_dst("in_field")
    if fields:
        print(f"  research_field: {', '.join(k['name'] for k in fields)}")

    crosses = fetch_dst("cross_cites")
    if crosses:
        print(f"  cross_cites → ({len(crosses)} local paper(s)):")
        for r in crosses:
            print(f"    [{r['pid']}] {r['name'][:80]}")
    cites = fetch_dst("cites")
    if cites:
        print(f"  cites → ({len(cites)} external paper(s)):")
        for r in cites:
            print(f"    (ext) {r['name'][:80]}")

    incoming = db.conn.execute(
        "SELECT n.paper_id AS pid, n.display_name AS name, e.edge_type "
        "FROM kg_edges e JOIN kg_nodes n ON n.id = e.src_id "
        "WHERE e.dst_id = ? AND e.edge_type='cross_cites' "
        "ORDER BY n.paper_id LIMIT ?",
        (local["id"], top),
    ).fetchall()
    if incoming:
        print(f"  cited by ({len(incoming)} local paper(s)):")
        for r in incoming:
            print(f"    [{r['pid']}] {r['name'][:80]}")


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
        # Relationship is uniform across rows (set by update_relationship_for_pair)
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

    # Build query embedding only if a vector path is needed
    query_vec = None
    use_vector = resolved_mode in ("hybrid", "vector", "all")
    use_fts = resolved_mode in ("hybrid", "fts", "all")
    use_graph = resolved_mode in ("graph", "all")

    if use_vector:
        if retriever.embedded_paper_count == 0:
            print("No papers with embeddings yet. Run `cli.py embed --all` first, "
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


def cmd_analyze(args, db: DatabaseConnection) -> None:
    paper_repo = PaperRepo(db)
    ref_repo = ReferenceRepo(db)
    lr_repo = LitReviewRepo(db)

    # Resolve target paper list
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

    # Filter out already-analyzed unless --force
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

    # Lazy-import LLM modules so non-analyze commands don't need openai installed
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

            # Stage 1: paper summary
            if not args.skip_summary:
                analysis = analyzer.analyze_paper(p, sections)
                paper_repo.update_llm_fields(
                    p.id,
                    summary=analysis.summary,
                    research_field=analysis.research_field,
                    methodology=analysis.methodology,
                    key_findings=analysis.key_findings,
                )
                # Refresh in-memory paper so downstream stages see the field
                p.llm_research_field = analysis.research_field
                print(f"   summary: {analysis.summary[:120]}{'…' if len(analysis.summary) > 120 else ''}")
                print(f"   field:   {analysis.research_field}")
                print(f"   findings: {len(analysis.key_findings)} item(s)")

            # Stage 2: reference scoring
            if not args.skip_refs and references:
                scores = analyzer.score_references(p, references)
                updates = [(s.ref_id, s.relevance_score, s.relationship) for s in scores]
                ref_repo.bulk_update_llm_fields(updates)
                print(f"   scored {len(updates)} reference(s)")

            # Stage 3: lit review viewpoints
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


# ── Serialization helpers ────────────────────────────────────────


def _paper_to_dict(p) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "authors": p.authors,
        "affiliations": p.affiliations,
        "abstract": p.abstract,
        "keywords": p.keywords,
        "doi": p.doi,
        "total_pages": p.total_pages,
        "pdf_path": p.pdf_path,
        "ingested_at": p.ingested_at,
    }


def _section_to_dict(s) -> dict:
    return {
        "heading": s.heading,
        "level": s.level,
        "paragraphs": s.paragraphs,
        "page_start": s.page_start,
        "page_end": s.page_end,
    }


def _ref_to_dict(r) -> dict:
    return {
        "ref_number": r.ref_number,
        "title": r.title,
        "authors": r.authors,
        "year": r.year,
        "identifiers": r.identifiers,
        "url": r.url,
        "type": r.type,
    }


def _cit_to_dict(c) -> dict:
    return {
        "page": c.page,
        "x": c.x,
        "y": c.y,
        "text": c.text,
        "sentence": c.sentence,
    }


# ── Main entry point ────────────────────────────────────────────


def cmd_init(args, db=None) -> None:
    """Bootstrap a new paperdb workspace."""
    from workspace_ops import init_workspace
    from config import default_workspace_dir

    target = Path(args.path).expanduser() if args.path else default_workspace_dir()
    print(f"Initializing paperdb workspace at: {target}\n")
    result = init_workspace(target, overwrite_config=args.force_config)
    cfg_path = result["config_path"]
    print(f"\n✓ Workspace ready: {target}")
    print(f"  Next steps:")
    print(f"    1. Edit {cfg_path}  and paste your API keys")
    print(f"    2. paperdb ingest path/to/paper.pdf")
    print(f"    3. paperdb start                 # launches API + Streamlit UI")
    print(f"\nTo use this workspace by default in a different shell, run:")
    print(f"    export PAPERDB_WORKSPACE={target}")


def cmd_workspace(args, db=None) -> None:
    """Inspect or migrate the workspace."""
    if args.workspace_action == "show":
        from workspace_ops import inspect_workspace
        info = inspect_workspace()
        if getattr(args, "as_json", False):
            print(json.dumps(info, indent=2, ensure_ascii=False))
            return
        print(f"Workspace : {info['workspace']}  (source: {info['workspace_source']})")
        print(f"Config    : {info['config_file']}  (source: {info['config_source']})")
        print(f"\nLayout:")
        for k, exists in info["exists"].items():
            mark = "✓" if exists else "·"
            print(f"  {mark}  {k}")
        if "db_size_bytes" in info:
            mb = info["db_size_bytes"] / 1024 / 1024
            print(f"\npapers.db: {mb:.1f} MB")
        if info["legacy_detected"]:
            print(f"\n⚠ Legacy data found in old OS directories:")
            for kind, p in info["legacy_detected"].items():
                print(f"    {kind:>8}: {p}")
            print(f"  Run `paperdb workspace migrate` to consolidate.")
        return

    if args.workspace_action == "migrate":
        from workspace_ops import plan_migration, execute_migration
        from config import default_workspace_dir

        target = Path(args.to).expanduser() if args.to else default_workspace_dir()
        plan = plan_migration(target)
        if plan.is_empty():
            print(f"No legacy data found — nothing to migrate.")
            print(f"(checked legacy OS paths; workspace target was {target})")
            return

        print(plan.summary())
        print()

        if args.dry_run:
            print("(--dry-run: no files moved)")
            return

        if not args.yes:
            reply = input(f"Proceed with migration? [y/N]: ").strip().lower()
            if reply not in ("y", "yes"):
                print("Aborted.")
                return

        execute_migration(plan, archive_old=not args.no_archive)
        return

    print("Usage: paperdb workspace {show|migrate}")
    sys.exit(2)


def main() -> None:
    # Populate LLM_* env vars from config.toml (workspace or legacy) before
    # anything else reads os.environ. Real env vars win.
    from config import (
        AppConfig,
        bootstrap_env_from_config_file,
        set_config,
        default_config_path,
    )

    parser = build_parser()
    args = parser.parse_args()

    # --workspace flag wins over env / marker / default. Set the env var so
    # any downstream module that re-reads config sees the same value.
    if getattr(args, "workspace", None):
        os.environ["PAPERDB_WORKSPACE"] = str(Path(args.workspace).expanduser())

    # Build the active config (uses --workspace via env we just set above).
    cfg = AppConfig.from_env_and_file()
    set_config(cfg)
    bootstrap_env_from_config_file(cfg.config_file_path())

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Bootstrap-only commands that don't need DB
    if args.command == "config":
        cmd_config(args)
        return
    if args.command == "start":
        cmd_start(args)
        return
    if args.command == "init":
        cmd_init(args)
        return
    if args.command == "workspace":
        cmd_workspace(args)
        return

    db_path = Path(args.db) if args.db else cfg.db_path
    with DatabaseConnection(db_path) as db:
        init_schema(db)

        dispatch = {
            "ingest": cmd_ingest,
            "list": cmd_list,
            "show": cmd_show,
            "search": cmd_search,
            "delete": cmd_delete,
            "stats": cmd_stats,
            "export": cmd_export,
            "tag": cmd_tag,
            "init-db": cmd_init_db,
            "archive": cmd_archive,
            "fix-metadata": cmd_fix_metadata,
            "classify-sections": cmd_classify_sections,
            "analyze": cmd_analyze,
            "embed": cmd_embed,
            "search-rag": cmd_search_rag,
            "cross-analyze": cmd_cross_analyze,
            "kg": cmd_kg,
            "agent": cmd_agent,
        }
        dispatch[args.command](args, db)


if __name__ == "__main__":
    main()
