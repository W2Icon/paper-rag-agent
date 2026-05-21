# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Claude Code Quick Start

This repo ships with Claude-Code-native subagents and slash commands so the user can drive the paper pipeline conversationally. **Prefer these over hand-running `paperdb` from Bash.**

### Slash commands (`.claude/commands/`)
| Command | Use it for |
|---|---|
| `/paper-ingest <pdf-or-dir>` | Add PDFs → embed → run LLM analysis in one shot |
| `/paper-search <query>` | Hybrid (vector + FTS + RRF) retrieval, optional `--rerank` |
| `/paper-agent <question>` | Multi-agent answer (orchestrator dispatches specialists) |
| `/paper-stats` | One-screen library health snapshot |

### Subagents (`.claude/agents/`) — invoke via the `Agent` tool
| Subagent | When |
|---|---|
| `paper-lit-reviewer` | "what does my library say about X" — thematic review |
| `paper-qa` | Focused factual / quantitative question with `[paper N]` citations |
| `paper-comparator` | Row×column table across 2–5 papers (method/dataset/results) |
| `paper-gap-analyst` | "what's missing / unsolved / contradictory" |
| `paper-librarian` | Ingest / analyze / embed / cross-analyze / stats — NOT querying content |

### Routing logic (when the user asks something paper-related)
1. **One cohesive question** → `/paper-agent` (default — Python orchestrator already handles routing).
2. **Multiple independent questions on the same topic** → dispatch matching subagents IN PARALLEL via the `Agent` tool (single message, multiple `Agent` calls).
3. **Maintenance / ingestion** → `paper-librarian` subagent or `/paper-ingest`.
4. **Pure retrieval (no synthesis)** → `/paper-search`.

### Anti-patterns to avoid
- Don't re-implement retrieval / synthesis logic in the main conversation — the Python orchestrator already does multi-path recall, RRF, and LLM rerank. Wrap, don't replace.
- Don't strip `[paper N]` citations from any specialist output — the user needs them to follow up.
- Don't call `paperdb agent` and a subagent for the same question — pick one.
- Don't write to the DB without the `paper-librarian` subagent confirming the destructive action.

### Environment expectations
- The DB is at `~/Library/Application Support/paperdb/papers.db` (override with `PAPER_DB_PATH`).
- API keys (DeepSeek + DashScope) live in `~/Library/Application Support/paperdb/config.toml`. Check with `paperdb config show` if anything seems mis-keyed.
- WebUI: `paperdb start` → API on `localhost:8765`, UI on `localhost:8501`.

## Project

Academic PDF paper analysis tool + SQLite paper database. Extracts references, citation locations, metadata, and structured sections from PDFs, then stores and manages them in a local database.

## Commands

```bash
# Install (editable, with API + UI extras)
pip install -e ".[api,ui]"

# Optional: layout-aware DL parsing backend (MinerU)
# Required for scanned PDFs / formula → LaTeX / table → HTML-Markdown.
# ~2GB model download on first run; uses `mineru` CLI under the hood.
pip install -e ".[mineru]"               # adds mineru[core] (CPU-friendly)
# or manage MinerU separately:
pip install -U "mineru[core]"            # CPU only
pip install -U "mineru[all]"             # full stack incl. VLM (needs GPU)

# Switch parsing backend at ingest time (default = pymupdf):
paperdb ingest paper.pdf --backend mineru     # force layout-aware DL
paperdb ingest paper.pdf --backend auto       # sniff scanned → MinerU, else PyMuPDF
PAPER_PARSE_BACKEND=mineru paperdb ingest ... # env var equivalent

# One-time: create config file at OS-standard location (perms 0600)
paperdb config init                      # writes ~/Library/Application Support/paperdb/config.toml on macOS
# then edit it and paste your DeepSeek + DashScope API keys

# One-shot launcher: starts API + UI together, Ctrl-C cleans up both
paperdb start                            # localhost:8765 (API) + localhost:8501 (UI)
paperdb start --host 0.0.0.0             # LAN-accessible (no auth — VPN/LAN-only by design)

# Or start them separately (still picks up TOML config automatically)
paperdb-api                              # localhost only
paperdb-api --host 0.0.0.0 --port 8765   # LAN / NAS access
paperdb-ui                               # talks to localhost:8765 by default
paperdb-ui --api-url http://nas.local:8765   # point at remote API

# Inspect / debug config
paperdb config show                      # what env vars are loaded and from where
paperdb config path                      # just print the file path

# Legacy / minimal install (NOT recommended — see pyproject.toml for full deps)
# requirements.txt is kept only for back-compat with old `python main.py` workflows.
pip install -r requirements.txt          # pymupdf + openai + platformdirs + tomli

# Dev tools (pytest, ruff, mypy)
pip install -e ".[dev]"
pytest tests/                            # 49 unit tests cover schema migration,
                                         # RRF fusion, reference parser, config
                                         # workspace resolution, LLM year coercion

# Extract to JSON (reference-only)
python main.py paper.pdf -o refs.json -v

# Extract to JSON (full paper: metadata + sections + refs + citations)
python main.py paper.pdf --full-paper -v

# Database CLI — ingest, query, manage  (all subcommands invoked via `paperdb`)
paperdb ingest paper.pdf -v --tag "energy"
paperdb ingest ./papers/ --recursive
paperdb list [--tag TAG] [--json]
paperdb show <id> [--sections] [--references] [--citations] [--llm] [--json]
paperdb search "query" [--field title|abstract|author|all]
paperdb stats
paperdb export -o papers.json
paperdb tag add <id> <name>
paperdb delete <id> --force

# Phase 2: LLM analysis
paperdb analyze <id>                     # analyze one paper
paperdb analyze --missing                # analyze all papers without llm_analyzed_at
paperdb analyze --all --force            # re-analyze every paper
paperdb analyze <id> --skip-refs --skip-lit-review  # paper summary only
paperdb analyze <id> --dry-run           # preview without LLM calls
# v7+: `analyze` also fills papers.publication_year — re-run on existing
# papers to backfill the year for accurate --year-from/--year-to filtering.

# Phase 3: embeddings (Qwen text-embedding-v4)
paperdb ingest paper.pdf --embed         # ingest + embed in one shot
paperdb embed <id>                       # embed one paper
paperdb embed --missing                  # embed papers without title/abstract emb
paperdb embed --all --force              # re-embed everything
paperdb embed <id> --skip-refs           # skip per-reference embeddings

# Phase 3: three-tier retrieval (multi-path → RRF → LLM rerank)
paperdb search-rag "EV energy consumption"             # default: hybrid mode
paperdb search-rag "电动汽车续航" --mode vector         # semantic only (cross-lingual)
paperdb search-rag "MILP charging stations" --mode fts # lexical (BM25) only
paperdb search-rag "query" --rerank --top-k 10 --explain
paperdb search-rag "..." --tag energy --year-from 2024 # uses publication_year (v7+)

# Phase 3: citation cross-analysis
paperdb cross-analyze detect             # find shared refs (DOI + title)
paperdb cross-analyze detect --with-embedding  # also fuzzy match via ref_embedding
paperdb cross-analyze relate --min-shared 2    # LLM-characterize pair relationships
paperdb cross-analyze all                # detect + relate in one shot
paperdb cross-analyze list --min-shared 2 # top pairs by shared-ref count
paperdb cross-analyze pair <a> <b>       # inspect a specific pair

# Phase 4: multi-agent assistant
paperdb agent "<question>"                            # orchestrator dispatches specialists
paperdb agent "<question>" --task lit-review          # thematic review
paperdb agent "<question>" --task qa                  # cited answer
paperdb agent "<question>" --task compare             # multi-paper comparison table
paperdb agent "<question>" --task gap                 # research-gap analysis
paperdb agent "<question>" --show-plan --show-trace   # debug orchestration
paperdb agent "<question>" --no-synth                 # see raw specialist JSON outputs

# Override database path or workspace (works on any subcommand)
paperdb --db /path/to/my.db list
paperdb --workspace /path/to/ws stats

# Environment variables
PAPERDB_WORKSPACE=/path/to/ws            # workspace dir (default: ~/paperdb/)
PAPER_DB_PATH=/path/to/papers.db         # override default workspace DB path

# Phase 2 LLM config (DeepSeek default — OpenAI-compatible endpoint)
LLM_PROVIDER=deepseek                    # deepseek | openai | openai-compatible | none
LLM_API_KEY=sk-xxx                       # required when provider != none
LLM_BASE_URL=https://api.deepseek.com/v1 # auto-set for provider=deepseek

# Tier models
LLM_COMPLEX_MODEL=deepseek-v4-pro        # complex tier (default: thinking ON)
LLM_SIMPLE_MODEL=deepseek-v4-flash       # simple tier (default: thinking OFF)

# Thinking mode (only deepseek-v4-pro supports it)
LLM_COMPLEX_THINKING=enabled             # enabled | disabled
LLM_COMPLEX_REASONING_EFFORT=high        # high | max
LLM_SIMPLE_THINKING=disabled             # keep flash in fast mode
LLM_SIMPLE_TEMPERATURE=0.0               # only effective when thinking OFF

# Per-stage tier override (which agent uses which tier)
LLM_TIER_ANALYZE_PAPER=complex           # paper summary — needs reasoning
LLM_TIER_SCORE_REFS=simple               # ref scoring — cheap classification
LLM_TIER_LIT_REVIEW=complex              # lit-review viewpoint — needs nuance

# Embeddings (Phase 3+) — defaults to Qwen via DashScope International
LLM_EMBEDDING_MODEL=text-embedding-v4                                # Qwen3-Embedding family
LLM_EMBEDDING_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1  # auto-set
LLM_EMBEDDING_API_KEY=sk-xxx             # DashScope key (separate from LLM_API_KEY)
LLM_EMBEDDING_DIMENSIONS=1024            # 64|128|256|512|768|1024|1536|2048
LLM_EMBEDDING_BATCH_SIZE=10              # Qwen v4 caps at 10 per request
```

Pytest suite at `tests/` (49 tests, ~0.1s). Covers: schema v1→v7 migration, RRF fusion + cosine, reference-parser helpers, config workspace resolution precedence, LLM publication_year coercion. Packaging via `pyproject.toml`; the repo IS now under git.

## Architecture

Two layers: an **extraction pipeline** (stateless, JSON output) and a **database layer** (SQLite persistence + CLI management).

### Extraction Pipeline

Two entry points produce the same `Reference` + `CitationLocation` objects:

- `PDFReferenceExtractor` (extractor.py) — references + citations only → `ExtractResult`
- `PaperExtractor` (paper_extractor.py) — adds metadata + sections → `PaperExtractResult`

`PaperExtractor` dispatches between two backends via `backend="pymupdf"|"mineru"|"auto"`:

| Backend | Speed | Models | Handles scanned PDFs | Recovers tables/formulas/images |
|---|---|---|---|---|
| `pymupdf` (default) | Fast (~1-2s) | none | No | No |
| `mineru` | Slow (10-30s) | ~2GB DL models | Yes (OCR fallback) | Yes (HTML+Markdown tables, LaTeX formulas, extracted images) |
| `auto` | Auto-pick | both available | If MinerU installed | If MinerU installed |

**PyMuPDF backend** (`paper_extractor.py` → existing path) — heuristic flow:
```
pdf_reader.read_pdf_page()           # PyMuPDF → PDFLine list per page
  → reference_parser.get_ref_lines() # header/footer removal, column detection, ref section finding
  → reference_parser.merge_same_ref()# multi-line entry merging by indent
  → utils.ref_text_to_info()         # title/authors/year/DOI/arXiv parsing per entry
  → citation_finder.find_citations() # 6 citation formats: [1], (1), fullwidth, superscript, author-year
  → metadata_extractor.extract_metadata()  # (full-paper only) title/authors/abstract from page 1
  → section_parser.parse_sections()        # (full-paper only) heading detection + paragraph grouping
```

**MinerU backend** (`mineru_extractor.py` → new) — layout-aware DL flow:
```
mineru CLI subprocess               # invokes DocLayout-YOLO + UniMERNet + RapidTable + OCR
  → reads <out>/<stem>_content_list.json  # reading-order-sorted blocks
  → MinerUExtractor._blocks_to_sections() # title blocks open Sections;
                                          # table → section.tables (html+md+caption)
                                          # equation → section.formulas (LaTeX)
                                          # image → section.images (path+caption)
                                          # text inside References heading → ref_block_texts
  → MinerUExtractor._refs_from_blocks()   # one ref per block; reuse utils.ref_text_to_info
  → MinerUExtractor._attach_citations()   # citation locations still come from PyMuPDF body scan
                                          # (MinerU drops per-citation bbox info)
  → metadata_extractor.extract_metadata() # baseline; MinerU's first title block overrides if cleaner
```

Auto-detection in `PaperExtractor._resolve_backend()`: samples up to 3 pages with PyMuPDF, switches to MinerU when avg chars/page < 200 (i.e. likely scanned). Hard fall-back to PyMuPDF on MinerU subprocess failure (auto only — explicit `backend="mineru"` raises).

Key detail: `get_ref_lines(full_text=True)` returns `list[list[PDFLine]]` (page parts for sections), while `get_ref_lines(full_text=False)` returns `list[PDFLine]` (reference lines only). Same function, different return types based on flag.

### Database Layer

```
paperdb_cli.commands.ingest → BatchProcessor → PaperExtractor.extract()
                                             → converters.convert_extract_result()  # PaperExtractResult → DB records
                                             → PaperRepo.insert_full_paper()        # single transaction
```

- `converters.py` bridges extraction types to DB models. SHA-256 file hash provides dedup.
- `db/paper_repo.py` — transactional insert of paper + sections + references + citation_locations. CASCADE delete. `update_llm_fields` accepts an optional `publication_year` (v7+).
- `db/reference_repo.py` — reference queries, cross-paper DOI matching via `json_extract()`.
- `db/tag_repo.py` — tag CRUD with INSERT OR IGNORE idempotency.
- `db_connection.py` — SQLite with WAL mode, foreign keys ON, `transaction()` context manager.
- `schema.py` — 9 core tables + 3 layout-aware sibling tables (`section_tables` / `section_formulas` / `section_images`, only populated by the MinerU backend). `papers.parse_backend` records which extractor produced each row. **Current schema: v7** — `papers.publication_year` (INTEGER, NULL until LLM-extracted) with partial index. Migration is idempotent: `init_schema()` runs the full DDL then `_apply_column_migrations()` adds any missing columns / indexes on pre-existing DBs.

The `references_` table has a trailing underscore to avoid SQL keyword collision.

**Schema migration gotcha** (kept here so future maintenance doesn't regress): the v7 `idx_papers_publication_year` index is intentionally created in `_apply_column_migrations` (not inline in the main `_DDL`). Inline `CREATE INDEX IF NOT EXISTS` referencing a not-yet-added column crashes when `executescript(_DDL)` runs against a pre-v7 DB before the ALTER TABLE fires.

### LLM Interface & Analyzer (Phase 2)

`llm_interface.py` defines `LLMProvider` ABC with two-tier model routing (complex/simple). Implementations:
- `NoOpProvider` — used when `LLM_PROVIDER=none`
- `OpenAICompatibleProvider` — talks to any OpenAI-compatible Chat Completions endpoint (DeepSeek, OpenAI, etc.). Uses `complete_json()` with `response_format={"type": "json_object"}`. Retries 3× with backoff.

`llm_analyzer.py` orchestrates three stages, each independently callable:
1. **`analyze_paper(paper, sections)`** → fills `papers.llm_summary / llm_research_field / llm_methodology / llm_key_findings / publication_year`. Uses complex tier. Picks Intro/Methods/Results/Conclusion sections by keyword. The `publication_year` field (v7+) is coerced via `_coerce_year()` — accepts int / float / str within `[1900, 2099]`, rejects bool / dict / out-of-range values; returns None rather than guessing.
2. **`score_references(paper, refs)`** → fills `references_.llm_relevance_score` (0.0-1.0) and `llm_relationship` (foundational | comparison | methodology | dataset | background | extension | other). Uses simple tier in batches of 25.
3. **`extract_lit_review(paper_id, sections, refs, citations)`** → populates `lit_review_entries`. Only processes citations in lit-review-style sections (Introduction / Related Work / Literature Review). Uses complex tier per cited reference.

Driven via `paperdb analyze`.

### Embeddings (Phase 3)

`embedder.py` defines `PaperEmbedder` and float32 serializers (`vec_to_bytes` / `bytes_to_vec`). Generates 5 embedding types:
- `papers.title_embedding` — title text
- `papers.abstract_embedding` — abstract text
- `papers.fulltext_embedding` — **mean-pool of section embeddings** (avoids the 8192-token cap on single requests)
- `sections.section_embedding` — heading + joined paragraphs, one per section
- `references_.ref_embedding` — `"<title> [<authors>] (<year>)"` per reference

All vectors are stored as little-endian float32 bytes in BLOB columns. Default model is Qwen `text-embedding-v4` via DashScope International (`LLM_EMBEDDING_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1`), at 1024 dimensions. The provider's `embed()` auto-batches into chunks of 10 (Qwen's per-request limit) and tolerates empty strings.

`BatchProcessor` exposes both an in-line path (`ingest --embed`) and a standalone path (`embed_paper_by_id`). The CLI `embed` subcommand handles single / `--all` / `--missing` / `--force` / `--dry-run`, and `paperdb stats` shows embedding population per table.

### Three-tier retrieval (Phase 3)

`retrieval.py` + `reranker.py` + `db/fts_index.py` implement the standard RAG pattern:

1. **Multi-path recall** — `Retriever.vector_search()` (cosine over abstract_embedding) and `Retriever.fts_search()` (SQLite FTS5 over title/abstract/keywords, BM25 ranked). FTS queries are tokenised on `\w` + CJK ranges, quoted, AND-ed (falls back to OR if zero hits).
2. **RRF fusion** — `Retriever.rrf_fuse()` with k=60 (Cormack 2009). Robust to score-scale differences; works with one or many paths.
3. **LLM rerank** — `LLMReranker.rerank()` feeds title+abstract of top-N candidates to the simple tier (configurable via `LLM_TIER_RERANK`) and gets back per-doc relevance score + one-sentence reason. Falls back to fused order on LLM failure.

**FTS5 gotchas** (already fixed in `db/fts_index.py`, kept here so future maintenance doesn't regress):
- `AFTER UPDATE OF title, abstract, keywords` — bare `AFTER UPDATE` fires on every column write (incl. `title_embedding` BLOB) and corrupts the external-content index.
- Backfill for pre-existing rows uses `INSERT INTO papers_fts(papers_fts) VALUES('rebuild')`. Plain `INSERT INTO papers_fts(rowid, ...)` only creates stub rows without populating the inverted index for external-content tables.

**Year filter** (v7+): `--year-from`/`--year-to` resolve through `COALESCE(publication_year, year-of-ingested_at)`. Analyzed papers use the real publication year; unanalyzed papers fall back to ingestion year so they don't silently vanish from year-bounded queries. Run `paperdb analyze --missing` to clear the NULL bucket and tighten the filter.

Driven via `paperdb search-rag`. Auto-creates FTS index on first use.

### Citation cross-analysis (Phase 3)

`cross_analysis.py` + `db/shared_citations_repo.py` populate the `shared_citations` table with paper-pair overlaps.

**Detection** (`detect_all_shared_citations`) runs three layers in order:
1. **DOI exact** — `json_extract(identifiers, '$.DOI')` self-join, confidence 1.0
2. **Normalized title** — lowercase + strip punct/whitespace, bucket-join, confidence 0.95. Skips titles < 8 chars.
3. **Ref embedding cosine ≥ 0.92** — opt-in (`--with-embedding`). Pure-Python O(N²) over `ref_embedding` BLOBs.

Each detected match becomes one row in `shared_citations` with the pair-level abstract cosine stored in `similarity_score`. Deduplication by `(min(a,b), max(a,b), normalized_title)` ensures one row per (pair, ref) regardless of detection path.

**Relate** (`relate_all_pairs`) iterates pairs with ≥`min_shared` refs and no `relationship` yet, feeds the LLM (`simple` tier by default) the two paper meta + the shared-ref list, and writes a `relationship` ∈ {`builds_on_same_foundation`, `competing_methods`, `complementary`, `methodological_overlap`, `benchmarking_overlap`, `weak_overlap`, `other`} plus a per-pair explanation. The same relationship is applied to ALL shared-ref rows of that pair (uniform paper-pair characterization).

Driven via `paperdb cross-analyze {detect, relate, all, list, pair}`.

### Multi-agent assistant (Phase 4)

`agents/` package implements a hierarchical multi-agent system:

```
              Orchestrator (v4-pro thinking mode)
                  │   plans (one LLM call, JSON output)
                  ▼
   ┌──────────┬───────┴───┬──────────┐      (parallel via ThreadPoolExecutor;
   ▼          ▼           ▼          ▼       each specialist runs its own
LitReview  QAAgent    Compare      Gap       function-calling tool loop)
   │          │           │          │
   └──────────┴───────┬───┴──────────┘
                      ▼
                  SynthAgent (writes final markdown)
```

| Module | Role |
|---|---|
| `agents/tools.py` | 7 read-only tools (`search_papers`, `get_paper`, `get_paper_sections`, `get_lit_review_entries`, `get_paper_references`, `find_related_papers`, `search_within_paper`) + OpenAI function-calling schemas + `ToolContext` (db, llm, embedder). `get_paper` includes `publication_year` (v7+). |
| `agents/base.py` | `BaseAgent` — generic function-calling loop. DeepSeek thinking mode: passes `reasoning_content` back through messages on multi-turn tool use (per DeepSeek docs). Tool results are smart-truncated: 65% head + 35% tail with an explicit `[truncated N chars]` marker, with per-tool budgets in `TOOL_RESULT_BUDGETS` (e.g. `get_paper_sections=16k`, default 8k) so the model still sees JSON shape at the tail. |
| `agents/lit_review.py` | Thematic review specialist — finds papers on a topic, groups by theme, outputs structured JSON |
| `agents/qa.py` | Q&A specialist — answers focused questions with paper_id citations |
| `agents/compare.py` | Comparison specialist — rows×columns alignment across 2-5 papers (method, dataset, results, limitations) |
| `agents/gap.py` | Gap specialist — surfaces explicit gaps (papers' own limitations/future-work), methodological gaps (contradictions, no head-to-head), missing perspectives, and open research questions |
| `agents/orchestrator.py` | Plans (one v4-pro thinking call → JSON plan) and dispatches specialists in parallel. **Retries failed specialists up to `SPECIALIST_MAX_ATTEMPTS=2` times** (covers both raised exceptions and terminal `trace.error` states); `SpecialistResult.attempts` records how many tries it took. Fresh agent instance per attempt to avoid state leak. |
| `agents/synth.py` | Writes the final markdown report (executive summary + section structure + inline `[paper N]` citations + Sources line). Receives `[FAILED after N attempts: ...]` blocks for failed specialists and is instructed NOT to fabricate content for them; if ALL specialists fail it emits a short "could not be fulfilled" message. |

**Threading note**: `db_connection.py` uses `check_same_thread=False` so the orchestrator's `ThreadPoolExecutor` can dispatch specialists in parallel. All agent tools are read-only; SQLite WAL mode handles concurrent reads safely.

Driven via `paperdb agent`. `--task auto` (default) runs orchestrator; `--task lit-review|qa` calls one specialist directly. `--show-plan`/`--show-trace` for debugging.

### CLI layout (Phase 7.6 split)

The CLI is no longer a single `cli.py` monolith. The `paperdb_cli/` package layout:

```
paperdb_cli/
├── __init__.py          # re-exports `main`, `build_parser`
├── parser.py            # argparse spec for all 22 subcommands — kept as one
│                        #   module because subparsers share parent/sub objects
├── main_entry.py        # main() — config bootstrap + DISPATCH table
├── serializers.py       # paper_to_dict / section_to_dict / ref_to_dict / cit_to_dict
│                        #   (JSON output formats — adding fields is an API change)
└── commands/
    ├── ingest.py           # paperdb ingest
    ├── library.py          # list / show / search / delete / stats / export / tag / init-db
    ├── analyze.py          # paperdb analyze
    ├── embed.py            # paperdb embed
    ├── search_rag.py       # paperdb search-rag
    ├── cross_analyze.py    # paperdb cross-analyze
    ├── kg.py               # paperdb kg
    ├── agent.py            # paperdb agent
    ├── archive.py          # paperdb archive / fix-metadata
    ├── classify.py         # paperdb classify-sections
    └── config_cmds.py      # paperdb config / start / init / workspace
                            #   (bootstrap-only; dispatched before DB opens)
```

The `paperdb` console script (declared in `pyproject.toml`) points at
`paperdb_cli:main`, which resolves to `paperdb_cli/__init__.py`'s `main`.
The package is intentionally named `paperdb_cli` (not the more obvious
`cli`) because `cli` is a generic top-level name some Python installs
already populate in site-packages (e.g. stringzilla ships `split.py` /
`wc.py` under a `cli/` namespace). The previous flat `paperdb_cli.py`
shim has been folded into this package's `__init__.py`.

**Adding a new subcommand**: register it in `paperdb_cli/parser.py`, write a `cmd_<name>` in `paperdb_cli/commands/<group>.py`, and add it to `DISPATCH` in `paperdb_cli/main_entry.py`. The handler signature is `(args, db: DatabaseConnection) -> None` — except bootstrap-only ones (config/start/init/workspace) which take `(args, db=None)` and are invoked before the DB connection opens.

## Data Types

Extraction layer outputs: `PaperMeta`, `Section`, `Reference`, `CitationLocation`, `PaperExtractResult` (paper_extractor.py / extractor.py / citation_finder.py / metadata_extractor.py / section_parser.py).

DB layer records: `PaperRecord`, `SectionRecord`, `ReferenceRecord`, `CitationLocationRecord`, `TagRecord` (models.py). JSON list/dict fields use `json.dumps`/`json.loads` in `to_row()`/`from_row()`. `PaperRecord.publication_year: Optional[int]` is the v7-and-later LLM-extracted year (separate from the heuristic year embedded in references / DOIs).

## Known Limitations

PyMuPDF backend (default):
- PDF must have selectable text (no OCR) — switch to `--backend mineru` for scanned PDFs
- Metadata extraction is heuristic-based (~75-85% accuracy)
- Equation numbers `(1)` may false-match as citation `(1)` — no equation-aware filter
- `get_ref_lines` scans back-to-front for reference section; fails if no "References"/"Bibliography" heading exists
- Author-year citation matching requires `refs_info` to be built first (needs numbered reference entries)

MinerU backend:
- First run downloads ~2GB of model weights (cached in `~/.cache/huggingface/` or `~/.cache/modelscope/`)
- 10-30s per PDF on CPU vs 1-2s for PyMuPDF — only worth it for scanned/complex layouts
- MinerU drops per-citation bbox info, so `citation_locations` are still computed by PyMuPDF body scan
- Set `MINERU_MODEL_SOURCE=modelscope` if HuggingFace is unreachable from your network

## Project Roadmap

Phase 1 (done): SQLite database + ingestion pipeline + CLI
Phase 2 (done): LLM content analysis — DeepSeek/OpenAI-compatible provider, three-stage `paperdb analyze` (paper summary, ref scoring, lit-review viewpoints)
Phase 3 (done):
  - Embeddings: Qwen text-embedding-v4 via DashScope, `paperdb embed`, float32 BLOB storage
  - Retrieval: FTS5 + vector + RRF fusion + LLM rerank, `paperdb search-rag`
  - Citation cross-analysis: DOI + title + (optional) embedding detection → LLM pair relationship, `paperdb cross-analyze`
Phase 4 (done):
  - Orchestrator (v4-pro thinking, JSON plan) + 4 specialists (lit_review, qa, compare, gap) + Synth, parallel dispatch via ThreadPoolExecutor, `paperdb agent`
Phase 5 (done):
  - `pyproject.toml` packaging, console scripts (`paperdb`, `paperdb-api`, `paperdb-ui`)
  - Cross-platform config via `platformdirs` (Mac/Windows/Linux/NAS auto data-dir)
  - TOML config file at `~/.config/paperdb/config.toml` + env-var override
Phase 6 (done):
  - FastAPI HTTP backend (`paperdb_api/`) with all CLI commands exposed as REST
  - `POST /agent` streams via Server-Sent Events: plan / tool_call / specialist_done / synth_chunk / done
  - CORS enabled (LAN-only deployment, no auth per design)
Phase 7 partial (done):
  - Streamlit UI (`paperdb_ui/`) — 6 pages: Library, Paper Detail, Search, Agent Chat, Ingest, Settings
  - Agent Chat consumes SSE for live tool-call log + streaming synth markdown
Phase 7.5 (done):
  - Layout-aware parsing backend (`mineru_extractor.py`) — wraps MinerU CLI for OCR / formula → LaTeX / table → Markdown/HTML / image extraction
  - `PaperExtractor(backend=auto|pymupdf|mineru)`, `paperdb ingest --backend mineru`, `PAPER_PARSE_BACKEND` env var
  - Schema v6: `section_tables` / `section_formulas` / `section_images` + `papers.parse_backend`
Phase 7.6 (done — code-quality follow-up to REVIEW_AND_SUGGESTIONS.md):
  - Schema v7: `papers.publication_year` (INTEGER, LLM-extracted in `analyze_paper`) + partial index, retrieval year filter switched to `COALESCE(publication_year, year-of-ingested_at)`
  - CLI split: monolithic `cli.py` → `paperdb_cli/` package (parser / dispatcher / 11 command modules + serializers). Package named `paperdb_cli` to dodge the generic `cli` namespace some Python installs already use.
  - Test suite: `tests/` with 49 pytest cases covering schema migration, RRF fusion, reference-parser helpers, config workspace precedence, LLM year coercion
  - Multi-agent robustness: orchestrator retries failed specialists up to 2× (`SPECIALIST_MAX_ATTEMPTS`); synth handles `[FAILED after N attempts]` blocks without fabrication; smart tool-result truncation (head+tail with per-tool budget) replaces hard front-only cut
  - Polishing: `py.typed` marker for downstream type checking; `requirements.txt` deprecated in favour of `pip install -e ".[extras]"`
Phase 8 (TODO): Docker container (multi-arch amd64+arm64) for NAS deployment
Phase 9 (TODO): NAS deployment guide (Synology / QNAP), reverse proxy
