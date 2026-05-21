"""Shared pytest fixtures.

The project lives at the repo root (no `src/` layout yet), so we add the
repo root to `sys.path` here so tests can import top-level modules like
`schema`, `retrieval`, `reference_parser` without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
