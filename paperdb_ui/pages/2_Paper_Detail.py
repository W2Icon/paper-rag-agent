"""Paper Detail — metadata, LLM analysis, sections, references."""

import streamlit as st

from paperdb_ui.api_client import get_paper, get_references, get_sections
from paperdb_ui.i18n import t
from paperdb_ui.theme import apply_theme, hero, section_label


apply_theme(page_title="paperdb · Paper", layout="wide")
hero(
    title=t("paper.title"),
    eyebrow=t("paper.eyebrow"),
    dek=t("paper.dek"),
)

paper_id = st.session_state.get("selected_paper_id", None)
paper_id = st.number_input(t("paper.paper_id"), min_value=1, value=paper_id or 1, step=1)

try:
    p = get_paper(int(paper_id))
except Exception as e:
    st.error(t("paper.load_failed", id=paper_id, err=str(e)))
    st.stop()

st.session_state["selected_paper_id"] = int(paper_id)

# ── Header ────────────────────────────────────────────────────────

st.header(p["title"])
meta_bits = []
if p.get("authors"):
    meta_bits.append(f"**{t('paper.authors')}:** " + ", ".join(p["authors"]))
if p.get("doi"):
    meta_bits.append(f"**{t('paper.doi')}:** {p['doi']}")
meta_bits.append(f"**{t('paper.pages')}:** {p.get('total_pages', 0)}")
if p.get("ingested_at"):
    meta_bits.append(f"**{t('paper.ingested')}:** {p['ingested_at']}")
st.markdown("  ·  ".join(meta_bits))

if p.get("tags"):
    st.caption(f"{t('paper.tags')}: " + ", ".join(p["tags"]))

# ── Abstract ──────────────────────────────────────────────────────

if p.get("abstract"):
    with st.expander(t("paper.abstract"), expanded=True):
        st.write(p["abstract"])

# ── LLM analysis ─────────────────────────────────────────────────

section_label(t("paper.llm_analysis"))
if not p.get("llm_analyzed_at"):
    st.info(t("paper.not_analyzed"))
else:
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**{t('paper.research_field')}:** {p.get('llm_research_field') or '—'}")
        st.markdown(f"**{t('paper.summary')}**")
        st.write(p.get("llm_summary") or "—")
    with cols[1]:
        st.markdown(f"**{t('paper.methodology')}**")
        st.write(p.get("llm_methodology") or "—")
        findings = p.get("llm_key_findings") or []
        if findings:
            st.markdown(f"**{t('paper.key_findings')}**")
            for f in findings:
                st.markdown(f"- {f}")

# ── Sections ─────────────────────────────────────────────────────

with st.expander(t("paper.sections"), expanded=False):
    try:
        secs = get_sections(int(paper_id))
    except Exception as e:
        st.error(t("paper.sections_load_failed", err=str(e)))
        secs = []
    if not secs:
        st.caption(t("paper.no_sections"))
    for s in secs:
        st.markdown(
            f"**{'  ' * (s['level']-1)}[L{s['level']}] {s['heading']}** "
            f"<span style='color:#999'>(pp.{s['page_start']}-{s['page_end']})</span>",
            unsafe_allow_html=True,
        )
        for para in s.get("paragraphs", [])[:3]:
            st.caption(para[:400] + ("…" if len(para) > 400 else ""))

# ── References ───────────────────────────────────────────────────

with st.expander(t("paper.references"), expanded=False):
    try:
        refs = get_references(int(paper_id))
    except Exception as e:
        st.error(t("paper.references_load_failed", err=str(e)))
        refs = []
    if not refs:
        st.caption(t("paper.no_references"))
    for r in refs:
        score = r.get("llm_relevance_score")
        rel = r.get("llm_relationship") or ""
        rs = f"  ·  {t('paper.relevance')}={score:.2f} ({rel})" if score is not None else ""
        st.markdown(
            f"**[{r.get('ref_number') or '?'}]** {r['title'][:120]}"
            + (f"  ·  {r['year']}" if r.get("year") else "")
            + (f"  ·  {t('paper.doi')}: {r['doi']}" if r.get("doi") else "")
            + rs
        )
