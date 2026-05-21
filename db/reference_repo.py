from __future__ import annotations

import sqlite3
from typing import Optional

from db_connection import DatabaseConnection
from models import ReferenceRecord


class ReferenceRepo:

    def __init__(self, db: DatabaseConnection):
        self._db = db

    def insert_reference_cur(self, cursor: sqlite3.Cursor, ref: ReferenceRecord) -> int:
        row = ref.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        cursor.execute(
            f"INSERT INTO references_ ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        return cursor.lastrowid

    def get_references_for_paper(self, paper_id: int) -> list[ReferenceRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM references_ WHERE paper_id = ? ORDER BY ref_number",
            (paper_id,),
        ).fetchall()
        return [ReferenceRecord.from_row(dict(r)) for r in rows]

    def search_references(self, query: str, limit: int = 50) -> list[ReferenceRecord]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        rows = self._db.conn.execute(
            "SELECT * FROM references_ WHERE title LIKE ? ESCAPE '\\' OR authors LIKE ? ESCAPE '\\' LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
        return [ReferenceRecord.from_row(dict(r)) for r in rows]

    def count_references(self) -> int:
        row = self._db.conn.execute("SELECT COUNT(*) FROM references_").fetchone()
        return row[0]

    def get_most_cited_references(self, limit: int = 20) -> list[dict]:
        rows = self._db.conn.execute(
            """
            SELECT title, year, COUNT(DISTINCT paper_id) as paper_count
            FROM references_
            WHERE title != ''
            GROUP BY title
            HAVING paper_count > 1
            ORDER BY paper_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_llm_fields(
        self,
        ref_id: int,
        *,
        relevance_score: Optional[float] = None,
        relationship: Optional[str] = None,
    ) -> None:
        with self._db.transaction() as cursor:
            cursor.execute(
                """
                UPDATE references_
                SET llm_relevance_score = ?,
                    llm_relationship = ?
                WHERE id = ?
                """,
                (relevance_score, relationship, ref_id),
            )

    def update_ref_embedding(self, ref_id: int, vec_bytes: bytes) -> None:
        with self._db.transaction() as cursor:
            cursor.execute(
                "UPDATE references_ SET ref_embedding = ? WHERE id = ?",
                (vec_bytes, ref_id),
            )

    def update_ref_embeddings(self, items: list[tuple[int, bytes]]) -> int:
        if not items:
            return 0
        with self._db.transaction() as cursor:
            cursor.executemany(
                "UPDATE references_ SET ref_embedding = ? WHERE id = ?",
                [(vec, rid) for rid, vec in items],
            )
        return len(items)

    def bulk_update_llm_fields(self, updates: list[tuple[int, Optional[float], Optional[str]]]) -> int:
        """updates = [(ref_id, relevance_score, relationship), ...]"""
        if not updates:
            return 0
        with self._db.transaction() as cursor:
            cursor.executemany(
                """
                UPDATE references_
                SET llm_relevance_score = ?,
                    llm_relationship = ?
                WHERE id = ?
                """,
                [(rs, rel, rid) for (rid, rs, rel) in updates],
            )
        return len(updates)

    def find_shared_by_doi(self, paper_a_id: int, paper_b_id: int) -> list[tuple[ReferenceRecord, ReferenceRecord]]:
        rows = self._db.conn.execute(
            """
            SELECT a.id AS a_id, b.id AS b_id
            FROM references_ a
            JOIN references_ b ON json_extract(a.identifiers, '$.DOI') = json_extract(b.identifiers, '$.DOI')
            WHERE a.paper_id = ? AND b.paper_id = ?
              AND json_extract(a.identifiers, '$.DOI') IS NOT NULL
              AND json_extract(a.identifiers, '$.DOI') != ''
            """,
            (paper_a_id, paper_b_id),
        ).fetchall()
        result = []
        for row in rows:
            a_row = self._db.conn.execute("SELECT * FROM references_ WHERE id = ?", (row["a_id"],)).fetchone()
            b_row = self._db.conn.execute("SELECT * FROM references_ WHERE id = ?", (row["b_id"],)).fetchone()
            if a_row and b_row:
                result.append((
                    ReferenceRecord.from_row(dict(a_row)),
                    ReferenceRecord.from_row(dict(b_row)),
                ))
        return result
