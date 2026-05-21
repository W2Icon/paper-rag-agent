"""Tests for config.py workspace-resolution precedence.

Precedence (per the project's design):
    explicit (CLI arg) > env:PAPERDB_WORKSPACE > marker file (paperdb.workspace)
    > default (~/paperdb/)

We use monkeypatch + tmp_path so we never touch the real home dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config as config_mod


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any leaking PAPERDB_* env vars so each test starts clean."""
    monkeypatch.delenv("PAPERDB_WORKSPACE", raising=False)


def test_explicit_wins_over_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    env_dir = tmp_path / "env"
    explicit.mkdir(); env_dir.mkdir()
    monkeypatch.setenv("PAPERDB_WORKSPACE", str(env_dir))

    ws, source = config_mod.resolve_workspace(explicit=explicit)
    assert ws == explicit.resolve()
    assert source == "explicit"


def test_env_var_wins_over_marker_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_dir = tmp_path / "env"
    marker_dir = tmp_path / "marker"
    env_dir.mkdir(); marker_dir.mkdir()
    (marker_dir / "paperdb.workspace").touch()
    monkeypatch.setenv("PAPERDB_WORKSPACE", str(env_dir))
    monkeypatch.chdir(marker_dir)

    ws, source = config_mod.resolve_workspace()
    assert ws == env_dir.resolve()
    assert source.startswith("env:")


def test_marker_file_walks_upward_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "paperdb.workspace").touch()
    nested = root / "subproj" / "deeper"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    ws, source = config_mod.resolve_workspace()
    assert ws == root.resolve()
    assert source.startswith("marker:")


def test_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No explicit, no env, no marker → default workspace dir."""
    elsewhere = tmp_path / "no-marker-here"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    ws, source = config_mod.resolve_workspace()
    assert source == "default"
    # Returned path matches default_workspace_dir()
    assert ws == config_mod.default_workspace_dir()
