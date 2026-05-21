"""Search — three-tier RAG with mode/rerank toggles + filters."""

import time

import streamlit as st

from paperdb_ui.api_client import search
from paperdb_ui.i18n import t
from paperdb_ui.theme import apply_theme, hero


apply_theme(page_title="paperdb · Search", layout="wide")
hero(
    title=t("search.title"),
    eyebrow=t("search.eyebrow"),
    dek=t("search.dek"),
)

with st.sidebar:
    st.subheader(t("search.options"))
    mode = st.radio(t("search.mode"), ["hybrid", "vector", "fts"], index=0)
    rerank = st.checkbox(t("search.rerank_toggle"), value=False)
    top_k = st.slider(t("search.top_k"), 1, 30, 10)
    st.subheader(t("search.filters"))
    year_from = st.number_input(t("search.year_from"), min_value=0, value=0)
    year_to = st.number_input(t("search.year_to"), min_value=0, value=0)
    tag = st.text_input(t("search.tag"), value="")

query = st.text_input(
    t("search.query"),
    placeholder=t("search.query.placeholder"),
    key="search_query",
)


def _planned_stages(mode: str, rerank: bool) -> list[tuple[str, str]]:
    """Return [(key, label)] of stages that will run for this configuration."""
    stages: list[tuple[str, str]] = []
    if mode in ("hybrid", "vector"):
        stages.append(("embed", t("search.stage.embed")))
    paths = []
    if mode in ("hybrid", "vector"):
        paths.append("vector cosine")
    if mode in ("hybrid", "fts"):
        paths.append("FTS5 BM25")
    stages.append(("retrieval", t("search.stage.retrieve", paths=" + ".join(paths))))
    if mode == "hybrid":
        stages.append(("fuse", t("search.stage.fuse")))
    if rerank:
        stages.append(("rerank", t("search.stage.rerank")))
    return stages


if st.button(t("search.button"), type="primary", use_container_width=True) and query.strip():
    filters: dict = {}
    if year_from > 0:
        filters["year_from"] = int(year_from)
    if year_to > 0:
        filters["year_to"] = int(year_to)
    if tag:
        filters["tag"] = tag

    stages = _planned_stages(mode, rerank)

    with st.status(t("search.running"), expanded=True) as status:
        for _, label in stages:
            st.write(f"• {label}")
        t_wall = time.time()
        try:
            resp = search(query, mode=mode, top_k=top_k, rerank=rerank, **filters)
        except Exception as e:
            status.update(
                label=t("search.failed", err=str(e)),
                state="error", expanded=True,
            )
            st.stop()
        wall_ms = int((time.time() - t_wall) * 1000)

        timings = resp.get("timings_ms", {}) or {}
        hit_counts = resp.get("hit_counts", {}) or {}
        hits = resp.get("hits", [])

        parts = []
        if "embed_ms" in timings:
            parts.append(f"embed {timings['embed_ms']}ms")
        if "retrieval_ms" in timings:
            paths_str = ", ".join(
                f"{src}:{n}" for src, n in sorted(hit_counts.items())
            ) or "no paths"
            parts.append(f"retrieve {timings['retrieval_ms']}ms ({paths_str})")
        if "rerank_ms" in timings:
            parts.append(f"rerank {timings['rerank_ms']}ms")
        total_ms = timings.get("total_ms", wall_ms)
        detail = " · ".join(parts) if parts else f"{wall_ms}ms"

        status.update(
            label=t("search.done.summary", n=len(hits), ms=total_ms, detail=detail),
            state="complete", expanded=False,
        )

    st.caption(t(
        "search.found", n=len(hits), mode=resp["mode"], rerank=resp["rerank"]
    ))

    if not hits:
        st.info(t("search.no_results"))

    for i, h in enumerate(hits, 1):
        with st.container(border=True):
            cols = st.columns([10, 1])
            with cols[0]:
                st.markdown(f"**{i}. [{h['paper_id']}]** {h['title']}")
                if h.get("rerank_reason"):
                    st.markdown(f"> {h['rerank_reason']}")
                if h.get("abstract_snippet"):
                    st.caption(h["abstract_snippet"])
                src = h.get("source_scores", {})
                ranks = h.get("rank_in_source", {})
                src_str = ", ".join(
                    f"{k}#{ranks.get(k,'?')}({v:.3f})" for k, v in src.items()
                )
                st.caption(
                    f"score={h['score']:.4f}  ·  sources: {src_str or '—'}"
                )
            with cols[1]:
                if st.button(t("search.open"), key=f"open_{h['paper_id']}",
                              use_container_width=True):
                    st.session_state["selected_paper_id"] = h["paper_id"]
                    st.switch_page("pages/2_Paper_Detail.py")
