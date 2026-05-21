"""
Cross-platform configuration for paperdb.

WORKSPACE MODEL (new in 0.5)
────────────────────────────
Everything lives under a single workspace directory:

    <workspace>/
      config.toml      ← API keys + tunables (NEVER committed to git)
      papers.db        ← SQLite database
      pdfs/            ← uploaded / batch-ingested PDFs
      archive/         ← per-paper Markdown archives
      cache/           ← MinerU temp output and other regenerable artifacts

Workspace location is resolved in this order (highest priority first):

    1. --workspace CLI argument  (per-invocation override)
    2. PAPERDB_WORKSPACE env var (shell/Docker override)
    3. paperdb.workspace marker  (sentinel file in CWD or any ancestor)
    4. Default: ~/paperdb/

BACKWARD COMPATIBILITY
──────────────────────
Older installs used split OS directories (`~/Library/Application Support/paperdb/`
for db+pdfs, `~/.config/paperdb/config.toml`, `~/knowledge-base/archive/`).
If those are present AND the new workspace is empty, paperdb auto-discovers
the legacy locations and keeps using them — so upgrading does not break
existing libraries. Run `paperdb workspace migrate` to consolidate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib  # 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from platformdirs import user_config_dir, user_data_dir
except ImportError as e:  # pragma: no cover
    raise RuntimeError(
        "platformdirs not installed. Run: pip install platformdirs"
    ) from e


_APP_NAME = "paperdb"
_WORKSPACE_MARKER = "paperdb.workspace"
_DEFAULT_WORKSPACE = Path.home() / "paperdb"


# ── Workspace resolution ──────────────────────────────────────────


def default_workspace_dir() -> Path:
    """The unified workspace root (~/paperdb/ by default).

    Override with PAPERDB_WORKSPACE env var or --workspace CLI flag.
    """
    return _DEFAULT_WORKSPACE


def _find_workspace_marker(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a `paperdb.workspace` marker file.
    If found, the directory containing the marker is treated as the workspace.
    Lets users `cd` into a project and have paperdb pick up a local library
    without exporting any env var.
    """
    p = start.resolve()
    for _ in range(20):  # bounded climb — safety
        if (p / _WORKSPACE_MARKER).is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def resolve_workspace(explicit: Optional[Path] = None) -> tuple[Path, str]:
    """Pick the workspace directory and report where the value came from.

    Returns (workspace_path, source_label).
    """
    if explicit is not None:
        return explicit.expanduser().resolve(), "explicit"

    env_val = os.environ.get("PAPERDB_WORKSPACE")
    if env_val:
        return Path(env_val).expanduser().resolve(), "env:PAPERDB_WORKSPACE"

    marker = _find_workspace_marker(Path.cwd())
    if marker is not None:
        return marker, f"marker:{_WORKSPACE_MARKER}"

    return default_workspace_dir(), "default"


# ── Legacy path helpers (for backward-compat migration) ──────────


def _legacy_data_dir() -> Path:
    """Old (pre-0.5) per-user data directory from platformdirs."""
    return Path(user_data_dir(_APP_NAME, appauthor=False))


def _legacy_config_path() -> Path:
    """Old (pre-0.5) TOML config file location."""
    return Path(user_config_dir(_APP_NAME, appauthor=False)) / "config.toml"


def _legacy_archive_dir() -> Path:
    """Old (pre-0.5) hardcoded archive directory."""
    return Path.home() / "knowledge-base" / "archive"


def detect_legacy_data() -> dict:
    """Probe legacy locations and return what we found.

    Used by `paperdb workspace migrate` to summarise what would be moved,
    and at startup to auto-pick legacy paths when the new workspace is empty.
    """
    out: dict = {}
    ld = _legacy_data_dir()
    lc = _legacy_config_path()
    la = _legacy_archive_dir()
    if (ld / "papers.db").is_file():
        out["db"] = ld / "papers.db"
    if (ld / "pdfs").is_dir() and any(ld.joinpath("pdfs").iterdir()):
        out["pdfs"] = ld / "pdfs"
    if lc.is_file():
        out["config"] = lc
    if la.is_dir() and any(la.iterdir()):
        out["archive"] = la
    return out


# ── Per-subdir helpers (derive from workspace) ───────────────────


def default_config_path(workspace: Optional[Path] = None) -> Path:
    """The active config.toml location.

    Resolution order:
      1. <workspace>/config.toml if the workspace exists AND the file is there.
      2. Legacy ~/.config/paperdb/config.toml if it exists (backward compat).
      3. <workspace>/config.toml (will be created by `paperdb init`).
    """
    ws = workspace or resolve_workspace()[0]
    new = ws / "config.toml"
    if new.is_file():
        return new
    legacy = _legacy_config_path()
    if legacy.is_file():
        return legacy
    return new  # not yet created; this is where `paperdb init` will put it


# Backward-compat aliases (some callers still expect the old names).
def default_data_dir() -> Path:
    """DEPRECATED. Returns the workspace dir for callers that haven't migrated."""
    ws, _ = resolve_workspace()
    # If the new workspace doesn't exist but the old one does, prefer the old one.
    if not ws.exists() and _legacy_data_dir().exists():
        return _legacy_data_dir()
    return ws


def default_db_path() -> Path:
    ws, _ = resolve_workspace()
    if not (ws / "papers.db").exists() and (_legacy_data_dir() / "papers.db").is_file():
        return _legacy_data_dir() / "papers.db"
    return ws / "papers.db"


def default_pdf_dir() -> Path:
    ws, _ = resolve_workspace()
    legacy_pdfs = _legacy_data_dir() / "pdfs"
    if not (ws / "pdfs").exists() and legacy_pdfs.is_dir():
        return legacy_pdfs
    return ws / "pdfs"


def default_archive_dir() -> Path:
    """Per-paper Markdown archive directory.

    Defaults to `<workspace>/archive/`. Falls back to the old hardcoded
    `~/knowledge-base/archive/` ONLY if that directory already has content
    (i.e. you're an existing user — your data isn't going anywhere until
    you run `paperdb workspace migrate`).
    """
    ws, _ = resolve_workspace()
    new = ws / "archive"
    if new.exists():
        return new
    legacy = _legacy_archive_dir()
    if legacy.is_dir() and any(legacy.iterdir()):
        return legacy
    return new


# ── Config dataclass ──────────────────────────────────────────────


@dataclass
class AppConfig:
    workspace_dir: Path = field(default_factory=lambda: resolve_workspace()[0])
    db_path: Path = field(default_factory=default_db_path)
    data_dir: Path = field(default_factory=default_data_dir)
    pdf_dir: Path = field(default_factory=default_pdf_dir)
    archive_dir: Path = field(default_factory=default_archive_dir)
    cache_dir: Path = field(default_factory=lambda: resolve_workspace()[0] / "cache")
    verbose: bool = False
    batch_size: int = 50
    config_source: str = "defaults"  # for diagnostics
    workspace_source: str = "default"  # how the workspace was resolved

    @classmethod
    def from_env_and_file(cls, config_file: Optional[Path] = None) -> "AppConfig":
        """Build config from (in order): TOML file → env vars → defaults."""
        cfg = cls()
        source_bits: list[str] = []

        # Record where the workspace came from
        _, ws_source = resolve_workspace()
        cfg.workspace_source = ws_source
        source_bits.append(f"workspace:{ws_source}")

        # 1. TOML file (if present)
        path = config_file if config_file is not None else default_config_path(cfg.workspace_dir)
        if path.is_file():
            try:
                with path.open("rb") as f:
                    data = tomllib.load(f) or {}
                paths_section = data.get("paths", {}) if isinstance(data, dict) else {}
                # `workspace` overrides ALL derived paths
                if "workspace" in paths_section:
                    ws = Path(paths_section["workspace"]).expanduser()
                    cfg.workspace_dir = ws
                    cfg.data_dir = ws
                    cfg.db_path = ws / "papers.db"
                    cfg.pdf_dir = ws / "pdfs"
                    cfg.archive_dir = ws / "archive"
                    cfg.cache_dir = ws / "cache"
                # Individual overrides (rare — usually you'd just set `workspace`)
                if "data_dir" in paths_section:
                    cfg.data_dir = Path(paths_section["data_dir"]).expanduser()
                if "db_path" in paths_section:
                    cfg.db_path = Path(paths_section["db_path"]).expanduser()
                if "pdf_dir" in paths_section:
                    cfg.pdf_dir = Path(paths_section["pdf_dir"]).expanduser()
                if "archive_dir" in paths_section:
                    cfg.archive_dir = Path(paths_section["archive_dir"]).expanduser()
                if "cache_dir" in paths_section:
                    cfg.cache_dir = Path(paths_section["cache_dir"]).expanduser()
                runtime = data.get("runtime", {}) if isinstance(data, dict) else {}
                if "verbose" in runtime:
                    cfg.verbose = bool(runtime["verbose"])
                if "batch_size" in runtime:
                    cfg.batch_size = int(runtime["batch_size"])
                source_bits.append(f"file:{path}")
            except Exception as e:  # pragma: no cover
                print(f"[paperdb config] failed to read {path}: {e}")

        # 2. Env var overrides
        env_workspace = os.environ.get("PAPERDB_WORKSPACE")
        env_data_dir = os.environ.get("PAPER_DB_DATA_DIR")
        env_db_path = os.environ.get("PAPER_DB_PATH")
        env_pdf_dir = os.environ.get("PAPER_DB_PDF_DIR")
        env_archive_dir = os.environ.get("PAPER_DB_ARCHIVE_DIR")
        env_verbose = os.environ.get("PAPER_DB_VERBOSE")

        if env_workspace:
            ws = Path(env_workspace).expanduser()
            cfg.workspace_dir = ws
            cfg.data_dir = ws
            cfg.db_path = ws / "papers.db"
            cfg.pdf_dir = ws / "pdfs"
            cfg.archive_dir = ws / "archive"
            cfg.cache_dir = ws / "cache"
            source_bits.append("env:PAPERDB_WORKSPACE")
        if env_data_dir:
            cfg.data_dir = Path(env_data_dir).expanduser()
            source_bits.append("env:PAPER_DB_DATA_DIR")
        if env_db_path:
            cfg.db_path = Path(env_db_path).expanduser()
            source_bits.append("env:PAPER_DB_PATH")
        if env_pdf_dir:
            cfg.pdf_dir = Path(env_pdf_dir).expanduser()
            source_bits.append("env:PAPER_DB_PDF_DIR")
        if env_archive_dir:
            cfg.archive_dir = Path(env_archive_dir).expanduser()
            source_bits.append("env:PAPER_DB_ARCHIVE_DIR")
        if env_verbose is not None:
            cfg.verbose = env_verbose.strip().lower() in ("1", "true", "yes", "on")
            source_bits.append("env:PAPER_DB_VERBOSE")

        # If data_dir was overridden but db_path/pdf_dir weren't, derive them
        if env_data_dir and not env_db_path:
            cfg.db_path = cfg.data_dir / "papers.db"
        if env_data_dir and not env_pdf_dir:
            cfg.pdf_dir = cfg.data_dir / "pdfs"

        cfg.config_source = ", ".join(source_bits) if source_bits else "defaults"
        return cfg

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Backwards-compat shim."""
        return cls.from_env_and_file()

    def ensure_dirs(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def config_file_path(self) -> Path:
        """Where this AppConfig expects to find/write config.toml."""
        return default_config_path(self.workspace_dir)


_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig.from_env_and_file()
    return _config


def set_config(config: AppConfig) -> None:
    global _config
    _config = config


def reset_config() -> None:
    """Force the next get_config() to re-read env + file. Useful in tests."""
    global _config
    _config = None


# ── Bootstrap LLM env vars from TOML config file ─────────────────


# Mapping from [llm] TOML keys to LLM_* env var names. The TOML keys use
# snake_case; the env vars are the canonical ones consumed by llm_interface.py.
_LLM_TOML_TO_ENV = {
    "provider":                "LLM_PROVIDER",
    "api_key":                 "LLM_API_KEY",
    "base_url":                "LLM_BASE_URL",
    "complex_model":           "LLM_COMPLEX_MODEL",
    "complex_max_tokens":      "LLM_COMPLEX_MAX_TOKENS",
    "complex_temperature":     "LLM_COMPLEX_TEMPERATURE",
    "complex_thinking":        "LLM_COMPLEX_THINKING",
    "complex_reasoning_effort":"LLM_COMPLEX_REASONING_EFFORT",
    "simple_model":            "LLM_SIMPLE_MODEL",
    "simple_max_tokens":       "LLM_SIMPLE_MAX_TOKENS",
    "simple_temperature":      "LLM_SIMPLE_TEMPERATURE",
    "simple_thinking":         "LLM_SIMPLE_THINKING",
    "simple_reasoning_effort": "LLM_SIMPLE_REASONING_EFFORT",
    "embedding_model":         "LLM_EMBEDDING_MODEL",
    "embedding_api_key":       "LLM_EMBEDDING_API_KEY",
    "embedding_base_url":      "LLM_EMBEDDING_BASE_URL",
    "embedding_dimensions":    "LLM_EMBEDDING_DIMENSIONS",
    "embedding_batch_size":    "LLM_EMBEDDING_BATCH_SIZE",
    "tier_analyze_paper":      "LLM_TIER_ANALYZE_PAPER",
    "tier_score_refs":         "LLM_TIER_SCORE_REFS",
    "tier_lit_review":         "LLM_TIER_LIT_REVIEW",
    "tier_rerank":             "LLM_TIER_RERANK",
}


_injected_from_file: set[str] = set()


def get_env_vars_from_config_file() -> set[str]:
    """Returns the set of env vars that were populated by the TOML config
    file in this process (as opposed to being already in the real env)."""
    return frozenset(_injected_from_file)


def bootstrap_env_from_config_file(config_file: Optional[Path] = None) -> list[str]:
    """Read the [llm] section of the TOML config file and populate os.environ
    for any LLM_* vars NOT already set. Real env vars always win.

    Returns the list of env var names actually injected (for logging).
    Safe to call multiple times; idempotent.

    Call this AT THE START of every entrypoint (cli.py main, paperdb-api,
    paperdb-ui) before any module that calls LLMConfig.from_env() is imported
    in any code path that needs them.
    """
    path = config_file if config_file is not None else default_config_path()
    if not path.is_file():
        return []

    try:
        with path.open("rb") as f:
            data = tomllib.load(f) or {}
    except Exception as e:  # pragma: no cover
        print(f"[paperdb config] failed to read {path}: {e}")
        return []

    llm_section = data.get("llm", {}) if isinstance(data, dict) else {}
    if not isinstance(llm_section, dict):
        return []

    injected: list[str] = []
    for toml_key, env_key in _LLM_TOML_TO_ENV.items():
        if toml_key not in llm_section:
            continue
        if env_key in os.environ and os.environ[env_key] != "":
            continue  # real env var wins
        value = llm_section[toml_key]
        if value is None or value == "":
            continue
        os.environ[env_key] = str(value)
        injected.append(env_key)
        _injected_from_file.add(env_key)
    return injected
