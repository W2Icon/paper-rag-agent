"""
FTS5 lexical index over the papers table.

Uses an external-content virtual table so we don't duplicate the data — FTS5
keeps only the inverted index and references `papers` by rowid. Triggers keep
the index in sync on INSERT/UPDATE/DELETE.

Call `ensure_fts_index(db)` once per process (idempotent and cheap). Call
`rebuild_fts_index(db)` if the schema changes or after bulk imports done
before the triggers were installed.
"""

from __future__ import annotations

from db_connection import DatabaseConnection


_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title,
    abstract,
    keywords,
    content='papers',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS papers_fts_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, keywords)
        VALUES (new.id, new.title, new.abstract, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS papers_fts_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, keywords)
        VALUES('delete', old.id, old.title, old.abstract, old.keywords);
END;

-- Critical: AFTER UPDATE OF, not bare AFTER UPDATE. Otherwise this trigger
-- fires on every UPDATE (incl. embedding BLOB writes) and corrupts the
-- external-content FTS5 index with repeated delete/reinsert cycles.
CREATE TRIGGER IF NOT EXISTS papers_fts_au
AFTER UPDATE OF title, abstract, keywords ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, keywords)
        VALUES('delete', old.id, old.title, old.abstract, old.keywords);
    INSERT INTO papers_fts(rowid, title, abstract, keywords)
        VALUES (new.id, new.title, new.abstract, new.keywords);
END;
"""


def ensure_fts_index(db: DatabaseConnection) -> None:
    """Create the FTS5 table + triggers, and backfill any pre-existing rows
    via the 'rebuild' command (idempotent and safe to call repeatedly).

    Note: for external-content FTS5 tables, plain INSERT INTO papers_fts ...
    does NOT actually populate the inverted index — it only inserts a stub
    row. The supported way to (re)build the index from the content table is
    `INSERT INTO papers_fts(papers_fts) VALUES('rebuild')`.
    """
    db.conn.executescript(_FTS_DDL)

    # Check if any paper rows were inserted before the triggers existed,
    # which leaves them unindexed. Cheap heuristic: papers exist but the
    # docsize subtable has no rows for some of them.
    papers_count = db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    if papers_count == 0:
        return
    # 'docsize' is the FTS5 internal table that holds one row per indexed
    # document. If it's smaller than papers, we need a rebuild.
    try:
        indexed = db.conn.execute("SELECT COUNT(*) FROM papers_fts_docsize").fetchone()[0]
    except Exception:
        indexed = 0
    if indexed < papers_count:
        with db.transaction() as cursor:
            cursor.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")


def rebuild_fts_index(db: DatabaseConnection) -> int:
    """Force a full rebuild of the FTS5 index. Returns row count indexed."""
    db.conn.executescript(_FTS_DDL)
    with db.transaction() as cursor:
        cursor.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    row = db.conn.execute("SELECT COUNT(*) FROM papers_fts").fetchone()
    return row[0]
