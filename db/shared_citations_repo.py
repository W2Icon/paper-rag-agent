from __future__ import annotations

from typing import Optional

from db_connection import DatabaseConnection
from models import SharedCitationRecord


class SharedCitationsRepo:

    def __init__(self, db: DatabaseConnection):
        self._db = db

    # ── Writes ────────────────────────────────────────────────────

    def insert_many(self, records: list[SharedCitationRecord]) -> int:
        if not records:
            return 0
        with self._db.transaction() as cursor:
            for r in records:
                row = r.to_row()
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["?"] * len(row))
                # UNIQUE(paper_a_id, paper_b_id, shared_ref_title) — idempotent
                cursor.execute(
                    f"INSERT OR IGNORE INTO shared_citations ({cols}) VALUES ({placeholders})",
                    list(row.values()),
                )
        return len(records)

    def update_relationship_for_pair(
        self,
        paper_a_id: int,
        paper_b_id: int,
        *,
        relationship: str,
        explanation: str,
        similarity_score: Optional[float] = None,
    ) -> int:
        """Apply the same paper-to-paper relationship to ALL shared-ref rows
        of this pair. Returns count of rows updated."""
        with self._db.transaction() as cursor:
            cursor.execute(
                """
                UPDATE shared_citations
                SET relationship = ?,
                    explanation = ?,
                    similarity_score = COALESCE(?, similarity_score)
                WHERE paper_a_id = ? AND paper_b_id = ?
                """,
                (relationship, explanation, similarity_score,
                 paper_a_id, paper_b_id),
            )
            return cursor.rowcount

    def clear_for_paper(self, paper_id: int) -> int:
        with self._db.transaction() as cursor:
            cursor.execute(
                "DELETE FROM shared_citations WHERE paper_a_id = ? OR paper_b_id = ?",
                (paper_id, paper_id),
            )
            return cursor.rowcount

    def clear_all(self) -> int:
        with self._db.transaction() as cursor:
            cursor.execute("DELETE FROM shared_citations")
            return cursor.rowcount

    # ── Reads ─────────────────────────────────────────────────────

    def get_for_pair(self, paper_a_id: int, paper_b_id: int) -> list[SharedCitationRecord]:
        a, b = sorted([paper_a_id, paper_b_id])
        rows = self._db.conn.execute(
            """
            SELECT * FROM shared_citations
            WHERE paper_a_id = ? AND paper_b_id = ?
            ORDER BY confidence DESC, shared_ref_title
            """,
            (a, b),
        ).fetchall()
        return [SharedCitationRecord.from_row(dict(r)) for r in rows]

    def list_top_pairs(self, min_shared: int = 2, limit: int = 50) -> list[dict]:
        """Returns [{paper_a_id, paper_b_id, shared_count, relationship,
                     similarity_score}, ...] sorted by shared_count desc."""
        rows = self._db.conn.execute(
            """
            SELECT paper_a_id,
                   paper_b_id,
                   COUNT(*) AS shared_count,
                   MAX(relationship) AS relationship,
                   MAX(similarity_score) AS similarity_score
            FROM shared_citations
            GROUP BY paper_a_id, paper_b_id
            HAVING shared_count >= ?
            ORDER BY shared_count DESC, similarity_score DESC NULLS LAST
            LIMIT ?
            """,
            (min_shared, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_pairs_for_paper(self, paper_id: int, min_shared: int = 1) -> list[dict]:
        rows = self._db.conn.execute(
            """
            SELECT
                CASE WHEN paper_a_id = ? THEN paper_b_id ELSE paper_a_id END AS other_id,
                COUNT(*) AS shared_count,
                MAX(relationship) AS relationship,
                MAX(similarity_score) AS similarity_score
            FROM shared_citations
            WHERE paper_a_id = ? OR paper_b_id = ?
            GROUP BY other_id
            HAVING shared_count >= ?
            ORDER BY shared_count DESC
            """,
            (paper_id, paper_id, paper_id, min_shared),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_pairs_missing_relationship(self, min_shared: int = 2,
                                         limit: int = 200) -> list[tuple[int, int, int]]:
        """Pairs with ≥min_shared shared refs but no LLM relationship yet."""
        rows = self._db.conn.execute(
            """
            SELECT paper_a_id, paper_b_id, COUNT(*) AS shared_count
            FROM shared_citations
            WHERE relationship IS NULL
            GROUP BY paper_a_id, paper_b_id
            HAVING shared_count >= ?
            ORDER BY shared_count DESC
            LIMIT ?
            """,
            (min_shared, limit),
        ).fetchall()
        return [(r["paper_a_id"], r["paper_b_id"], r["shared_count"]) for r in rows]

    def count(self) -> int:
        return self._db.conn.execute("SELECT COUNT(*) FROM shared_citations").fetchone()[0]

    def count_pairs(self) -> int:
        row = self._db.conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM shared_citations "
            "GROUP BY paper_a_id, paper_b_id)"
        ).fetchone()
        return row[0]
