"""Streamlit landing page — editorial overview + API health + entry points."""

from __future__ import annotations

import streamlit as st

from paperdb_ui.api_client import health, stats
from paperdb_ui.i18n import t
from paperdb_ui.theme import (
    BORDER, PRIMARY, PRIMARY_SOFT, SURFACE, TEXT, TEXT_MUTED,
    apply_theme, hero, section_label,
)


apply_theme(page_title="paperdb", layout="wide")


# ── Page header ──────────────────────────────────────────────────


hero(
    title="paperdb",
    eyebrow=t("landing.eyebrow"),
    dek=t("landing.dek"),
)


# ── API health ───────────────────────────────────────────────────


ok, msg, kind = health()
if not ok:
    # Different remediation depending on why the probe failed. "Start the
    # API" advice is wrong (and confusing) for `timeout` — the API IS up,
    # just busy. Offering a retry button beats stop() in that case so users
    # don't think they need to relaunch anything.
    if kind == "timeout":
        title = t("landing.backend_busy_title")
        hint = t("landing.backend_busy_hint")
    else:
        title = t("landing.backend_unreachable_title")
        hint = t("landing.backend_unreachable_hint")
    st.markdown(
        f'<div style="border:1px solid #F4C8C8;background:#FEF7F7;'
        f'border-radius:10px;padding:1rem 1.2rem;color:#9B2F2F;'
        f'margin:1rem 0">'
        f'<strong>{title}</strong> {msg}<br>'
        f'<span style="color:#6B6B6B;font-size:0.9rem">'
        f'{hint}</span></div>',
        unsafe_allow_html=True,
    )
    if kind == "timeout":
        if st.button(t("landing.retry")):
            st.rerun()
    st.stop()


# ── Stats overview ───────────────────────────────────────────────


try:
    s = stats()
except Exception as e:
    st.error(t("landing.stats_failed", err=str(e)))
    st.stop()

section_label(t("landing.library_overview"))

emb = s.get("embeddings", {})

cols = st.columns(5)
cols[0].metric(t("landing.metric.papers"), s["papers"])
cols[1].metric(t("landing.metric.sections"), s["sections"])
cols[2].metric(t("landing.metric.references"), s["references"])
cols[3].metric(t("landing.metric.cross_paper_edges"), s.get("shared_citation_pairs", 0))
cols[4].metric(
    t("landing.metric.viewpoints"),
    emb.get("viewpoints_total", 0),
    delta=t("landing.metric.embedded_suffix", n=emb.get("viewpoints_with_emb", 0))
    if emb.get("viewpoints_with_emb")
    else None,
)


# ── Entry points ─────────────────────────────────────────────────


section_label(t("landing.where_to_go"))


PAGES = [
    ("landing.page.library",       "landing.page.library.desc"),
    ("landing.page.paper_detail",  "landing.page.paper_detail.desc"),
    ("landing.page.search",        "landing.page.search.desc"),
    ("landing.page.agent_chat",    "landing.page.agent_chat.desc"),
    ("landing.page.ingest",        "landing.page.ingest.desc"),
    ("landing.page.settings",      "landing.page.settings.desc"),
]

# 2-column card grid
for left, right in zip(PAGES[::2], PAGES[1::2]):
    c1, c2 = st.columns(2, gap="medium")
    for col, (name_key, desc_key) in ((c1, left), (c2, right)):
        col.markdown(
            f'<div style="background:{SURFACE};border:1px solid {BORDER};'
            f'border-radius:12px;padding:1.1rem 1.3rem;height:100%;'
            f'transition:border-color 150ms ease">'
            f'<div style="font-family:Newsreader,Georgia,\'Songti SC\','
            f'\'Source Han Serif SC\',\'Microsoft YaHei\',serif;'
            f'font-size:1.1rem;font-weight:600;color:{TEXT};'
            f'margin-bottom:0.35rem">{t(name_key)}</div>'
            f'<div style="color:{TEXT_MUTED};font-size:0.92rem;'
            f'line-height:1.55">{t(desc_key)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Architecture note ────────────────────────────────────────────


section_label(t("landing.architecture"))

st.markdown(
    f'<div style="color:{TEXT_MUTED};font-size:0.92rem;line-height:1.65;'
    f'max-width:700px">'
    f'{t("landing.architecture.body")}</div>',
    unsafe_allow_html=True,
)
