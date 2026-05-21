"""Argparse spec for the `paperdb` CLI.

Kept as one module because argparse's subparser model is awkward to split
across files (sharing the `parent` and `subparsers` objects), and because
the flags themselves are stable enough that one place to read them is a
feature, not a liability.

If you add a new subcommand: register the parser here, then add a `cmd_*`
function in `cli/commands/*.py` and wire it in `cli/main_entry.DISPATCH`.
"""

from __future__ import annotations

import argparse


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
    p.add_argument("--year-from", type=int,
                   help="Filter by publication_year >= (falls back to ingested_at year "
                        "for papers that haven't been LLM-analyzed yet)")
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
