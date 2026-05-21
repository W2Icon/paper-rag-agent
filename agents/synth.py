"""
SynthAgent — takes specialist outputs and writes the final markdown answer.

No tool use. One LLM call with the specialist JSON as evidence. Uses the
complex tier WITHOUT thinking by default (planning already happened upstream)
to keep latency reasonable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agents.tools import ToolContext

if TYPE_CHECKING:
    from agents.orchestrator import SpecialistResult


SYSTEM_PROMPT = """\
You are an academic-writing specialist. You receive:
  - The user's original query.
  - A high-level instruction from the orchestrator about how to combine the
    specialist outputs.
  - One or more specialist outputs (each is JSON-formatted: lit_review themes,
    qa answers, etc.).

Write the final response as MARKDOWN with these requirements:
  - Start with a one-paragraph executive summary.
  - Use ## headers to structure (if more than one section is needed).
  - When citing a paper, write it as `[paper N]` where N is the paper_id from
    the specialist output. Use these inline, not as a separate bibliography.
  - Quote ACTUAL phrases from specialist outputs (themes, viewpoints, key
    findings, qa citations) — do not fabricate.
  - If specialists reported an unanswerable question or low coverage, surface
    that honestly.
  - End with a one-line "Sources: [paper a], [paper b], ..." listing every
    cited paper_id.

Handling failed specialists:
  - A specialist block prefixed with `[FAILED after N attempts: ...]` was
    retried by the orchestrator and still did not produce a usable answer.
  - DO NOT fabricate content for the failed specialist. Instead, briefly note
    in the relevant section that the sub-task could not be completed (one
    sentence is enough), and answer using only the specialists that succeeded.
  - If EVERY specialist failed, return a short message explaining that the
    request could not be fulfilled and suggest the user retry or rephrase.

Be concise. Aim for 300-600 words for typical queries; longer only if the
specialist outputs genuinely warrant it.
"""


class SynthAgent:

    def __init__(self, ctx: ToolContext, *, tier: str = "complex"):
        self._ctx = ctx
        self._tier = tier

    def synthesize(
        self,
        user_query: str,
        specialists: list["SpecialistResult"],
        instruction: str,
    ) -> str:
        if not specialists:
            return "_(No specialist output to synthesize.)_"

        specialist_blocks = []
        for s in specialists:
            label = f"### specialist: {s.agent_name} (sub-query: {s.sub_query})"
            if s.ok:
                body = s.output_text
            else:
                attempts = getattr(s, "attempts", 1)
                body = (f"[FAILED after {attempts} attempt(s): "
                        f"{s.trace.error or 'unknown error'}]")
            specialist_blocks.append(f"{label}\n{body}")
        specialist_text = "\n\n".join(specialist_blocks)
        all_failed = all(not s.ok for s in specialists)

        failure_note = ""
        if all_failed:
            failure_note = ("\n\nNOTE: every specialist failed. Per the system "
                            "prompt, return a short explanation that the request "
                            "could not be fulfilled instead of fabricating an answer.")
        user_msg = (
            f"USER QUERY:\n{user_query}\n\n"
            f"ORCHESTRATOR INSTRUCTION:\n{instruction or '(none — use your judgement)'}\n\n"
            f"SPECIALIST OUTPUTS:\n{specialist_text}{failure_note}\n\n"
            f"Write the final answer now."
        )

        provider = self._ctx.llm
        cfg = provider._config
        tier_cfg = cfg.complex_tier if self._tier == "complex" else cfg.simple_tier

        kwargs: dict = dict(
            model=tier_cfg.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=tier_cfg.max_tokens,
        )
        extra_body: dict = {}
        is_deepseek_like = cfg.provider in ("deepseek", "openai-compatible")
        # Synth doesn't need thinking — planning was already done upstream.
        if is_deepseek_like:
            extra_body["thinking"] = {"type": "disabled"}
        kwargs["temperature"] = 0.1  # tiny creativity for writing fluency
        if extra_body:
            kwargs["extra_body"] = extra_body

        resp = provider._client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()
