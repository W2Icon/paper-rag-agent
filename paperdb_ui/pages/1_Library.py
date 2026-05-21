"""Library — paginated list of all papers with filters."""

import streamlit as st

from paperdb_ui.api_client import list_papers
from paperdb_ui.i18n import t
from paperdb_ui.theme import (
    BORDER, PRIMARY, PRIMARY_SOFT, SURFACE, TEXT, TEXT_MUTED, TEXT_FAINT,
    apply_theme, hero,
)


apply_theme(page_title="paperdb · Library", layout="wide")
hero(
    title=t("library.title"),
    eyebrow=t("library.eyebrow"),
    dek=t("library.dek"),
)


# ── Sidebar filters ──────────────────────────────────────────────


with st.sidebar:
    st.subheader(t("library.filters"))
    limit = st.slider(t("library.limit"), 10, 200, 50, 10)
    tag = st.text_input(t("library.tag_filter"), value="")
    only_analyzed = st.checkbox(t("library.only_analyzed"), value=False)
    only_embedded = st.checkbox(t("library.only_embedded"), value=False)


# ── Fetch ────────────────────────────────────────────────────────


try:
    papers = list_papers(limit=limit, tag=tag or None)
except Exception as e:
    st.error(t("library.load_failed", err=str(e)))
    st.stop()

if only_analyzed:
    papers = [p for p in papers if p.get("has_llm_analysis")]
if only_embedded:
    papers = [p for p in papers if p.get("has_embedding")]

count_str = (
    t("library.count.one") if len(papers) == 1
    else t("library.count.many", n=len(papers))
)
st.markdown(
    f'<div style="color:{TEXT_MUTED};font-size:0.86rem;'
    f'margin-bottom:0.4rem">{count_str}</div>',
    unsafe_allow_html=True,
)


# ── Page-scoped CSS: editorial list with hairline dividers ───────


st.markdown(
    """
    <style>
    /* Scope all styles to the .library-list-scope marker via sibling combinator */
    .library-list-scope ~ [data-testid="stHorizontalBlock"] {
        border-top: 1px solid #E5E0D8;
        padding: 1.15rem 0.4rem;
        margin: 0 !important;
        transition: background 160ms ease;
        align-items: center;
    }
    /* The last row gets a closing rule too */
    .library-list-end {
        border-top: 1px solid #E5E0D8;
        height: 0;
        margin-top: 0;
    }
    /* Hover: subtle copper-tint background, no movement */
    .library-list-scope ~ [data-testid="stHorizontalBlock"]:hover {
        background: rgba(204, 120, 92, 0.045);
    }
    /* When you focus a row's button, highlight the whole row */
    .library-list-scope ~ [data-testid="stHorizontalBlock"]:focus-within {
        background: rgba(204, 120, 92, 0.06);
    }

    /* Serif index badge — left-aligned column 1 */
    .pdb-idx {
        font-family: 'Newsreader', Georgia, 'Songti SC', serif;
        font-size: 1.05rem;
        font-weight: 500;
        color: #B86B50;
        letter-spacing: 0.05em;
        font-variant-numeric: tabular-nums;
    }

    /* Title row */
    .pdb-row-title {
        font-size: 1rem;
        font-weight: 600;
        color: #1A1A1A;
        line-height: 1.45;
        margin-bottom: 0.25rem;
    }
    .pdb-row-authors {
        font-size: 0.85rem;
        color: #6B6B6B;
        line-height: 1.4;
        margin-bottom: 0.2rem;
    }
    .pdb-row-meta {
        font-size: 0.78rem;
        color: #9A9A9A;
        line-height: 1.4;
    }

    /* Status dot pills */
    .pdb-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.32rem;
        font-size: 0.75rem;
        font-weight: 500;
        color: #6B6B6B;
        margin-left: 0.7rem;
    }
    .pdb-pill::before {
        content: '';
        display: inline-block;
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #C8C5BD;
    }
    .pdb-pill.on { color: #0E7C5A; }
    .pdb-pill.on::before { background: #0E7C5A; }

    /* DOI: monospace small */
    .pdb-doi {
        font-family: 'JetBrains Mono', SF Mono, monospace;
        font-size: 0.73rem;
        color: #9A9A9A;
        letter-spacing: 0.005em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Render list ──────────────────────────────────────────────────


if not papers:
    st.info(t("library.empty"))
    st.stop()


# Marker div: CSS sibling combinator hangs the row styling off this anchor.
st.markdown('<div class="library-list-scope"></div>', unsafe_allow_html=True)


def _meta_html(p: dict) -> str:
    """Build the metadata HTML for one paper row."""
    authors_html = ""
    if p.get("authors"):
        authors = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors += " et al."
        authors_html = f'<div class="pdb-row-authors">{authors}</div>'

    meta_bits = []
    if p.get("doi"):
        meta_bits.append(f'<span class="pdb-doi">{p["doi"]}</span>')
    analyzed_cls = "pdb-pill on" if p.get("has_llm_analysis") else "pdb-pill"
    analyzed_label = (
        t("library.pill.analyzed") if p.get("has_llm_analysis")
        else t("library.pill.not_analyzed")
    )
    meta_bits.append(f'<span class="{analyzed_cls}">{analyzed_label}</span>')
    embedded_cls = "pdb-pill on" if p.get("has_embedding") else "pdb-pill"
    embedded_label = (
        t("library.pill.embedded") if p.get("has_embedding")
        else t("library.pill.no_embed")
    )
    meta_bits.append(f'<span class="{embedded_cls}">{embedded_label}</span>')
    meta_html = f'<div class="pdb-row-meta">{"".join(meta_bits)}</div>'

    return (
        f'<div class="pdb-row-title">{p.get("title") or "(untitled)"}</div>'
        f'{authors_html}'
        f'{meta_html}'
    )


for p in papers:
    cols = st.columns([1, 14, 3], gap="small")
    with cols[0]:
        st.markdown(
            f'<div class="pdb-idx">{p["id"]:03d}</div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(_meta_html(p), unsafe_allow_html=True)
    with cols[2]:
        if st.button(t("library.open"), key=f"sel_{p['id']}",
                      use_container_width=True):
            st.session_state["selected_paper_id"] = p["id"]
            st.switch_page("pages/2_Paper_Detail.py")


# Closing rule for the last row
st.markdown('<div class="library-list-end"></div>', unsafe_allow_html=True)
