"""Console-script entrypoint for `paperdb-ui`. Wraps streamlit run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    # Bootstrap from TOML before Streamlit imports anything that reads os.environ
    from config import bootstrap_env_from_config_file
    bootstrap_env_from_config_file()

    parser = argparse.ArgumentParser(
        prog="paperdb-ui",
        description="Run the paperdb Streamlit UI (web frontend).",
    )
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--host", default="localhost",
                         help="Bind address (use 0.0.0.0 for LAN/NAS access)")
    parser.add_argument("--api-url", default=None,
                         help="Backend API URL (overrides PAPERDB_API_URL)")
    args = parser.parse_args()

    try:
        import streamlit.web.cli as stcli
    except ImportError:
        print("streamlit not installed. Run: pip install 'paperdb[ui]'",
              file=sys.stderr)
        sys.exit(1)

    if args.api_url:
        import os
        os.environ["PAPERDB_API_URL"] = args.api_url

    main_py = Path(__file__).parent / "main.py"
    sys.argv = [
        "streamlit", "run", str(main_py),
        "--server.port", str(args.port),
        "--server.address", args.host,
        "--browser.gatherUsageStats", "false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":  # pragma: no cover
    main()
