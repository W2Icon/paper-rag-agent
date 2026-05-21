---
description: Multi-agent Q&A over the paper library. Orchestrator dispatches lit-review / QA / compare / gap specialists in parallel and synthesizes a final report.
argument-hint: <question> [--task auto|lit-review|qa|compare|gap] [--show-plan]
allowed-tools: Bash, Read
---

You are running the multi-agent assistant.

# Arguments
`$ARGUMENTS` — the user's natural-language question, plus optional flags.

# Routing principle
Three valid paths — pick by examining the user's intent:

1. **Direct Python orchestrator** (default) — let `paperdb agent` plan + dispatch:
   ```bash
   paperdb agent "<question>"
   ```
   Use this when the user asks one cohesive question. Cheapest, most reliable.

2. **Single specialist** — when the user is explicit ("just compare …", "give me a literature review"):
   ```bash
   paperdb agent "<question>" --task <lit-review|qa|compare|gap>
   ```

3. **Claude-side parallel dispatch** — when the user has 2+ truly independent questions, dispatch the matching Claude Code subagents (`paper-lit-reviewer`, `paper-qa`, `paper-comparator`, `paper-gap-analyst`) in parallel from the parent. Don't do this from inside this slash command; advise the parent.

# Steps for paths 1 and 2

1. Pass `$ARGUMENTS` verbatim into `paperdb agent`. Preserve quotation and ids.
2. Add `--show-plan` if the user asked "what would you do" or wants to see routing.
3. Add `--show-trace` if a previous run gave a suspicious answer and the user wants to debug.
4. The command prints markdown on stdout. Return it as-is — DO NOT re-summarize.

# Timeouts
- Default budget: 180s. If you suspect a long task (>3 specialists), prefix with `timeout 240 paperdb agent ...`.
- If it times out, retry once with a single `--task <best-guess>`.

# What NOT to do
- Don't strip the `[paper N]` citations from the output.
- Don't merge the orchestrator's output with your own commentary — show the report cleanly, then add at most one trailing line if you have an explicit follow-up suggestion.
