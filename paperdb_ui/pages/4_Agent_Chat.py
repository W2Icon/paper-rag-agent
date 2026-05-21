"""Agent Chat — natural-language research queries with SSE streaming.

Architecture (page-navigation safe):

  The SSE consumption runs in a background daemon thread. The thread appends
  events to a plain dict stored in `st.session_state.agent` — so switching
  pages does NOT kill the agent run. When the user returns to this page, we
  re-render the full UI deterministically by replaying the event list.

  While the agent is running we self-poll via `time.sleep + st.rerun` (~400ms)
  so new events show up live. When done, polling stops automatically.
"""

import json
import threading
import time

import streamlit as st

from paperdb_ui.api_client import agent_stream
from paperdb_ui.i18n import t
from paperdb_ui.theme import (
    BORDER, PRIMARY, SURFACE, TEXT_MUTED,
    apply_theme, hero, section_label,
)


apply_theme(page_title="paperdb · Agent", layout="wide")
hero(
    title=t("agent.title"),
    eyebrow=t("agent.eyebrow"),
    dek=t("agent.dek"),
)


# ── Session-state store ───────────────────────────────────────────


def _ensure_store() -> dict:
    """The store is a plain dict held in st.session_state — the bg thread
    mutates it directly (no Streamlit API calls from inside the thread)."""
    if "agent" not in st.session_state:
        st.session_state.agent = {
            "running": False,
            "query": "",
            "task": "auto",
            "no_synth": False,
            "events": [],          # list[tuple[str, dict]]
            "t_start": 0.0,
            "t_end": 0.0,
            "thread": None,
        }
    return st.session_state.agent


def _agent_worker(store: dict, query: str, task: str, no_synth: bool) -> None:
    """Runs in a daemon thread. Pushes events into store['events']."""
    try:
        for event, payload in agent_stream(query, task=task, no_synth=no_synth):
            store["events"].append((event, payload))
            if event in ("done", "error"):
                break
    except Exception as e:
        store["events"].append(("error", {"message": f"{type(e).__name__}: {e}"}))
    finally:
        store["t_end"] = time.time()
        store["running"] = False


# ── Sidebar ───────────────────────────────────────────────────────


store = _ensure_store()

with st.sidebar:
    section_label(t("agent.task"))
    task = st.radio(
        t("agent.task"),
        ["auto", "lit-review", "qa", "compare", "gap"],
        index=0,
        label_visibility="collapsed",
        help=t("agent.task.help"),
        disabled=store["running"],
    )
    no_synth = st.checkbox(
        t("agent.no_synth"), value=False,
        help=t("agent.no_synth.help"),
        disabled=store["running"],
    )
    if store["query"] and not store["running"]:
        if st.button(t("agent.clear"), use_container_width=True):
            st.session_state.agent = {
                "running": False, "query": "", "task": "auto", "no_synth": False,
                "events": [], "t_start": 0.0, "t_end": 0.0, "thread": None,
            }
            st.rerun()


# ── Input area ────────────────────────────────────────────────────


query = st.text_area(
    "Your question",
    value=store["query"] if store["running"] else "",
    placeholder=t("agent.question.placeholder"),
    height=110,
    label_visibility="collapsed",
    disabled=store["running"],
)

cols = st.columns([1, 6])
with cols[0]:
    run_btn = st.button(
        t("agent.ask"), type="primary",
        disabled=(not query.strip()) or store["running"],
    )
with cols[1]:
    if store["running"]:
        st.caption(t("agent.running_caption"))


# ── Kick off a new run ────────────────────────────────────────────


if run_btn and query.strip() and not store["running"]:
    store["running"] = True
    store["query"] = query
    store["task"] = task
    store["no_synth"] = no_synth
    store["events"] = []
    store["t_start"] = time.time()
    store["t_end"] = 0.0
    th = threading.Thread(
        target=_agent_worker,
        args=(store, query, task, no_synth),
        daemon=True,
    )
    store["thread"] = th
    th.start()
    # Immediate rerun so the rest of the UI renders against the new state
    st.rerun()


# ── Render helpers ────────────────────────────────────────────────


def _step_pill(text: str, kind: str = "neutral") -> str:
    colors = {
        "neutral": (TEXT_MUTED, BORDER),
        "active":  (PRIMARY,    "#E5C9BC"),
    }
    fg, bg_border = colors.get(kind, colors["neutral"])
    return (f'<span style="display:inline-block;font-size:0.78rem;'
            f'font-weight:600;letter-spacing:0.04em;text-transform:uppercase;'
            f'color:{fg};background:{SURFACE};border:1px solid {bg_border};'
            f'border-radius:999px;padding:0.18rem 0.65rem;margin-right:0.45rem">'
            f'{text}</span>')


AGENT_LABEL = {
    "lit_review": "LitReview",
    "qa": "QA",
    "compare": "Compare",
    "gap": "Gap",
}


def _fmt_args(args: dict, max_len: int = 80) -> str:
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _replay(events: list[tuple[str, dict]], running: bool,
            elapsed_s: float) -> None:
    """Pure function: events → UI. Called on every rerun."""

    plan_evt: dict | None = None
    spec_started: dict[str, dict] = {}
    spec_calls: dict[str, list[dict]] = {}
    spec_done: dict[str, dict] = {}
    synth_chunks: list[str] = []
    synth_started = False
    final_event: dict | None = None
    error_msg: str | None = None
    tool_call_total = 0

    for ev, p in events:
        if ev == "plan":
            plan_evt = p
        elif ev == "specialist_start":
            spec_started[p["agent"]] = p
            spec_calls.setdefault(p["agent"], [])
        elif ev == "tool_call":
            spec_calls.setdefault(p.get("agent", "?"), []).append(p)
            tool_call_total += 1
        elif ev == "specialist_done":
            spec_done[p["agent"]] = p
        elif ev == "synth_start":
            synth_started = True
        elif ev == "synth_chunk":
            synth_chunks.append(p.get("chunk", ""))
        elif ev == "done":
            final_event = p
        elif ev == "error":
            error_msg = p.get("message", "unknown")

    # ── Top-level status banner ──────────────────────────────────
    if error_msg:
        top_label = t("agent.status.error", msg=error_msg)
        top_state = "error"
        top_expanded = True
    elif final_event:
        n_specs = len(spec_done)
        via = final_event.get("via", "synth")
        top_label = t(
            "agent.status.done",
            s=elapsed_s, n_specs=n_specs, n_calls=tool_call_total, via=via,
        )
        top_state = "complete"
        top_expanded = False
    elif synth_started:
        top_label = t("agent.status.composing", s=elapsed_s)
        top_state = "running"
        top_expanded = True
    elif plan_evt and spec_started:
        top_label = t(
            "agent.status.specialists",
            done=len(spec_done), total=len(spec_started), s=elapsed_s,
        )
        top_state = "running"
        top_expanded = True
    elif plan_evt:
        top_label = t("agent.status.plan_ready", s=elapsed_s)
        top_state = "running"
        top_expanded = True
    else:
        top_label = t("agent.status.planning", s=elapsed_s)
        top_state = "running"
        top_expanded = True

    overall = st.status(top_label, state=top_state, expanded=top_expanded)

    # ── Plan ─────────────────────────────────────────────────────
    if plan_evt:
        with overall:
            section_label(t("agent.section.plan"))
            for step in plan_evt.get("plan", []):
                label = AGENT_LABEL.get(step["agent"], step["agent"])
                st.markdown(
                    f'{_step_pill(label, "active")}'
                    f'<span style="color:#1A1A1A">{step["sub_query"]}</span>',
                    unsafe_allow_html=True,
                )
            instr = plan_evt.get("synth_instruction", "")
            if instr:
                st.markdown(
                    f'<div style="color:{TEXT_MUTED};font-size:0.88rem;'
                    f'margin-top:0.6rem;line-height:1.55">'
                    f'<em>{t("agent.synth_instruction")}</em> {instr}</div>',
                    unsafe_allow_html=True,
                )

    # ── Specialists ──────────────────────────────────────────────
    if spec_started:
        section_label(t("agent.section.specialists"))
        for name, start_p in spec_started.items():
            label = AGENT_LABEL.get(name, name)
            done_p = spec_done.get(name)
            calls = spec_calls.get(name, [])

            if done_p:
                ok = done_p.get("ok", True)
                mark = "✓" if ok else "✗"
                hdr = t(
                    "agent.spec.done",
                    mark=mark, name=label,
                    n_calls=done_p.get("tool_calls", "?"),
                    iters=done_p.get("iterations", "?"),
                )
                state = "complete" if ok else "error"
                expanded = False
            else:
                hdr = t("agent.spec.running", name=label, n=len(calls))
                state = "running"
                expanded = True

            ss = st.status(hdr, state=state, expanded=expanded)
            with ss:
                st.caption(t(
                    "agent.spec.sub_query",
                    q=start_p.get("sub_query", "")[:120],
                ))
                if calls:
                    tail = [
                        f"→ `{c.get('tool')}`({_fmt_args(c.get('args', {}))}) "
                        f"· {c.get('ms', 0)}ms"
                        for c in calls[-5:]
                    ]
                    st.markdown("\n\n".join(tail))
                if done_p:
                    out = done_p.get("output", "")
                    try:
                        st.json(json.loads(out))
                    except Exception:
                        if out:
                            st.markdown(out)
                    if done_p.get("error"):
                        st.error(done_p["error"])

    # ── Synth / final answer ─────────────────────────────────────
    if synth_started or final_event:
        section_label(t("agent.section.final"))

    if final_event:
        final_text = final_event.get("final_answer", "") or "".join(synth_chunks)
        if final_text:
            ss = st.status(t("agent.synth.complete"),
                            state="complete", expanded=True)
            with ss:
                st.markdown(final_text)
        else:
            st.info(t("agent.synth.empty"))
    elif synth_started:
        ss = st.status(t("agent.synth.streaming"),
                        state="running", expanded=True)
        with ss:
            buf = "".join(synth_chunks)
            st.markdown(buf if buf else t("agent.synth.thinking"))

    if error_msg and not final_event:
        st.error(t("agent.error", msg=error_msg))


# ── Render current state ──────────────────────────────────────────


if store["query"]:
    elapsed_end = store["t_end"] if store["t_end"] else time.time()
    elapsed_s = max(0.0, elapsed_end - store["t_start"])
    _replay(store["events"], store["running"], elapsed_s)


# ── Poll while running ────────────────────────────────────────────


if store["running"]:
    time.sleep(0.4)
    st.rerun()
