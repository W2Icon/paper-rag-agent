from __future__ import annotations

import struct
import sqlite3
from typing import Optional

from db_connection import DatabaseConnection
from models import PaperRecord, SectionRecord, CitationLocationRecord


def _vec_to_blob(vec) -> bytes:
    """Pack a float vector as little-endian float32 bytes. Mirrors
    embedder.vec_to_bytes but lives here to keep paper_repo free of an
    upstream import on embedder (and avoid a circular import)."""
    return struct.pack(f"<{len(vec)}f", *vec)


class PaperRepo:

    def __init__(self, db: DatabaseConnection):
        self._db = db

    # ── Transactional full-paper insert ──────────────────────────

    def insert_full_paper(
        self,
        paper: PaperRecord,
        sections: list[SectionRecord],
        ref_groups: list[tuple],
        reference_repo,
    ) -> int:
        with self._db.transaction() as cursor:
            paper_id = self._insert_paper_cur(cursor, paper)

            for sec in sections:
                sec.paper_id = paper_id
            self._insert_sections_cur(cursor, sections)

            for ref_rec, cit_recs in ref_groups:
                ref_rec.paper_id = paper_id
                ref_id = reference_repo.insert_reference_cur(cursor, ref_rec)
                for cit in cit_recs:
                    cit.reference_id = ref_id
                    cit.paper_id = paper_id
                self._insert_citations_cur(cursor, cit_recs)

            return paper_id

    # ── Single-record inserts (cursor-based) ─────────────────────

    def _insert_paper_cur(self, cursor: sqlite3.Cursor, paper: PaperRecord) -> int:
        row = paper.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["?"] * len(row))
        cursor.execute(
            f"INSERT INTO papers ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        return cursor.lastrowid

    def _insert_sections_cur(self, cursor: sqlite3.Cursor, sections: list[SectionRecord]) -> None:
        if not sections:
            return
        for sec in sections:
            row = sec.to_row()
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?"] * len(row))
            cursor.execute(
                f"INSERT INTO sections ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            sec.id = cursor.lastrowid
            self._insert_section_richcontent_cur(cursor, sec)

    def _insert_section_richcontent_cur(
        self, cursor: sqlite3.Cursor, section: SectionRecord
    ) -> None:
        """Fan a section's MinerU-sourced tables/formulas/images out into the
        v6 sibling tables. No-op for PyMuPDF-sourced sections (empty lists)."""
        sid = section.id
        pid = section.paper_id
        if sid is None or pid in (0, None):
            return

        if section.tables:
            cursor.executemany(
                "INSERT INTO section_tables "
                "(section_id, paper_id, html, markdown, caption, page, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        sid,
                        pid,
                        t.get("html", "") or "",
                        t.get("markdown", "") or "",
                        t.get("caption", "") or "",
                        int(t.get("page", 0) or 0),
                        i,
                    )
                    for i, t in enumerate(section.tables)
                ],
            )

        if section.formulas:
            cursor.executemany(
                "INSERT INTO section_formulas "
                "(section_id, paper_id, latex, formula_type, page, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        sid,
                        pid,
                        f.get("latex", "") or "",
                        f.get("type", "block") or "block",
                        int(f.get("page", 0) or 0),
                        i,
                    )
                    for i, f in enumerate(section.formulas)
                    if f.get("latex")
                ],
            )

        if section.images:
            cursor.executemany(
                "INSERT INTO section_images "
                "(section_id, paper_id, image_path, caption, page, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        sid,
                        pid,
                        img.get("path", "") or "",
                        img.get("caption", "") or "",
                        int(img.get("page", 0) or 0),
                        i,
                    )
                    for i, img in enumerate(section.images)
                    if img.get("path")
                ],
            )

    def _insert_citations_cur(self, cursor: sqlite3.Cursor, citations: list[CitationLocationRecord]) -> None:
        if not citations:
            return
        for cit in citations:
            row = cit.to_row()
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?"] * len(row))
            cursor.execute(
                f"INSERT INTO citation_locations ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )

    # ── Read operations ──────────────────────────────────────────

    def get_by_id(self, paper_id: int) -> Optional[PaperRecord]:
        row = self._db.conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        return PaperRecord.from_row(dict(row)) if row else None

    def get_by_sha256(self, sha256: str) -> Optional[PaperRecord]:
        row = self._db.conn.execute(
            "SELECT * FROM papers WHERE pdf_sha256 = ?", (sha256,)
        ).fetchone()
        return PaperRecord.from_row(dict(row)) if row else None

    def get_by_doi(self, doi: str) -> Optional[PaperRecord]:
        row = self._db.conn.execute(
            "SELECT * FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        return PaperRecord.from_row(dict(row)) if row else None

    def list_papers(
        self,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "ingested_at DESC",
    ) -> list[PaperRecord]:
        allowed_orders = {
            "ingested_at DESC", "ingested_at ASC",
            "title ASC", "title DESC",
            "id ASC", "id DESC",
        }
        if order_by not in allowed_orders:
            order_by = "ingested_at DESC"
        rows = self._db.conn.execute(
            f"SELECT * FROM papers ORDER BY {order_by} LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [PaperRecord.from_row(dict(r)) for r in rows]

    def search_papers(self, query: str, limit: int = 20, field: str = "all") -> list[PaperRecord]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        esc = " ESCAPE '\\'"
        if field == "title":
            sql = f"SELECT * FROM papers WHERE title LIKE ?{esc} LIMIT ?"
            params = (pattern, limit)
        elif field == "abstract":
            sql = f"SELECT * FROM papers WHERE abstract LIKE ?{esc} LIMIT ?"
            params = (pattern, limit)
        elif field == "author":
            sql = f"SELECT * FROM papers WHERE authors LIKE ?{esc} LIMIT ?"
            params = (pattern, limit)
        else:
            sql = (f"SELECT * FROM papers WHERE "
                   f"title LIKE ?{esc} OR abstract LIKE ?{esc} OR authors LIKE ?{esc} OR keywords LIKE ?{esc} "
                   f"LIMIT ?")
            params = (pattern, pattern, pattern, pattern, limit)
        rows = self._db.conn.execute(sql, params).fetchall()
        return [PaperRecord.from_row(dict(r)) for r in rows]

    def count_papers(self) -> int:
        row = self._db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()
        return row[0]

    def delete_paper(self, paper_id: int) -> bool:
        with self._db.transaction() as cursor:
            cursor.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
            return cursor.rowcount > 0

    def update_llm_fields(
        self,
        paper_id: int,
        *,
        summary: Optional[str] = None,
        research_field: Optional[str] = None,
        methodology: Optional[str] = None,
        key_findings: Optional[list[str]] = None,
    ) -> None:
        import json as _json
        with self._db.transaction() as cursor:
            cursor.execute(
                """
                UPDATE papers
                SET llm_summary = ?,
                    llm_research_field = ?,
                    llm_methodology = ?,
                    llm_key_findings = ?,
                    llm_analyzed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?
                """,
                (
                    summary,
                    research_field,
                    methodology,
                    _json.dumps(key_findings, ensure_ascii=False) if key_findings else None,
                    paper_id,
                ),
            )

    def list_unanalyzed(self, limit: int = 1000) -> list[PaperRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM papers WHERE llm_analyzed_at IS NULL ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [PaperRecord.from_row(dict(r)) for r in rows]

    def list_papers_without_embeddings(self, limit: int = 1000) -> list[PaperRecord]:
        rows = self._db.conn.execute(
            """
            SELECT * FROM papers
            WHERE title_embedding IS NULL
               OR abstract_embedding IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [PaperRecord.from_row(dict(r)) for r in rows]

    def update_paper_embeddings(
        self,
        paper_id: int,
        *,
        title: Optional[bytes] = None,
        abstract: Optional[bytes] = None,
        fulltext: Optional[bytes] = None,
    ) -> None:
        with self._db.transaction() as cursor:
            cursor.execute(
                """
                UPDATE papers
                SET title_embedding = ?,
                    abstract_embedding = ?,
                    fulltext_embedding = ?
                WHERE id = ?
                """,
                (title, abstract, fulltext, paper_id),
            )

    def update_summary_embedding(self, paper_id: int, vec_bytes: bytes) -> None:
        """Persist the vector built from llm_summary + methodology + findings."""
        with self._db.transaction() as cursor:
            cursor.execute(
                "UPDATE papers SET summary_embedding = ? WHERE id = ?",
                (vec_bytes, paper_id),
            )

    def update_section_embedding(self, section_id: int, vec_bytes: bytes) -> None:
        with self._db.transaction() as cursor:
            cursor.execute(
                "UPDATE sections SET section_embedding = ? WHERE id = ?",
                (vec_bytes, section_id),
            )

    def update_section_embeddings(self, items: list[tuple[int, bytes]]) -> int:
        if not items:
            return 0
        with self._db.transaction() as cursor:
            cursor.executemany(
                "UPDATE sections SET section_embedding = ? WHERE id = ?",
                [(vec, sid) for sid, vec in items],
            )
        return len(items)

    def replace_section_chunks(
        self,
        chunks: list,  # list[EmbeddedChunk] — typed as list to avoid circular import
    ) -> int:
        """Atomically replace the chunk rows for the sections referenced in
        `chunks`. Existing chunks for those sections are deleted; the new
        chunks (with their text + embedding) are inserted. Sections that
        have no chunks in the input list are left untouched.

        Returns the number of chunks written.
        """
        if not chunks:
            return 0
        section_ids = sorted({c.section_id for c in chunks})
        with self._db.transaction() as cursor:
            placeholders = ",".join(["?"] * len(section_ids))
            cursor.execute(
                f"DELETE FROM section_chunks WHERE section_id IN ({placeholders})",
                section_ids,
            )
            cursor.executemany(
                "INSERT INTO section_chunks "
                "(section_id, paper_id, chunk_idx, text, char_start, char_end, chunk_embedding) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.section_id,
                        # paper_id is filled in by caller using section's paper_id.
                        # We expect EmbeddedChunk-like duck typing.
                        getattr(c, "paper_id", 0),
                        c.chunk_idx,
                        c.text,
                        c.char_start,
                        c.char_end,
                        # vec is a Python list[float]; caller should pre-pack
                        # via embedder.vec_to_bytes for portability. Here we
                        # accept either bytes (pre-packed) or a list/tuple.
                        c.vec if isinstance(c.vec, (bytes, bytearray, memoryview))
                            else _vec_to_blob(c.vec),
                    )
                    for c in chunks
                ],
            )
        return len(chunks)

    def get_embedding_stats(self) -> dict:
        c = self._db.conn
        return {
            "papers_with_title_emb": c.execute(
                "SELECT COUNT(*) FROM papers WHERE title_embedding IS NOT NULL"
            ).fetchone()[0],
            "papers_with_abstract_emb": c.execute(
                "SELECT COUNT(*) FROM papers WHERE abstract_embedding IS NOT NULL"
            ).fetchone()[0],
            "papers_with_fulltext_emb": c.execute(
                "SELECT COUNT(*) FROM papers WHERE fulltext_embedding IS NOT NULL"
            ).fetchone()[0],
            "sections_with_emb": c.execute(
                "SELECT COUNT(*) FROM sections WHERE section_embedding IS NOT NULL"
            ).fetchone()[0],
            "section_chunks_total": c.execute(
                "SELECT COUNT(*) FROM section_chunks"
            ).fetchone()[0],
            "section_chunks_with_emb": c.execute(
                "SELECT COUNT(*) FROM section_chunks WHERE chunk_embedding IS NOT NULL"
            ).fetchone()[0],
            "papers_with_summary_emb": c.execute(
                "SELECT COUNT(*) FROM papers WHERE summary_embedding IS NOT NULL"
            ).fetchone()[0],
            "viewpoints_total": c.execute(
                "SELECT COUNT(*) FROM lit_review_entries"
            ).fetchone()[0],
            "viewpoints_with_emb": c.execute(
                "SELECT COUNT(*) FROM lit_review_entries WHERE viewpoint_embedding IS NOT NULL"
            ).fetchone()[0],
            "references_with_emb": c.execute(
                "SELECT COUNT(*) FROM references_ WHERE ref_embedding IS NOT NULL"
            ).fetchone()[0],
        }

    def get_sections(self, paper_id: int) -> list[SectionRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM sections WHERE paper_id = ? ORDER BY sort_order",
            (paper_id,),
        ).fetchall()
        return [SectionRecord.from_row(dict(r)) for r in rows]

    def get_citation_locations(self, paper_id: int) -> list[CitationLocationRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM citation_locations WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [CitationLocationRecord.from_row(dict(r)) for r in rows]

    def get_stats(self) -> dict:
        c = self._db.conn
        papers = c.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        sections = c.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        refs = c.execute("SELECT COUNT(*) FROM references_").fetchone()[0]
        citations = c.execute("SELECT COUNT(*) FROM citation_locations").fetchone()[0]
        tags = c.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        return {
            "papers": papers,
            "sections": sections,
            "references": refs,
            "citation_locations": citations,
            "tags": tags,
        }
