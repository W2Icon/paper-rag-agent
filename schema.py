from db_connection import DatabaseConnection

CURRENT_SCHEMA_VERSION = 6  # v6: rich content (section_tables/formulas/images) + papers.parse_backend

_DDL = """
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    authors         TEXT NOT NULL DEFAULT '[]',
    affiliations    TEXT NOT NULL DEFAULT '[]',
    abstract        TEXT NOT NULL DEFAULT '',
    keywords        TEXT NOT NULL DEFAULT '[]',
    doi             TEXT,
    total_pages     INTEGER NOT NULL DEFAULT 0,
    pdf_path        TEXT NOT NULL,
    pdf_sha256      TEXT NOT NULL UNIQUE,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    ingested_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    llm_summary         TEXT,
    llm_research_field  TEXT,
    llm_methodology     TEXT,
    llm_key_findings    TEXT,
    llm_analyzed_at     TEXT,

    title_embedding     BLOB,
    abstract_embedding  BLOB,
    fulltext_embedding  BLOB,
    summary_embedding   BLOB,

    -- v6: which backend produced this record ("pymupdf" | "mineru")
    parse_backend       TEXT NOT NULL DEFAULT 'pymupdf'
);

CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_papers_sha256 ON papers(pdf_sha256);
CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title);
CREATE INDEX IF NOT EXISTS idx_papers_ingested_at ON papers(ingested_at);

CREATE TABLE IF NOT EXISTS sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    heading         TEXT NOT NULL,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 3),
    body_text       TEXT NOT NULL DEFAULT '',
    paragraphs      TEXT NOT NULL DEFAULT '[]',
    page_start      INTEGER NOT NULL,
    page_end        INTEGER NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    section_type    TEXT,
    section_embedding BLOB
);

CREATE INDEX IF NOT EXISTS idx_sections_paper_id ON sections(paper_id);

CREATE TABLE IF NOT EXISTS section_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id      INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    paper_id        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    chunk_idx       INTEGER NOT NULL,
    text            TEXT NOT NULL,
    char_start      INTEGER NOT NULL DEFAULT 0,
    char_end        INTEGER NOT NULL DEFAULT 0,
    chunk_embedding BLOB,
    UNIQUE(section_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS idx_section_chunks_section ON section_chunks(section_id);
CREATE INDEX IF NOT EXISTS idx_section_chunks_paper ON section_chunks(paper_id);

CREATE TABLE IF NOT EXISTS references_ (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    ref_number      INTEGER,
    text            TEXT NOT NULL,
    raw_text        TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    authors         TEXT NOT NULL DEFAULT '[]',
    year            TEXT,
    identifiers     TEXT NOT NULL DEFAULT '{}',
    url             TEXT,
    type            TEXT NOT NULL DEFAULT 'journalArticle',
    position_x      REAL,
    position_y      REAL,
    llm_relevance_score  REAL,
    llm_relationship     TEXT,
    ref_embedding   BLOB
);

CREATE INDEX IF NOT EXISTS idx_references_paper_id ON references_(paper_id);
CREATE INDEX IF NOT EXISTS idx_references_year ON references_(year) WHERE year IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_references_title ON references_(title);

CREATE TABLE IF NOT EXISTS citation_locations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id    INTEGER NOT NULL REFERENCES references_(id) ON DELETE CASCADE,
    paper_id        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page            INTEGER NOT NULL,
    x               REAL NOT NULL,
    y               REAL NOT NULL,
    text            TEXT NOT NULL,
    sentence        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_citation_locations_ref ON citation_locations(reference_id);
CREATE INDEX IF NOT EXISTS idx_citation_locations_paper ON citation_locations(paper_id);

CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    source  TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_tags_tag ON paper_tags(tag_id);

CREATE TABLE IF NOT EXISTS shared_citations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_a_id      INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    paper_b_id      INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    shared_ref_title TEXT NOT NULL,
    shared_ref_doi   TEXT,
    relationship     TEXT,
    confidence       REAL,
    explanation      TEXT,
    similarity_score REAL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(paper_a_id, paper_b_id, shared_ref_title)
);

CREATE INDEX IF NOT EXISTS idx_shared_citations_a ON shared_citations(paper_a_id);
CREATE INDEX IF NOT EXISTS idx_shared_citations_b ON shared_citations(paper_b_id);

CREATE TABLE IF NOT EXISTS lit_review_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_id      INTEGER REFERENCES sections(id),
    cited_ref_id    INTEGER REFERENCES references_(id),
    viewpoint       TEXT NOT NULL,
    context         TEXT,
    category        TEXT,
    llm_model       TEXT,
    viewpoint_embedding BLOB,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_lit_review_paper ON lit_review_entries(paper_id);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type       TEXT NOT NULL,
    name            TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    paper_id        INTEGER REFERENCES papers(id) ON DELETE CASCADE,
    doi             TEXT,
    embedding       BLOB,
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(node_type, name)
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON kg_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_paper ON kg_nodes(paper_id) WHERE paper_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kg_nodes_doi ON kg_nodes(doi) WHERE doi IS NOT NULL;

CREATE TABLE IF NOT EXISTS kg_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id          INTEGER NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    dst_id          INTEGER NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL,
    weight          REAL NOT NULL DEFAULT 1.0,
    metadata        TEXT NOT NULL DEFAULT '{}',
    UNIQUE(src_id, dst_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges(src_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_kg_edges_dst ON kg_edges(dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_kg_edges_type ON kg_edges(edge_type);

-- v6: rich content tables produced by layout-aware backends (MinerU).
-- The PyMuPDF heuristic backend leaves these empty; queries should LEFT JOIN.
CREATE TABLE IF NOT EXISTS section_tables (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id  INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    html        TEXT NOT NULL DEFAULT '',
    markdown    TEXT NOT NULL DEFAULT '',
    caption     TEXT NOT NULL DEFAULT '',
    page        INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_section_tables_section ON section_tables(section_id);
CREATE INDEX IF NOT EXISTS idx_section_tables_paper ON section_tables(paper_id);

CREATE TABLE IF NOT EXISTS section_formulas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id    INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    paper_id      INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    latex         TEXT NOT NULL,
    formula_type  TEXT NOT NULL DEFAULT 'block',
    page          INTEGER NOT NULL DEFAULT 0,
    sort_order    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_section_formulas_section ON section_formulas(section_id);
CREATE INDEX IF NOT EXISTS idx_section_formulas_paper ON section_formulas(paper_id);

CREATE TABLE IF NOT EXISTS section_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id   INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    image_path   TEXT NOT NULL,
    caption      TEXT NOT NULL DEFAULT '',
    page         INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_section_images_section ON section_images(section_id);
CREATE INDEX IF NOT EXISTS idx_section_images_paper ON section_images(paper_id);

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def init_schema(db: DatabaseConnection) -> None:
    db.conn.executescript(_DDL)
    _apply_column_migrations(db)
    current = get_schema_version(db)
    if current < CURRENT_SCHEMA_VERSION:
        db.conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        db.conn.commit()


def _apply_column_migrations(db: DatabaseConnection) -> None:
    """Add columns that may be missing from pre-existing tables.

    `CREATE TABLE IF NOT EXISTS` is a no-op when the table already exists,
    so any column added to _DDL after the first deploy needs an explicit
    ALTER TABLE here, guarded by a PRAGMA check.
    """
    # v2: sections.section_type
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sections)").fetchall()}
    if "section_type" not in cols:
        db.conn.execute("ALTER TABLE sections ADD COLUMN section_type TEXT")
        db.conn.commit()

    # v4: papers.summary_embedding + lit_review_entries.viewpoint_embedding
    paper_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "summary_embedding" not in paper_cols:
        db.conn.execute("ALTER TABLE papers ADD COLUMN summary_embedding BLOB")
        db.conn.commit()
    lr_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(lit_review_entries)").fetchall()}
    if "viewpoint_embedding" not in lr_cols:
        db.conn.execute("ALTER TABLE lit_review_entries ADD COLUMN viewpoint_embedding BLOB")
        db.conn.commit()

    # v6: papers.parse_backend (which extractor produced this row).
    # Old rows backfill to "pymupdf" via the column DEFAULT.
    if "parse_backend" not in paper_cols:
        db.conn.execute(
            "ALTER TABLE papers ADD COLUMN parse_backend TEXT NOT NULL DEFAULT 'pymupdf'"
        )
        db.conn.commit()


def get_schema_version(db: DatabaseConnection) -> int:
    try:
        row = db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row[0] is not None else 0
    except Exception:
        return 0


def migrate(db: DatabaseConnection) -> None:
    current = get_schema_version(db)
    if current < CURRENT_SCHEMA_VERSION:
        init_schema(db)
