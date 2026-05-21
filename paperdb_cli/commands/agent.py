"""`paperdb agent` — multi-agent assistant entry point."""

from __future__ import annotations

import json
import sys

from db_connection import DatabaseConnection


def cmd_agent(args, db: DatabaseConnection) -> None:
    from llm_interface import LLMConfig, OpenAICompatibleProvider, get_llm_provider
    from embedder import PaperEmbedder
    from agents.tools import ToolContext
    from agents.orchestrator import Orchestrator

    try:
        cfg = LLMConfig.from_env()
        provider = get_llm_provider(cfg)
    except Exception as e:
        print(f"LLM init failed: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(provider, OpenAICompatibleProvider):
        print("Agent requires LLM_PROVIDER=deepseek (or openai-compatible). "
              f"Got: {cfg.provider}", file=sys.stderr)
        sys.exit(2)

    ctx = ToolContext(db=db, llm=provider, embedder=PaperEmbedder(provider))
    orch = Orchestrator(ctx)

    task = args.task
    if task in ("lit-review", "qa", "compare", "gap"):
        agent_name = {"lit-review": "lit_review", "qa": "qa",
                       "compare": "compare", "gap": "gap"}[task]
        print(f"Running specialist directly: {agent_name}")
        result = orch.run_single_specialist(agent_name, args.query, verbose=args.verbose)
        if args.show_trace:
            _print_trace(result)
        print("\n--- specialist output ---\n")
        print(result.output_text)
        return

    # auto: full orchestration
    print(f"Orchestrating: {args.query!r}")
    out = orch.run(args.query, verbose=args.verbose)

    if args.show_plan:
        print("\n--- orchestrator plan ---")
        for i, step in enumerate(out.plan, 1):
            print(f"  {i}. {step['agent']:<11} → {step['sub_query']}")
        print(f"  synth_instruction: {out.synth_instruction}")

    if args.show_trace:
        for s in out.specialists:
            _print_trace(s)

    if args.no_synth:
        print("\n--- specialist outputs (no synth) ---")
        for s in out.specialists:
            print(f"\n=== {s.agent_name} ({s.sub_query[:60]}) {'✓' if s.ok else '✗'} ===")
            print(s.output_text)
        return

    print("\n" + "=" * 70)
    print(out.final_answer)
    print("=" * 70)


def _print_trace(specialist_result) -> None:
    t = specialist_result.trace
    print(f"\n--- trace: {specialist_result.agent_name} ---")
    print(f"  iterations: {t.iterations}")
    print(f"  tool calls: {len(t.tool_calls)}")
    for tc in t.tool_calls:
        arg_brief = json.dumps(tc["args"], ensure_ascii=False)[:60]
        print(f"    • {tc['name']}({arg_brief})  {tc['ms']}ms")
    if t.error:
        print(f"  error: {t.error}")
