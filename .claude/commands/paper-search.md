---
description: Three-tier retrieval (vector + FTS + RRF + optional LLM rerank) over the paper library.
argument-hint: <query> [--rerank] [--mode hybrid|vector|fts|graph|all|auto] [--tag X]
allowed-tools: Bash, Read
---

You are running paper retrieval.

# Arguments
`$ARGUMENTS` — natural-language query, plus any flags from the schema below.

# Defaults
- `--mode hybrid` (vector + FTS + RRF)
- `--rerank` is OFF by default for speed. Turn it on when the user wants precision and there are >10 candidates.
- `--top-k 10`

# How to run

1. **Construct the command** by appending any user-supplied flags to:
   ```bash
   paperdb search-rag "<query>" --json
   ```
   - If the user explicitly asks for "cross-lingual" / "semantic only", set `--mode vector`.
   - If they want an exact-term match (e.g. an acronym or a specific name), set `--mode fts`.
   - If they say "rerank" / "best 5" / "more precise", add `--rerank --top-k 5`.
   - Honor `--tag`, `--year-from`, `--year-to` if mentioned.

2. **Parse the JSON output**. It's an array of `{id, title, score, ...}`.

3. **Format the reply** as a numbered list:
   ```
   1. **<title>**  ·  paper #<id>  ·  score=<score>
      <abstract first 200 chars …>
   ```
   Cap at 10 entries unless user asked for more.

4. **If 0 results**, say so explicitly and suggest:
   - Try `--mode vector` (cross-lingual / paraphrased)
   - Try `--mode fts` (exact term)
   - Try `/paper-ingest` to grow the library

# What NOT to do
- Don't synthesize content from the abstracts into a "summary" — that's the job of `/paper-agent`. Just rank and list.
- Don't drop the paper id; the user needs it for follow-up queries.
