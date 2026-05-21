"""paperdb command-line interface.

Public surface kept tiny: the `paperdb` console-script entry point and any
external caller only need `main` and `build_parser`.

Historically this entire CLI lived in a single ~2000-line `cli.py`. Phase 7.6
split it by command group under `paperdb_cli.commands.*`; the argparse spec
lives in `paperdb_cli.parser`; the dispatch loop lives in
`paperdb_cli.main_entry`. The package was named `paperdb_cli` (rather than
the more obvious `cli`) to avoid colliding with the generic `cli` namespace
that already exists in some Python distributions' site-packages.
"""

from paperdb_cli.main_entry import main
from paperdb_cli.parser import build_parser

__all__ = ["main", "build_parser"]
