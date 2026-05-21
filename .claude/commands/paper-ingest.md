---
description: Ingest a PDF (or folder of PDFs) into the paperdb library, embed it, and run LLM analysis.
argument-hint: <pdf-path-or-dir> [--no-analyze]
allowed-tools: Bash, Read
---

You are running the paper ingestion pipeline.

# Arguments
`$ARGUMENTS` — path to a PDF file or a directory of PDFs. Optional flag `--no-analyze` skips the post-ingest LLM stage.

# Steps

1. **Validate input**
   - Resolve the path. If it doesn't exist, stop and tell the user.
   - If it's a directory, you'll add `--recursive`.

2. **Ingest + embed** (one shot)
   ```bash
   # single file
   paperdb ingest "<path>" --embed -v
   # OR folder
   paperdb ingest "<dir>" --recursive --embed -v
   ```

3. **LLM analysis** (unless user passed `--no-analyze`)
   ```bash
   paperdb analyze --missing
   ```
   This fills `llm_summary`, ref relevance scores, and lit-review viewpoint entries for any paper that doesn't have them yet.

4. **Report**
   - Run `paperdb stats --json` and extract: total papers, papers with embeddings, papers with LLM summary, papers ingested today.
   - Reply with a compact summary, e.g.:
     > Added **N** papers. Library now has **X / Y** with embeddings, **A / Y** with LLM summaries. Latest id: **Z**.

# Failure handling
- "duplicate file hash" → that paper is already ingested, NOT an error. Report which id it's stored as.
- LLM API error → skip step 3 and tell the user; the paper is still safely ingested.
- Large folders (>20 PDFs) → run step 2 with `-v` and surface the count of failed files at the end.
