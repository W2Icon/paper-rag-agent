# paper-rag-agent

> Self-hosted academic paper library with LLM analysis, multi-path retrieval, and a Claude-Code-native multi-agent assistant.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757)](https://claude.com/claude-code)

`paper-rag-agent` (package name: `paperdb`) ingests PDF papers, extracts structured content (sections, references, citations, tables, formulas, figures), runs LLM analysis, builds embeddings, and answers questions about your library through a multi-agent orchestrator. Everything runs locally against your own API keys — no SaaS, no telemetry.

---

## Why use this

- **Layout-aware PDF parsing** — PyMuPDF fast path + optional [MinerU](https://github.com/opendatalab/MinerU) backend for scanned PDFs, formulas → LaTeX, tables → HTML/Markdown
- **Three-tier retrieval** — vector + BM25 + RRF fusion + LLM rerank
- **Multi-agent QA** — orchestrator dispatches lit-review / Q&A / comparison / gap-analysis specialists in parallel
- **Cross-paper analysis** — auto-discover shared citations and LLM-characterise relationships
- **Knowledge graph** — entity/topic graph over the whole library, queryable from the agent
- **Single-folder workspace** — `cd ~/paperdb` and everything is there: db + PDFs + markdown archive + config
- **Claude Code native** — bundled slash commands (`/paper-ingest`, `/paper-search`, `/paper-agent`) and subagents work out of the box

---

## Quick start

```bash
# 1. install
pip install -e ".[api,ui]"

# 2. create your workspace (default ~/paperdb/) — drops a config.toml template
paperdb init

# 3. edit your API keys
$EDITOR ~/paperdb/config.toml          # fill in llm.api_key + embedding.api_key

# 4. ingest a paper
paperdb ingest path/to/paper.pdf

# 5. launch the web UI (API on :8765, Streamlit on :8501)
paperdb start
```

You're done. Open <http://localhost:8501> and start asking questions about your library.

---

## Configuration

### One file, one location

All API keys and settings live in **one file**: `<workspace>/config.toml`.

| Workspace | Config file location |
|---|---|
| Default (`~/paperdb/`) | `~/paperdb/config.toml` |
| Custom (`paperdb init ~/my-papers`) | `~/my-papers/config.toml` |
| Docker / CI | `$PAPERDB_WORKSPACE/config.toml` |

To see where paperdb is reading from right now:

```bash
paperdb workspace show
```

### Required keys

Two providers, two keys (see [`config.toml.example`](./config.toml.example) for the full annotated template):

| Key | Where to get one | Used for |
|---|---|---|
| `llm.api_key` | [platform.deepseek.com](https://platform.deepseek.com/api_keys) (or OpenAI / any OpenAI-compatible) | Summaries, viewpoint extraction, reranking, agent reasoning |
| `embedding.api_key` | [dashscope.aliyuncs.com](https://dashscope.console.aliyun.com/apiKey) | Qwen text-embedding-v4 for vector search |

### Env vars (Docker / CI alternative)

If you prefer environment variables (for Docker, Kubernetes, or CI), copy `.env.example` to `.env` and use your shell loader of choice (`direnv`, `docker-compose env_file`, systemd `EnvironmentFile`). Real env vars override `config.toml` values.

---

## Workspace layout

```
~/paperdb/
├── config.toml             ← your API keys + tunables (gitignored)
├── papers.db               ← SQLite database (the single source of truth)
├── paperdb.workspace       ← marker file (so `cd` ancestors find this dir)
├── pdfs/                   ← original PDFs you've ingested
├── archive/                ← derived per-paper markdown (Obsidian-friendly)
└── cache/                  ← MinerU temp output, regenerable
```

**Why this design**: one folder for your whole library. Back it up with `tar`, move it with `rsync`, point a new machine at it with `export PAPERDB_WORKSPACE=...`. No data sprinkled across OS app-support directories.

### Upgrading from a previous version

If you've used an older paperdb (data scattered across `~/Library/Application Support/paperdb/`, `~/.config/paperdb/`, `~/knowledge-base/archive/`), consolidate it with:

```bash
paperdb workspace migrate
# → shows the move plan, asks for confirmation, archives legacy dirs as *.backup-YYYY-MM-DD
```

Your existing config.toml (with API keys) and `papers.db` are preserved; nothing is deleted, just renamed.

---

## CLI cheat sheet

```bash
# Workspace
paperdb init [path]                 # create a new workspace
paperdb workspace show              # where am I reading data from?
paperdb workspace migrate           # consolidate legacy data

# Ingest
paperdb ingest paper.pdf            # extract + embed + analyze + archive (all-in-one)
paperdb ingest ./papers/ -r         # recursive directory ingest
paperdb ingest paper.pdf --backend mineru   # force layout-aware DL parser
paperdb ingest paper.pdf --backend auto     # sniff scanned vs digital
paperdb ingest paper.pdf --quick    # extraction only, no LLM steps

# Inspect
paperdb list [--tag energy]
paperdb show 42 --sections --references --citations --llm
paperdb stats

# Search
paperdb search "EV charging" --field title
paperdb search-rag "EV charging" --rerank --explain

# Multi-agent QA
paperdb agent "What does my library say about lithium-ion degradation?"
paperdb agent "Compare the methodology of papers 12 and 17" --task compare

# Cross-paper analysis
paperdb cross-analyze all          # detect shared refs + LLM-relate pairs

# Web UI
paperdb start                       # API + Streamlit UI together
paperdb start --host 0.0.0.0        # LAN access
```

---

## Optional: MinerU backend for scanned PDFs

The default PyMuPDF backend is ~1 s/paper but only works on born-digital PDFs. For scanned PDFs, formulas, or tables, install the optional [MinerU](https://github.com/opendatalab/MinerU) backend:

```bash
pip install -e ".[mineru]"
# Then auto-routing works:
paperdb ingest scanned-paper.pdf --backend auto
```

MinerU runs DocLayout-YOLO + UniMERNet + RapidTable models locally (~2 GB downloaded on first run). See [`docs/architecture.md`](./docs/architecture.md) for the full pipeline.

---

## Claude Code integration

If you use [Claude Code](https://claude.com/claude-code), this repo ships with native slash commands and subagents under `.claude/`:

| Slash command | Purpose |
|---|---|
| `/paper-ingest <pdf>` | One-shot ingest pipeline |
| `/paper-search <query>` | Hybrid retrieval with optional rerank |
| `/paper-agent <question>` | Multi-agent answer with citations |
| `/paper-stats` | Library health snapshot |

| Subagent | Use case |
|---|---|
| `paper-lit-reviewer` | Thematic review across the library |
| `paper-qa` | Focused factual / quantitative question |
| `paper-comparator` | Multi-paper alignment table |
| `paper-gap-analyst` | Unsolved problems / contradictions |
| `paper-librarian` | Ingest / analyze / maintain |

Just `cd` into the repo (or any directory with the workspace marker) and Claude Code picks them up automatically.

---

## Architecture

```
                     CLI / REST API / Streamlit UI
                                │
                       ┌────────┴─────────┐
                       │  BatchProcessor   │
                       └────────┬─────────┘
                                │
       ┌────────────────────────┼────────────────────────┐
       ▼                        ▼                        ▼
  PaperExtractor          PaperEmbedder            PaperAnalyzer
  (pymupdf | mineru)      (Qwen v4, 1024D)         (DeepSeek tier'd)
       │                        │                        │
       └────────────────────────┴────────────────────────┘
                                │
                       ┌────────┴─────────┐
                       │  SQLite (WAL)     │
                       │  - papers          │
                       │  - sections + chunks
                       │  - references_     │
                       │  - citation_locations
                       │  - section_tables / formulas / images
                       │  - shared_citations  (cross-paper)
                       │  - kg_nodes / kg_edges (graph)
                       │  - FTS5 indexes
                       └────────┬─────────┘
                                │
                       ┌────────┴─────────┐
                       │  Multi-agent     │
                       │  orchestrator    │
                       │  (DeepSeek pro,   │
                       │   thinking mode) │
                       └──────────────────┘
```

See [`docs/architecture.md`](./docs/architecture.md) for details.

---

## Status & roadmap

✅ Implemented: SQLite store, LLM analysis, embeddings (Qwen v4), three-tier RAG, cross-paper relationships, multi-agent assistant, REST API + Streamlit UI, MinerU optional backend, unified workspace

🚧 Roadmap: Docker container, NAS deployment guide (Synology / QNAP), webhook-triggered ingest, Obsidian plugin

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for the development workflow.

---

## License

[MIT](./LICENSE)

---

## Acknowledgements

- [DeepSeek](https://www.deepseek.com/) — primary LLM provider with `thinking` mode
- [Qwen](https://qwenlm.github.io/) — `text-embedding-v4` via Alibaba DashScope
- [MinerU](https://github.com/opendatalab/MinerU) — layout-aware PDF parsing
- [PyMuPDF](https://pymupdf.readthedocs.io/) — fast PDF text extraction
- [Claude Code](https://claude.com/claude-code) — Anthropic's CLI agent that built much of this
