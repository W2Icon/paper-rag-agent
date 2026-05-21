from __future__ import annotations

from typing import Optional

from db_connection import DatabaseConnection
from models import TagRecord


class TagRepo:

    def __init__(self, db: DatabaseConnection):
        self._db = db

    def create_tag(self, name: str, source: str = "user") -> int:
        with self._db.transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO tags (name, source) VALUES (?, ?)",
                (name, source),
            )
            row = cursor.execute(
                "SELECT id FROM tags WHERE name = ?", (name,)
            ).fetchone()
            return row[0]

    def get_tag_by_name(self, name: str) -> Optional[TagRecord]:
        row = self._db.conn.execute(
            "SELECT * FROM tags WHERE name = ?", (name,)
        ).fetchone()
        return TagRecord.from_row(dict(row)) if row else None

    def list_tags(self) -> list[TagRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM tags ORDER BY name"
        ).fetchall()
        return [TagRecord.from_row(dict(r)) for r in rows]

    def tag_paper(self, paper_id: int, tag_name: str) -> None:
        tag_id = self.create_tag(tag_name)
        with self._db.transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO paper_tags (paper_id, tag_id) VALUES (?, ?)",
                (paper_id, tag_id),
            )

    def untag_paper(self, paper_id: int, tag_name: str) -> None:
        tag = self.get_tag_by_name(tag_name)
        if tag is None:
            return
        with self._db.transaction() as cursor:
            cursor.execute(
                "DELETE FROM paper_tags WHERE paper_id = ? AND tag_id = ?",
                (paper_id, tag.id),
            )

    def get_tags_for_paper(self, paper_id: int) -> list[TagRecord]:
        rows = self._db.conn.execute(
            """
            SELECT t.* FROM tags t
            JOIN paper_tags pt ON t.id = pt.tag_id
            WHERE pt.paper_id = ?
            ORDER BY t.name
            """,
            (paper_id,),
        ).fetchall()
        return [TagRecord.from_row(dict(r)) for r in rows]

    def get_papers_by_tag(self, tag_name: str) -> list[int]:
        rows = self._db.conn.execute(
            """
            SELECT pt.paper_id FROM paper_tags pt
            JOIN tags t ON t.id = pt.tag_id
            WHERE t.name = ?
            """,
            (tag_name,),
        ).fetchall()
        return [r[0] for r in rows]

    def delete_tag(self, tag_name: str) -> bool:
        with self._db.transaction() as cursor:
            cursor.execute("DELETE FROM tags WHERE name = ?", (tag_name,))
            return cursor.rowcount > 0
