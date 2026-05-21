"""Tests for schema initialisation and incremental migrations.

Covers:
  - Fresh DB lands at CURRENT_SCHEMA_VERSION with every expected column.
  - A pre-v7 DB (no `publication_year` column) is upgraded in place.
  - The v7 `idx_papers_publication_year` partial index is created on both
    fresh and migrated DBs.
  - Re-running `init_schema` is idempotent.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db_connection import DatabaseConnection
from schema import CURRENT_SCHEMA_VERSION, get_schema_version, init_schema


def _fresh_path(tmp_path: Path, name: str = "test.db") -> Path:
    return tmp_path / name


def _papers_columns(db: DatabaseConnection) -> set[str]:
    return {r[1] for r in db.conn.execute("PRAGMA table_info(papers)").fetchall()}


def _papers_indexes(db: DatabaseConnection) -> set[str]:
    return {r[1] for r in db.conn.execute("PRAGMA index_list(papers)").fetchall()}


def test_fresh_db_lands_at_current_version(tmp_path: Path) -> None:
    db = DatabaseConnection(str(_fresh_path(tmp_path)))
    init_schema(db)

    assert get_schema_version(db) == CURRENT_SCHEMA_VERSION
    cols = _papers_columns(db)
    for expected in (
        "id", "title", "abstract", "doi", "ingested_at",
        "llm_summary", "llm_analyzed_at", "parse_backend",
        "publication_year",  # v7
    ):
        assert expected in cols, f"missing column on fresh DB: {expected}"


def test_publication_year_index_present_on_fresh_db(tmp_path: Path) -> None:
    db = DatabaseConnection(str(_fresh_path(tmp_path)))
    init_schema(db)
    assert "idx_papers_publication_year" in _papers_indexes(db)


def test_migrate_from_pre_v7_db(tmp_path: Path) -> None:
    """Simulate a v6 schema (no `publication_year`) and confirm the migration
    block adds the column + index without dropping existing rows."""
    path = _fresh_path(tmp_path, "v6.db")
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY, title TEXT, authors TEXT, affiliations TEXT,
            abstract TEXT, keywords TEXT, doi TEXT, total_pages INTEGER,
            pdf_path TEXT, pdf_sha256 TEXT UNIQUE, file_size_bytes INTEGER,
            ingested_at TEXT, llm_summary TEXT, llm_research_field TEXT,
            llm_methodology TEXT, llm_key_findings TEXT, llm_analyzed_at TEXT,
            title_embedding BLOB, abstract_embedding BLOB,
            fulltext_embedding BLOB, summary_embedding BLOB,
            parse_backend TEXT DEFAULT 'pymupdf'
        );
        INSERT INTO papers (title, authors, affiliations, abstract, keywords,
            pdf_path, pdf_sha256, ingested_at)
        VALUES ('Old paper', '[]', '[]', 'a', '[]', '/x', 'sha', '2024-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    db = DatabaseConnection(str(path))
    init_schema(db)

    assert "publication_year" in _papers_columns(db)
    assert "idx_papers_publication_year" in _papers_indexes(db)

    # Existing row preserved; new column defaults to NULL.
    row = db.conn.execute(
        "SELECT title, publication_year FROM papers"
    ).fetchone()
    assert row["title"] == "Old paper"
    assert row["publication_year"] is None


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    db = DatabaseConnection(str(_fresh_path(tmp_path)))
    init_schema(db)
    init_schema(db)  # should not raise

    assert get_schema_version(db) == CURRENT_SCHEMA_VERSION


def test_year_coalesce_filter_logic(tmp_path: Path) -> None:
    """Mirrors retrieval._apply_filters' COALESCE expression: papers with
    publication_year set are filtered on that; NULL papers fall back to the
    ingested_at year."""
    db = DatabaseConnection(str(_fresh_path(tmp_path)))
    init_schema(db)

    db.conn.execute(
        "INSERT INTO papers (title, authors, affiliations, abstract, keywords, "
        "pdf_path, pdf_sha256, ingested_at, publication_year) "
        "VALUES ('P1', '[]', '[]', '', '[]', '/p1', 'h1', '2026-01-01T00:00:00Z', 2020)"
    )
    db.conn.execute(
        "INSERT INTO papers (title, authors, affiliations, abstract, keywords, "
        "pdf_path, pdf_sha256, ingested_at) "
        "VALUES ('P2', '[]', '[]', '', '[]', '/p2', 'h2', '2024-01-01T00:00:00Z')"
    )
    db.conn.commit()

    rows = db.conn.execute(
        "SELECT title FROM papers "
        "WHERE COALESCE(publication_year, "
        "CAST(SUBSTR(ingested_at, 1, 4) AS INTEGER)) >= 2023 "
        "ORDER BY title"
    ).fetchall()
    titles = [r["title"] for r in rows]
    # P1 has explicit year 2020 → dropped; P2 has NULL → falls back to 2024 → kept.
    assert titles == ["P2"]
