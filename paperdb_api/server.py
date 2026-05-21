"""Console-script entrypoint for `paperdb-api`."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    # Bootstrap LLM_* env vars from the TOML config file before uvicorn
    # imports the app (which calls LLMConfig.from_env()).
    from config import bootstrap_env_from_config_file
    injected = bootstrap_env_from_config_file()

    parser = argparse.ArgumentParser(prog="paperdb-api",
                                       description="Run the paperdb HTTP API")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host (use 0.0.0.0 for LAN/NAS access)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true",
                        help="Enable auto-reload (dev only)")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug"])
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install 'paperdb[api]'",
              file=sys.stderr)
        sys.exit(1)

    # Surface useful diagnostics at startup
    from config import get_config
    cfg = get_config()
    cfg.ensure_dirs()
    print(f"paperdb-api starting on http://{args.host}:{args.port}")
    print(f"  data dir : {cfg.data_dir}")
    print(f"  db path  : {cfg.db_path}")
    print(f"  config   : {cfg.config_source}")
    llm_provider = os.environ.get("LLM_PROVIDER", "(unset)")
    embed_key_set = "yes" if os.environ.get("LLM_EMBEDDING_API_KEY") else "no"
    print(f"  llm      : provider={llm_provider}  embed_key={embed_key_set}")
    if injected:
        print(f"  config   : loaded {len(injected)} LLM var(s) from config file")
    print()

    uvicorn.run(
        "paperdb_api.main:app",
        host=args.host, port=args.port,
        reload=args.reload, log_level=args.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
