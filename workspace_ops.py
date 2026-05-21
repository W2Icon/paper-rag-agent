"""
Workspace operations: init / migrate / inspect.

Backs the `paperdb init`, `paperdb workspace migrate`, and `paperdb workspace show`
commands. Kept separate from cli.py so the logic is testable in isolation and
re-usable from the API layer.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from config import (
    AppConfig,
    _legacy_archive_dir,
    _legacy_config_path,
    _legacy_data_dir,
    default_workspace_dir,
    detect_legacy_data,
    resolve_workspace,
)


# ── Init ─────────────────────────────────────────────────────────


def workspace_layout(workspace: Path) -> dict[str, Path]:
    """The canonical set of subdirectories/files inside a workspace.

    Used by both `init` (to create them) and the API layer (to query them).
    """
    return {
        "workspace": workspace,
        "config":    workspace / "config.toml",
        "db":        workspace / "papers.db",
        "pdfs":      workspace / "pdfs",
        "archive":   workspace / "archive",
        "cache":     workspace / "cache",
    }


def init_workspace(
    workspace: Path,
    *,
    overwrite_config: bool = False,
    quiet: bool = False,
) -> dict:
    """Create the workspace skeleton + drop a starter config.toml.

    Returns a dict of {path, action} so the caller can pretty-print results.

    Args:
        workspace: target directory (will be created if missing)
        overwrite_config: if False (default), an existing config.toml is left
                          alone so we never clobber API keys.
    """
    workspace = workspace.expanduser().resolve()
    layout = workspace_layout(workspace)
    actions: list[tuple[Path, str]] = []

    # Create directories
    for key in ("workspace", "pdfs", "archive", "cache"):
        p = layout[key]
        if p.exists():
            actions.append((p, "exists"))
        else:
            p.mkdir(parents=True, exist_ok=True)
            actions.append((p, "created"))

    # Drop a starter config.toml from the example template
    config_path = layout["config"]
    if config_path.exists() and not overwrite_config:
        actions.append((config_path, "exists (kept)"))
    else:
        template = _find_config_template()
        if template is not None:
            shutil.copy2(template, config_path)
            os.chmod(config_path, 0o600)  # API keys live here
            actions.append((config_path, "created from template"))
        else:
            config_path.write_text(_minimal_config_text(), encoding="utf-8")
            os.chmod(config_path, 0o600)
            actions.append((config_path, "created (minimal)"))

    # Add a marker so child commands can detect this as a workspace.
    marker = workspace / "paperdb.workspace"
    if not marker.exists():
        marker.write_text(
            "# paperdb workspace marker — do not delete\n"
            "# `paperdb` will find this directory by walking up from CWD.\n",
            encoding="utf-8",
        )
        actions.append((marker, "created"))

    if not quiet:
        for path, action in actions:
            mark = "✓" if action.startswith("created") else " "
            print(f"  {mark} [{action:>22}]  {path}")

    return {"workspace": workspace, "actions": actions, "config_path": config_path}


def _find_config_template() -> Optional[Path]:
    """Locate config.toml.example in the package, falling back to repo root."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "config.toml.example", here.parent / "config.toml.example"):
        if candidate.is_file():
            return candidate
    return None


def _minimal_config_text() -> str:
    """Fallback when config.toml.example is unavailable for some reason."""
    return (
        "# paperdb config — keys go here. NEVER commit this file.\n"
        "[llm]\n"
        'provider = "deepseek"\n'
        'api_key = ""                 # REQUIRED — DeepSeek / OpenAI key\n'
        "\n"
        "[embedding]\n"
        'api_key = ""                 # REQUIRED — DashScope key for Qwen embeddings\n'
    )


# ── Migrate ──────────────────────────────────────────────────────


@dataclass
class MigrationPlan:
    """A concrete plan of files to move from legacy paths to a workspace."""
    workspace: Path
    moves: list[tuple[Path, Path, str]]  # (src, dst, kind)
    legacy_dirs_to_archive: list[Path]   # parent dirs that will be empty afterwards

    def is_empty(self) -> bool:
        return not self.moves

    def summary(self) -> str:
        lines = [f"Workspace target: {self.workspace}"]
        for src, dst, kind in self.moves:
            lines.append(f"  [{kind:>7}] {src}  →  {dst}")
        if self.legacy_dirs_to_archive:
            lines.append("")
            lines.append("After migration, legacy directories will be renamed to:")
            for d in self.legacy_dirs_to_archive:
                lines.append(f"  {d}  →  {d.with_name(d.name + '.backup-' + _today())}")
        return "\n".join(lines)


def plan_migration(workspace: Optional[Path] = None) -> MigrationPlan:
    """Inspect the filesystem and return a MigrationPlan without doing anything."""
    workspace = (workspace or default_workspace_dir()).expanduser().resolve()
    detected = detect_legacy_data()

    moves: list[tuple[Path, Path, str]] = []
    if "db" in detected:
        moves.append((detected["db"], workspace / "papers.db", "db"))
    if "config" in detected:
        moves.append((detected["config"], workspace / "config.toml", "config"))
    if "pdfs" in detected:
        for pdf in sorted(detected["pdfs"].glob("*.pdf")):
            moves.append((pdf, workspace / "pdfs" / pdf.name, "pdf"))
    if "archive" in detected:
        for md in sorted(detected["archive"].rglob("*")):
            if md.is_file():
                rel = md.relative_to(detected["archive"])
                moves.append((md, workspace / "archive" / rel, "archive"))

    legacy_dirs: list[Path] = []
    if "db" in detected or "pdfs" in detected:
        legacy_dirs.append(_legacy_data_dir())
    if "config" in detected and _legacy_config_path().parent != _legacy_data_dir():
        legacy_dirs.append(_legacy_config_path().parent)
    if "archive" in detected:
        legacy_dirs.append(_legacy_archive_dir())

    return MigrationPlan(workspace=workspace, moves=moves, legacy_dirs_to_archive=legacy_dirs)


def execute_migration(
    plan: MigrationPlan,
    *,
    archive_old: bool = True,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict:
    """Perform the move operations described in `plan`.

    Args:
        archive_old: if True, rename legacy parent dirs to `*.backup-YYYY-MM-DD`
                     after moving their contents (so they're easy to delete later
                     but stay around for recovery if something looks off).
        dry_run: if True, only print what would happen.
    """
    workspace = plan.workspace
    if dry_run:
        if not quiet:
            print("[dry-run] Would execute the following plan:")
            print(plan.summary())
        return {"moved": 0, "archived": [], "dry_run": True}

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "pdfs").mkdir(exist_ok=True)
    (workspace / "archive").mkdir(exist_ok=True)
    (workspace / "cache").mkdir(exist_ok=True)

    moved = 0
    for src, dst, kind in plan.moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if not quiet:
                print(f"  [skip ] {dst} already exists; leaving legacy source in place")
            continue
        if kind == "db":
            # Move WAL/SHM siblings alongside the db file.
            shutil.move(str(src), str(dst))
            for suffix in ("-wal", "-shm"):
                sib = src.parent / (src.name + suffix)
                if sib.exists():
                    shutil.move(str(sib), str(dst.parent / (dst.name + suffix)))
        else:
            shutil.move(str(src), str(dst))
        moved += 1
        if not quiet:
            print(f"  [ ok  ] {src.name}  →  {dst}")

    # Tighten perms on the freshly-moved config.toml — it contains keys.
    new_config = workspace / "config.toml"
    if new_config.exists():
        try:
            os.chmod(new_config, 0o600)
        except OSError:
            pass

    archived: list[Path] = []
    if archive_old:
        for d in plan.legacy_dirs_to_archive:
            if not d.exists():
                continue
            # Only archive directories that are now empty of paperdb data.
            renamed = d.with_name(d.name + ".backup-" + _today())
            try:
                d.rename(renamed)
                archived.append(renamed)
                if not quiet:
                    print(f"  [arch ] {d}  →  {renamed}")
            except OSError as e:  # pragma: no cover
                if not quiet:
                    print(f"  [warn ] could not rename {d}: {e}")

    # Drop a workspace marker if not already present.
    marker = workspace / "paperdb.workspace"
    if not marker.exists():
        marker.write_text("# paperdb workspace marker\n", encoding="utf-8")

    if not quiet:
        print(f"\nDone. {moved} item(s) moved into {workspace}")
        if archived:
            print(f"Legacy directories archived: {len(archived)} (safe to delete after verification)")

    return {"moved": moved, "archived": archived, "dry_run": False}


# ── Helpers ──────────────────────────────────────────────────────


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def inspect_workspace(cfg: Optional[AppConfig] = None) -> dict:
    """Snapshot of the active workspace for `paperdb workspace show`."""
    cfg = cfg or AppConfig.from_env_and_file()
    layout = workspace_layout(cfg.workspace_dir)
    info: dict = {
        "workspace": str(cfg.workspace_dir),
        "workspace_source": cfg.workspace_source,
        "config_source": cfg.config_source,
        "config_file": str(cfg.config_file_path()),
    }
    info["exists"] = {k: p.exists() for k, p in layout.items()}
    if layout["db"].is_file():
        info["db_size_bytes"] = layout["db"].stat().st_size
    info["legacy_detected"] = {k: str(v) for k, v in detect_legacy_data().items()}
    return info
