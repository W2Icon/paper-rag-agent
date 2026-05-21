"""`paperdb` entry point — parses args, bootstraps config, dispatches subcommands."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from db_connection import DatabaseConnection
from schema import init_schema

from paperdb_cli.parser import build_parser

# DB-backed subcommands. Bootstrap-only commands (config/init/start/workspace)
# are handled before the DB connection opens so they work without a real db
# directory.
from paperdb_cli.commands.analyze import cmd_analyze
from paperdb_cli.commands.archive import cmd_archive, cmd_fix_metadata
from paperdb_cli.commands.agent import cmd_agent
from paperdb_cli.commands.classify import cmd_classify_sections
from paperdb_cli.commands.cross_analyze import cmd_cross_analyze
from paperdb_cli.commands.embed import cmd_embed
from paperdb_cli.commands.ingest import cmd_ingest
from paperdb_cli.commands.kg import cmd_kg
from paperdb_cli.commands.library import (
    cmd_delete, cmd_export, cmd_init_db, cmd_list, cmd_search, cmd_show,
    cmd_stats, cmd_tag,
)
from paperdb_cli.commands.search_rag import cmd_search_rag

from paperdb_cli.commands.config_cmds import (
    cmd_config, cmd_init, cmd_start, cmd_workspace,
)


DISPATCH: dict[str, callable] = {
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


def main() -> None:
    # Populate LLM_* env vars from config.toml (workspace or legacy) before
    # anything else reads os.environ. Real env vars always win.
    from config import AppConfig, bootstrap_env_from_config_file, set_config

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

    # Bootstrap-only commands that don't need a DB connection
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
        handler = DISPATCH.get(args.command)
        if handler is None:
            parser.print_help()
            sys.exit(1)
        handler(args, db)
