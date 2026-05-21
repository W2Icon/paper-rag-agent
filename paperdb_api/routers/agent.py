"""Agent endpoint with Server-Sent Events streaming.

Emits events as the orchestration progresses:
  - event=plan         : after orchestrator returns its JSON plan
  - event=specialist_start  : a specialist agent begins
  - event=tool_call    : a specialist calls a tool (name + ms)
  - event=specialist_done   : a specialist finishes (with output)
  - event=synth_chunk  : streaming chunks of the final markdown
  - event=done         : final answer (full text) + total stats

Frontend consumes via EventSource (or httpx-sse). Each event is JSON-serialised
in `data:` per SSE spec.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from paperdb_api.deps import get_db, get_llm_provider_cached, get_embedder_cached
from paperdb_api.schemas import AgentRequest


router = APIRouter(tags=["agent"])


def _evt(name: str, payload: dict) -> dict:
    """SSE event helper: sse-starlette expects {event, data} dicts."""
    return {"event": name, "data": json.dumps(payload, ensure_ascii=False)}


@router.post("/agent")
async def run_agent(req: AgentRequest, db=Depends(get_db)):
    provider = get_llm_provider_cached()
    if provider is None:
        raise HTTPException(503, "Agent requires LLM_PROVIDER + LLM_API_KEY")

    # Eager embedder check so we fail fast (used by tools.search_papers etc.)
    try:
        embedder = get_embedder_cached()
    except HTTPException:
        embedder = None  # tools that don't need embedder will still work

    from agents.tools import ToolContext
    from agents.orchestrator import Orchestrator, SPECIALISTS
    from agents.base import BaseAgent

    ctx = ToolContext(db=db, llm=provider, embedder=embedder)

    # Use a thread-safe queue to bridge synchronous LLM/tool calls to the
    # async SSE generator.
    events: queue.Queue[dict | None] = queue.Queue()

    def emit(name: str, payload: dict) -> None:
        events.put(_evt(name, payload))

    def emit_done() -> None:
        events.put(None)  # sentinel

    def worker() -> None:
        try:
            if req.task != "auto":
                # Single specialist directly
                spec_map = {"lit-review": "lit_review", "qa": "qa",
                             "compare": "compare", "gap": "gap"}
                agent_name = spec_map[req.task]
                AgentCls = SPECIALISTS[agent_name]
                emit("plan", {
                    "plan": [{"agent": agent_name, "sub_query": req.query}],
                    "synth_instruction": "(single specialist; output passed through verbatim)",
                })
                _run_one(AgentCls, agent_name, req.query, ctx, emit)
                # No synth — emit specialist output as final
                # (collected below via specialist_done event)
                emit("done", {"final_answer": "", "via": "single-specialist"})
            else:
                # Full orchestration with parallel dispatch
                _run_orchestrated(ctx, req, emit)
        except Exception as e:
            emit("error", {"message": str(e)})
        finally:
            emit_done()

    threading.Thread(target=worker, daemon=True).start()

    async def stream() -> AsyncIterator[dict]:
        import asyncio
        loop = asyncio.get_event_loop()
        while True:
            evt = await loop.run_in_executor(None, events.get)
            if evt is None:
                break
            yield evt

    return EventSourceResponse(stream())


# ── Worker helpers ────────────────────────────────────────────────


def _run_one(AgentCls, name: str, sub_query: str, ctx, emit) -> str:
    """Run a single specialist with per-tool-call event emission."""
    agent = AgentCls(ctx)
    # Monkeypatch the agent's tool execution to emit events. We do this by
    # wrapping execute_tool at the module level via thread-local storage.
    emit("specialist_start", {"agent": name, "sub_query": sub_query})
    t0 = time.time()

    from agents import tools as _tools
    original = _tools.execute_tool

    def wrapped(ctx_, tool_name: str, args: dict):
        ts = time.time()
        result = original(ctx_, tool_name, args)
        emit("tool_call", {
            "agent": name, "tool": tool_name, "args": args,
            "ms": int((time.time() - ts) * 1000),
        })
        return result

    _tools.execute_tool = wrapped
    try:
        text, trace = agent.run(sub_query, verbose=False)
    finally:
        _tools.execute_tool = original

    emit("specialist_done", {
        "agent": name,
        "output": text,
        "iterations": trace.iterations,
        "tool_calls": len(trace.tool_calls),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "ok": trace.error is None,
        "error": trace.error,
    })
    return text


def _run_orchestrated(ctx, req: AgentRequest, emit) -> None:
    """Full orchestration: plan → parallel specialists → synth (streamed)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from agents.orchestrator import Orchestrator, SpecialistResult, SPECIALISTS
    from agents.synth import SynthAgent
    from agents import tools as _tools
    from agents.base import AgentTrace

    # Build orchestrator + get plan
    orch = Orchestrator(ctx)
    plan, synth_instr, reasoning = orch._plan(req.query)
    emit("plan", {
        "plan": plan,
        "synth_instruction": synth_instr,
        "reasoning_preview": (reasoning or "")[:400],
    })

    if not plan:
        emit("done", {"final_answer": "Orchestrator produced empty plan.",
                       "via": "empty-plan"})
        return

    # Patch tools.execute_tool to emit per-call events; thread-safe because
    # all worker threads use the same patched function.
    original = _tools.execute_tool
    _agent_name_local = threading.local()

    def wrapped(ctx_, tool_name: str, args: dict):
        ts = time.time()
        result = original(ctx_, tool_name, args)
        agent_name = getattr(_agent_name_local, "name", "?")
        emit("tool_call", {
            "agent": agent_name, "tool": tool_name, "args": args,
            "ms": int((time.time() - ts) * 1000),
        })
        return result

    _tools.execute_tool = wrapped

    specialist_results: list[SpecialistResult] = []
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(plan))) as ex:
            futures = {}
            for step in plan:
                AgentCls = SPECIALISTS[step["agent"]]
                agent = AgentCls(ctx)
                emit("specialist_start", {"agent": step["agent"], "sub_query": step["sub_query"]})

                def runner(_agent=agent, _step=step):
                    _agent_name_local.name = _step["agent"]
                    return _agent.run(_step["sub_query"], verbose=False)

                fut = ex.submit(runner)
                futures[fut] = step

            for fut in as_completed(futures):
                step = futures[fut]
                try:
                    text, trace = fut.result()
                    ok = trace.error is None
                    specialist_results.append(SpecialistResult(
                        agent_name=step["agent"], sub_query=step["sub_query"],
                        output_text=text, trace=trace, ok=ok,
                    ))
                    emit("specialist_done", {
                        "agent": step["agent"], "output": text,
                        "iterations": trace.iterations,
                        "tool_calls": len(trace.tool_calls),
                        "ok": ok, "error": trace.error,
                    })
                except Exception as e:
                    emit("specialist_done", {
                        "agent": step["agent"], "output": f"[error: {e}]",
                        "ok": False, "error": str(e),
                    })
                    specialist_results.append(SpecialistResult(
                        agent_name=step["agent"], sub_query=step["sub_query"],
                        output_text=f"[error: {e}]",
                        trace=AgentTrace(error=str(e)), ok=False,
                    ))
    finally:
        _tools.execute_tool = original

    if req.no_synth or not specialist_results:
        # Pass specialists through as the final
        bundle = "\n\n".join(
            f"## {s.agent_name}\n{s.output_text}" for s in specialist_results
        )
        emit("done", {"final_answer": bundle, "via": "no-synth"})
        return

    # Synth — streaming
    emit("synth_start", {})
    full_text = _run_synth_streaming(ctx, req.query, specialist_results,
                                       synth_instr, emit)
    emit("done", {"final_answer": full_text, "via": "synth"})


def _run_synth_streaming(ctx, user_query: str, specialists, instruction: str,
                          emit) -> str:
    """Like SynthAgent but using OpenAI streaming so we can emit per-chunk events."""
    from agents.synth import SYSTEM_PROMPT

    if not specialists:
        return ""

    specialist_blocks = []
    for s in specialists:
        label = f"### specialist: {s.agent_name} (sub-query: {s.sub_query})"
        body = s.output_text if s.ok else f"[failed: {s.trace.error}]"
        specialist_blocks.append(f"{label}\n{body}")
    specialist_text = "\n\n".join(specialist_blocks)

    user_msg = (
        f"USER QUERY:\n{user_query}\n\n"
        f"ORCHESTRATOR INSTRUCTION:\n{instruction or '(none — use your judgement)'}\n\n"
        f"SPECIALIST OUTPUTS:\n{specialist_text}\n\n"
        f"Write the final answer now."
    )

    provider = ctx.llm
    cfg = provider._config
    tier_cfg = cfg.complex_tier

    kwargs = dict(
        model=tier_cfg.model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=tier_cfg.max_tokens,
        stream=True,
    )
    extra_body: dict = {}
    if cfg.provider in ("deepseek", "openai-compatible"):
        extra_body["thinking"] = {"type": "disabled"}
    kwargs["temperature"] = 0.1
    if extra_body:
        kwargs["extra_body"] = extra_body

    stream = provider._client.chat.completions.create(**kwargs)
    pieces: list[str] = []
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None) or ""
        if content:
            pieces.append(content)
            emit("synth_chunk", {"chunk": content})
    return "".join(pieces)
