"""
BaseAgent — generic tool-using LLM loop compatible with DeepSeek thinking mode.

Loop:
    1. Send messages + tool schemas to LLM.
    2. If the response contains tool_calls, execute each tool and append the
       results as `role=tool` messages. Re-loop.
    3. If no tool_calls, the response.content is the final answer; return it.

DeepSeek-specific: when thinking mode is on, the assistant message has
`reasoning_content` that MUST be included in subsequent requests for
multi-turn tool use (per DeepSeek docs). We use `.model_dump()` to preserve
all fields when echoing the assistant message back.

The agent never edits the DB. All tools are read-only. Safe to run
multiple agents concurrently against the same DB (SQLite WAL).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from llm_interface import LLMConfig, OpenAICompatibleProvider
from agents import tools as _tools_module
from agents.tools import TOOL_SCHEMAS, ToolContext
from intent_classifier import classify_intent, get_profile


MAX_ITERATIONS_DEFAULT = 10
MAX_TOOL_RESULT_CHARS = 8000  # truncate huge tool outputs


@dataclass
class AgentTrace:
    iterations: int = 0
    tool_calls: list[dict] = field(default_factory=list)  # [{name, args, ms}]
    reasoning_chunks: list[str] = field(default_factory=list)
    final_text: str = ""
    error: Optional[str] = None


class BaseAgent:

    # Intent policy. Subclasses override:
    #   - a profile name from intent_classifier.INTENT_PROFILES → fixed policy
    #     (e.g. lit_review always uses "related_work")
    #   - "auto" → classify_intent(sub_query) at run time
    #   - None → no section weighting (default abstract+FTS recall)
    default_intent: Optional[str] = None

    def __init__(
        self,
        ctx: ToolContext,
        *,
        name: str = "agent",
        system_prompt: str = "",
        tier: str = "complex",
        max_iterations: int = MAX_ITERATIONS_DEFAULT,
        tool_schemas: list[dict] = TOOL_SCHEMAS,
    ):
        self._ctx = ctx
        self._name = name
        self._system_prompt = system_prompt
        self._tier = tier
        self._max_iter = max_iterations
        self._tool_schemas = tool_schemas

    # ── Public API ───────────────────────────────────────────────

    def run(self, user_query: str, *, verbose: bool = False) -> tuple[str, AgentTrace]:
        trace = AgentTrace()
        # Resolve the retrieval profile for this run and install a per-call
        # cloned ctx so concurrent specialists don't trample each other.
        ctx = self._ctx_for_query(user_query, verbose=verbose)
        messages: list[dict] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": user_query})

        for it in range(self._max_iter):
            trace.iterations = it + 1
            try:
                assistant_msg, reasoning = self._chat(messages)
            except Exception as e:
                trace.error = f"LLM call failed: {e}"
                trace.final_text = f"[{self._name}] error: {e}"
                return trace.final_text, trace

            if reasoning:
                trace.reasoning_chunks.append(reasoning)

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                trace.final_text = (assistant_msg.get("content") or "").strip()
                if verbose:
                    print(f"[{self._name}] final after {it + 1} iter(s), "
                          f"{len(trace.tool_calls)} tool calls")
                return trace.final_text, trace

            # Echo the assistant message back (including reasoning_content for DS)
            messages.append(assistant_msg)

            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name") or ""
                args_str = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {}

                t0 = time.time()
                # Look up at call time so SSE monkey-patches reach us
                result = _tools_module.execute_tool(ctx, name, args)
                dt_ms = int((time.time() - t0) * 1000)
                trace.tool_calls.append({"name": name, "args": args, "ms": dt_ms})
                if verbose:
                    arg_brief = json.dumps(args, ensure_ascii=False)[:80]
                    print(f"  [{self._name}] tool {name}({arg_brief})  {dt_ms}ms")

                content = json.dumps(result, ensure_ascii=False, default=str)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = content[:MAX_TOOL_RESULT_CHARS] + '..."[truncated]"'

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": content,
                })

        trace.error = f"max iterations ({self._max_iter}) exceeded"
        # Salvage whatever the last assistant message was
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                trace.final_text = m["content"]
                break
        return trace.final_text or f"[{self._name}] gave up after {self._max_iter} iterations", trace

    # ── Intent → ctx clone with profile ─────────────────────────────

    def _ctx_for_query(self, query: str, *, verbose: bool) -> ToolContext:
        """Resolve the per-run retrieval profile and return a ctx clone
        carrying it. Returns the parent ctx unchanged when this specialist
        has no intent policy (so callers' explicit profile, if any, sticks)."""
        intent = self.default_intent
        if intent is None:
            return self._ctx
        if intent == "auto":
            profile = classify_intent(query)
        else:
            profile = get_profile(intent)
        if verbose and not profile.is_default:
            weights = ", ".join(f"{k}={v}" for k, v in profile.section_weights.items())
            print(f"  [{self._name}] intent={profile.intent} "
                  f"({profile.matched_by}); section_weights: {weights}")
        return self._ctx.with_profile(profile)

    # ── LLM call (returns full assistant message dict + reasoning) ─────────

    def _chat(self, messages: list[dict]) -> tuple[dict, str]:
        provider = self._ctx.llm
        if not isinstance(provider, OpenAICompatibleProvider):
            raise RuntimeError("BaseAgent requires OpenAICompatibleProvider for tool use")

        cfg: LLMConfig = provider._config
        tier_cfg = cfg.complex_tier if self._tier == "complex" else cfg.simple_tier

        kwargs: dict = dict(
            model=tier_cfg.model,
            messages=messages,
            tools=self._tool_schemas,
            tool_choice="auto",
            max_tokens=tier_cfg.max_tokens,
        )
        extra_body: dict = {}
        is_deepseek_like = cfg.provider in ("deepseek", "openai-compatible")
        if tier_cfg.thinking_enabled:
            if is_deepseek_like:
                extra_body["thinking"] = {"type": "enabled"}
            if tier_cfg.reasoning_effort:
                kwargs["reasoning_effort"] = tier_cfg.reasoning_effort
        else:
            if is_deepseek_like:
                extra_body["thinking"] = {"type": "disabled"}
            kwargs["temperature"] = tier_cfg.temperature
        if extra_body:
            kwargs["extra_body"] = extra_body

        resp = provider._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        msg_dict = msg.model_dump(exclude_none=True)
        # Ensure content is at least empty string (some providers strip it
        # when tool_calls are present)
        msg_dict.setdefault("content", "")
        reasoning = msg_dict.pop("reasoning_content", "") or ""
        # Keep reasoning content on the message for DeepSeek context continuity
        if reasoning and is_deepseek_like:
            msg_dict["reasoning_content"] = reasoning
        return msg_dict, reasoning
