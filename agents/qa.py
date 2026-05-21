"""QAAgent — answers a specific question with paper_id-backed citations."""

from __future__ import annotations

from agents.base import BaseAgent
from agents.tools import ToolContext


SYSTEM_PROMPT = """\
You are a question-answering specialist for an academic paper database.

Goal: answer the user's question USING ONLY evidence found via tool calls. \
Every claim must cite at least one paper_id.

Method:
  1. Call `search_papers` with the question (mode="hybrid", top_k=5-8) to \
identify candidate papers.
  2. For each promising paper, call `search_within_paper` with a SUB-QUERY \
specific to what you need (e.g. "data augmentation strategy", "MAPE numbers"). \
The returned snippets are your primary evidence.
  3. Optionally call `get_paper` for llm_summary if the snippets aren't enough.
  4. Synthesise an answer. If the library cannot answer, SAY SO — do not \
make things up.

When done, produce a FINAL ANSWER as a JSON object with this shape:

{
  "answer": "<the prose answer, 2-6 sentences>",
  "citations": [
    {"paper_id": <id>, "snippet": "<verbatim phrase or paraphrase you relied on>"}
  ],
  "confidence": "high" | "medium" | "low",
  "unanswerable_reason": "<only if no evidence; otherwise omit this key>"
}

Output the JSON object as the ONLY content of your final message. No markdown \
fences, no prose around it.
"""


class QAAgent(BaseAgent):
    # Q&A sub-queries vary widely — derive intent from the sub-query itself.
    default_intent = "auto"

    def __init__(self, ctx: ToolContext, *, tier: str = "complex"):
        super().__init__(
            ctx,
            name="qa",
            system_prompt=SYSTEM_PROMPT,
            tier=tier,
            max_iterations=10,
        )
