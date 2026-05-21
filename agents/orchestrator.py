"""
Orchestrator — top-level planner that decides which specialist agents to
dispatch (in parallel) and routes their outputs to SynthAgent for the final
report.

Design:
  - The orchestrator calls the LLM ONCE for planning, in v4-pro thinking mode.
  - The LLM returns a JSON plan: which agents to invoke + sub-queries.
  - We execute the specialists in parallel via ThreadPoolExecutor.
  - SynthAgent (in a separate call) writes the final answer from collected
    specialist outputs.

This is cleaner than function-calling for the planning step because the plan
is a fixed-shape decision (which agents × sub-queries), not an open-ended
tool-use trace.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from agents.base import AgentTrace
from agents.compare import CompareAgent
from agents.gap import GapAgent
from agents.lit_review import LitReviewAgent
from agents.qa import QAAgent
from agents.synth import SynthAgent
from agents.tools import ToolContext


PLAN_SYSTEM_PROMPT = """\
You are the orchestrator of a multi-agent academic assistant.

Available specialist agents:
  - lit_review : thematic review of papers in the library on a topic.
                 Use for "what does my library say about X?" — overview/landscape.
  - qa         : focused answer with paper_id citations.
                 Use for specific factual/quantitative questions: WHAT, HOW MUCH,
                 WHICH paper says ...
  - compare    : structured row×column comparison across 2-5 named (or
                 retrievable) papers. Use when the user wants a table-like view
                 aligning methods/datasets/results across papers.
  - gap        : finds open questions, limitations, contradictions, and missing
                 perspectives. Use for "what's NOT solved", "what's MISSING",
                 "future work", "research gaps".

Your job: read the user query and output a plan that uses ONE OR MORE of the
specialists. Each specialist can be invoked independently with its own
sub-query (in natural language). Specialists run IN PARALLEL.

Then describe what the Synth agent should do with their outputs.

Return a JSON object EXACTLY in this shape (no markdown fences, no prose):

{
  "plan": [
    {"agent": "lit_review" | "qa" | "compare" | "gap",
     "sub_query": "<full natural-language sub-query>"}
  ],
  "synth_instruction": "<2-3 sentences telling Synth how to combine the specialist outputs>"
}

Heuristics:
  - One agent for simple queries; multiple only when the query genuinely has
    distinct parts.
  - "Summarize X and tell me MAPE numbers" → lit_review + qa.
  - "Compare papers 1, 2, 3 by methodology and find what's still open" →
    compare + gap.
  - "What are the research gaps in EV energy estimation?" → gap (alone).
  - "Compare papers 1 and 3" → compare (alone).
  - Sub-queries must be self-contained: pass any paper_ids, topic, constraints
    from the user query verbatim.
  - Distinct sub-queries that target the same agent are fine but unusual —
    prefer one specialist call per agent with a richer sub-query.
"""


SPECIALISTS = {
    "lit_review": LitReviewAgent,
    "qa": QAAgent,
    "compare": CompareAgent,
    "gap": GapAgent,
}


SPECIALIST_MAX_ATTEMPTS = 2  # one retry on failure


@dataclass
class SpecialistResult:
    agent_name: str
    sub_query: str
    output_text: str
    trace: AgentTrace
    ok: bool = True
    attempts: int = 1


@dataclass
class OrchestrationResult:
    plan: list[dict] = field(default_factory=list)
    synth_instruction: str = ""
    specialists: list[SpecialistResult] = field(default_factory=list)
    final_answer: str = ""
    planning_reasoning: str = ""


class Orchestrator:

    def __init__(self, ctx: ToolContext, *, max_workers: int = 4):
        self._ctx = ctx
        self._max_workers = max_workers

    def run(self, user_query: str, *, verbose: bool = False) -> OrchestrationResult:
        result = OrchestrationResult()

        # ── Step 1: plan ─────────────────────────────────────────
        plan, synth_instr, reasoning = self._plan(user_query)
        result.plan = plan
        result.synth_instruction = synth_instr
        result.planning_reasoning = reasoning
        if verbose:
            print(f"\nPlan: {len(plan)} specialist(s) to dispatch")
            for step in plan:
                print(f"  → {step['agent']}: {step['sub_query'][:80]}")

        if not plan:
            result.final_answer = "Orchestrator produced empty plan; no work to do."
            return result

        # ── Step 2: parallel specialist execution ───────────────
        specialists = self._dispatch_parallel(plan, verbose=verbose)
        result.specialists = specialists

        # ── Step 3: synth ───────────────────────────────────────
        synth = SynthAgent(self._ctx)
        result.final_answer = synth.synthesize(
            user_query=user_query,
            specialists=specialists,
            instruction=synth_instr,
        )
        return result

    # ── Planning step (one LLM call, JSON output) ────────────────

    def _plan(self, user_query: str) -> tuple[list[dict], str, str]:
        provider = self._ctx.llm
        cfg = provider._config
        tier_cfg = cfg.complex_tier  # always use complex for planning

        extra_body: dict = {}
        kwargs: dict = dict(
            model=tier_cfg.model,
            messages=[
                {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": user_query},
            ],
            max_tokens=tier_cfg.max_tokens,
            response_format={"type": "json_object"},
        )
        if tier_cfg.thinking_enabled:
            extra_body["thinking"] = {"type": "enabled"}
            if tier_cfg.reasoning_effort:
                kwargs["reasoning_effort"] = tier_cfg.reasoning_effort
        else:
            extra_body["thinking"] = {"type": "disabled"}
            kwargs["temperature"] = tier_cfg.temperature
        if extra_body:
            kwargs["extra_body"] = extra_body

        resp = provider._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        reasoning = getattr(msg, "reasoning_content", "") or ""

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to recover JSON from fenced or wrapped output
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(text[start:end + 1])
            else:
                raise RuntimeError(f"Orchestrator returned non-JSON: {text[:200]}")

        plan_raw = data.get("plan") or []
        plan = []
        for step in plan_raw:
            agent = str(step.get("agent", "")).strip().lower()
            sub_q = str(step.get("sub_query", "")).strip()
            if agent in SPECIALISTS and sub_q:
                plan.append({"agent": agent, "sub_query": sub_q})
        instr = str(data.get("synth_instruction", "")).strip()
        return plan, instr, reasoning

    # ── Parallel dispatch ────────────────────────────────────────

    def _run_one_specialist(self, step: dict, *, verbose: bool) -> SpecialistResult:
        """Run a specialist with up to SPECIALIST_MAX_ATTEMPTS attempts.

        Retries cover both LLM transport errors (raised exceptions) and
        terminal `trace.error` states from BaseAgent (e.g. max-iterations
        exceeded, JSON-decode failures). We instantiate a fresh agent on
        each attempt so state can't leak between tries.
        """
        AgentCls = SPECIALISTS[step["agent"]]
        last_exc: Optional[Exception] = None
        last_trace: Optional[AgentTrace] = None
        last_text = ""
        for attempt in range(1, SPECIALIST_MAX_ATTEMPTS + 1):
            try:
                agent = AgentCls(self._ctx)
                text, trace = agent.run(step["sub_query"], verbose=verbose)
                if trace.error is None:
                    return SpecialistResult(
                        agent_name=step["agent"],
                        sub_query=step["sub_query"],
                        output_text=text,
                        trace=trace,
                        ok=True,
                        attempts=attempt,
                    )
                last_trace = trace
                last_text = text
                if verbose:
                    print(f"  [orchestrator] {step['agent']} attempt {attempt}/"
                          f"{SPECIALIST_MAX_ATTEMPTS} failed: {trace.error}")
            except Exception as e:
                last_exc = e
                if verbose:
                    print(f"  [orchestrator] {step['agent']} attempt {attempt}/"
                          f"{SPECIALIST_MAX_ATTEMPTS} raised: {e}")
        if last_exc is not None:
            return SpecialistResult(
                agent_name=step["agent"],
                sub_query=step["sub_query"],
                output_text=f"[error: {last_exc}]",
                trace=AgentTrace(error=str(last_exc)),
                ok=False,
                attempts=SPECIALIST_MAX_ATTEMPTS,
            )
        return SpecialistResult(
            agent_name=step["agent"],
            sub_query=step["sub_query"],
            output_text=last_text or "[no output]",
            trace=last_trace or AgentTrace(error="unknown failure"),
            ok=False,
            attempts=SPECIALIST_MAX_ATTEMPTS,
        )

    def _dispatch_parallel(self, plan: list[dict], *,
                            verbose: bool) -> list[SpecialistResult]:
        results: list[SpecialistResult] = []
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(plan))) as ex:
            futures = {
                ex.submit(self._run_one_specialist, step, verbose=verbose): step
                for step in plan
            }
            for fut in as_completed(futures):
                step = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    # Defensive: _run_one_specialist already catches, but if a
                    # future itself errors (e.g. cancelled), don't lose the slot.
                    results.append(SpecialistResult(
                        agent_name=step["agent"],
                        sub_query=step["sub_query"],
                        output_text=f"[error: {e}]",
                        trace=AgentTrace(error=str(e)),
                        ok=False,
                        attempts=SPECIALIST_MAX_ATTEMPTS,
                    ))
        # Preserve plan order
        order = {s["agent"] + s["sub_query"]: i for i, s in enumerate(plan)}
        results.sort(key=lambda r: order.get(r.agent_name + r.sub_query, 99))
        return results

    # ── Direct specialist invocation (skip orchestration) ────────

    def run_single_specialist(self, agent_name: str, query: str,
                               *, verbose: bool = False) -> SpecialistResult:
        if agent_name not in SPECIALISTS:
            raise ValueError(f"unknown agent: {agent_name}. "
                             f"Available: {list(SPECIALISTS)}")
        return self._run_one_specialist(
            {"agent": agent_name, "sub_query": query}, verbose=verbose
        )
