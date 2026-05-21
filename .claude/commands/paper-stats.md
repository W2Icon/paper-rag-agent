---
description: One-screen snapshot of the paper library — counts, coverage, recent ingests.
allowed-tools: Bash, Read
---

You are giving the user a library health check. Be terse.

# Steps

1. Run:
   ```bash
   paperdb stats --json
   ```
2. Parse the JSON. Expect fields like total papers, references, sections, citation_locations, papers with title/abstract/fulltext embedding, papers analyzed, lit-review entries, shared-citation pairs.

3. (Optional) For the 3 most recently ingested papers:
   ```bash
   paperdb list --json | head -120
   ```

4. **Format the reply** as a compact table:

   ```
   ## Library snapshot
   |                       | count |
   |-----------------------|-------|
   | papers                |   …   |
   | references            |   …   |
   | sections              |   …   |
   | citations             |   …   |
   | papers w/ embeddings  |   …   |
   | papers w/ LLM summary |   …   |
   | shared-citation pairs |   …   |

   **Recent ingests**:
   - #<id> <title>  (<ingested_at>)
   - …
   ```

5. **Flag gaps**: if any of these is true, surface a one-line action hint:
   - papers without embeddings > 0 → suggest `/paper-ingest` or `paperdb embed --missing`
   - papers without LLM summary > 0 → suggest `paperdb analyze --missing`
   - shared-citation pairs == 0 and papers > 5 → suggest `paperdb cross-analyze all`

# What NOT to do
- Don't dump raw JSON.
- Don't include more than 3 recent ingests.
- Don't probe individual papers (use `paperdb show <id>` for that — separate flow).
