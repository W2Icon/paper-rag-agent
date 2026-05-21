from __future__ import annotations

from db_connection import DatabaseConnection
from models import LitReviewEntryRecord


class LitReviewRepo:

    def __init__(self, db: DatabaseConnection):
        self._db = db

    def insert(self, entry: LitReviewEntryRecord) -> int:
        row = entry.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        with self._db.transaction() as cursor:
            cursor.execute(
                f"INSERT INTO lit_review_entries ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            return cursor.lastrowid

    def insert_many(self, entries: list[LitReviewEntryRecord]) -> int:
        if not entries:
            return 0
        with self._db.transaction() as cursor:
            for e in entries:
                row = e.to_row()
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["?"] * len(row))
                cursor.execute(
                    f"INSERT INTO lit_review_entries ({cols}) VALUES ({placeholders})",
                    list(row.values()),
                )
        return len(entries)

    def get_by_paper(self, paper_id: int) -> list[LitReviewEntryRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM lit_review_entries WHERE paper_id = ? ORDER BY id",
            (paper_id,),
        ).fetchall()
        return [LitReviewEntryRecord.from_row(dict(r)) for r in rows]

    def delete_by_paper(self, paper_id: int) -> int:
        with self._db.transaction() as cursor:
            cursor.execute(
                "DELETE FROM lit_review_entries WHERE paper_id = ?",
                (paper_id,),
            )
            return cursor.rowcount

    def count_for_paper(self, paper_id: int) -> int:
        row = self._db.conn.execute(
            "SELECT COUNT(*) FROM lit_review_entries WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        return row[0]

    def update_viewpoint_embeddings(self, items: list[tuple[int, bytes]]) -> int:
        """Bulk-write the viewpoint_embedding BLOB. items = [(entry_id, blob), ...]."""
        if not items:
            return 0
        with self._db.transaction() as cursor:
            cursor.executemany(
                "UPDATE lit_review_entries SET viewpoint_embedding = ? WHERE id = ?",
                [(blob, eid) for eid, blob in items],
            )
        return len(items)
