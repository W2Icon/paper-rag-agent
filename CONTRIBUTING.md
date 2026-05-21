# Contributing to paper-rag-agent

Thanks for considering a contribution. This is a hobby/research project — keep things small, focused, and respectful of the maintainer's bandwidth.

## Dev setup

```bash
git clone https://github.com/W2Icon/paper-rag-agent.git
cd paper-rag-agent

# Create a venv (recommended — paperdb pulls in torch, transformers, etc.)
python -m venv .venv
source .venv/bin/activate

# Install with dev + all optional groups
pip install -e ".[api,ui,mineru,dev]"

# Bootstrap a workspace for testing
paperdb init ./dev-workspace
echo "PAPERDB_WORKSPACE=$(pwd)/dev-workspace" >> .env
```

Add your own DeepSeek + DashScope API keys to `dev-workspace/config.toml` (the file is gitignored).

## Running

```bash
# CLI
paperdb --help
paperdb workspace show

# REST API + Streamlit
paperdb start
```

## What I'm happy to merge

✅ Bug fixes with a reproducer  
✅ New LLM providers (Claude, Gemini, local Ollama, etc.) — drop in a new `LLMProvider` impl  
✅ Better parsers, new optional extras under `pyproject.toml`  
✅ Docs improvements, typo fixes  
✅ New CLI subcommands that follow the existing pattern (see `cli.py`)  
✅ Bug fixes for the multi-agent prompts / tool definitions

## What needs discussion first (open an issue)

⚠ Schema changes (we're at v6 — every change needs a migration)  
⚠ New required dependencies (avoid; prefer optional extras)  
⚠ Web UI redesigns  
⚠ API contract changes (the SSE event stream is consumed by the UI)

## What probably won't get merged

❌ Hosting / SaaS variants (this is intentionally local-only)  
❌ Telemetry / analytics  
❌ Auth layers on the REST API (LAN-only by design; if you need auth, run behind a reverse proxy)  
❌ Hard-coded vendor lock-in (no AWS-only, OpenAI-only paths)

## Code style

- Black-compatible formatting; we use `ruff` (`ruff check . && ruff format .`)
- Type hints on public functions (mypy strict isn't enforced — be reasonable)
- No new files in repo root unless necessary; new modules go under their existing namespace
- Commit messages: imperative mood, ~70 char first line, body explains *why* not *what*

## Testing

We don't have a real test suite yet (TODO). For now:
- Run `python scripts/calibrate_backend_threshold.py <pdf-dir>` to validate backend routing
- Manual smoke test: `paperdb ingest test.pdf --quick` should round-trip a small paper
- Schema migrations: test against a fresh DB AND a copy of an older DB

PR title prefix conventions:
- `fix:` — bug fix
- `feat:` — new feature
- `docs:` — README / docstrings only
- `chore:` — refactor / cleanup / build
- `perf:` — optimisation

## Security

If you find a security issue (key leakage, SSRF, injection in the agent layer), **don't open a public issue**. Email the maintainer (see `pyproject.toml`).

## Code of conduct

Be kind. Disagree on technical merits, not people. That's it.
