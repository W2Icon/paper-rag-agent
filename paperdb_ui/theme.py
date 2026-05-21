"""
Shared visual theme for the paperdb Streamlit UI.

Streamlit's native theme config is too narrow (primary color, bg, font) for
the editorial / warm-cream / Claude-inspired look we want. Native config is
applied as the baseline, then CSS overrides do the heavy lifting:

  - Warm cream page bg (#FAF9F5), white surface cards
  - Anthropic-copper accent (#CC785C) for CTAs and links
  - Newsreader (serif) for display headings, Inter for body
  - 1px hairline borders, soft 8-12px radii, almost no shadow
  - Generous spacing, max-width reading column
  - Streamlit's own menu / "Made with Streamlit" footer hidden

Every page should call `apply_theme(...)` as its FIRST Streamlit call.
"""

from __future__ import annotations

import streamlit as st


# ── Design tokens ─────────────────────────────────────────────────

PRIMARY = "#CC785C"        # Anthropic copper — links, primary buttons
PRIMARY_HOVER = "#B86B50"
PRIMARY_SOFT = "#F5E6DE"   # tinted backgrounds (selected nav, badges)

BG = "#FAF9F5"             # warm cream page bg
SURFACE = "#FFFFFF"        # card / input bg
SIDEBAR_BG = "#F4F2EC"     # slightly darker cream

TEXT = "#1A1A1A"           # body text — deeper than slate-900 for warmth
TEXT_MUTED = "#6B6B6B"
TEXT_FAINT = "#9A9A9A"

BORDER = "#E5E0D8"         # warm hairline
BORDER_STRONG = "#D4CFC4"

SUCCESS = "#0E7C5A"        # forest green
WARNING = "#B45309"        # amber-700
ERROR = "#B91C1C"          # red-700


def apply_theme(page_title: str = "paperdb", layout: str = "wide",
                page_icon: str = "○") -> None:
    """Set page config + inject the global Claude-inspired CSS.

    Call this once at the top of every page (before any other Streamlit
    output). Idempotent — Streamlit deduplicates set_page_config.
    """
    st.set_page_config(page_title=page_title, page_icon=page_icon, layout=layout)
    st.markdown(_CSS, unsafe_allow_html=True)
    # Top-right EN / 中文 toggle, rendered above the hero on every page.
    from paperdb_ui.i18n import language_toggle
    language_toggle()


# ── CSS ───────────────────────────────────────────────────────────


_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=JetBrains+Mono:wght@400;500&display=swap');

/* Force light mode regardless of OS dark-mode preference — without this
 * the browser briefly paints body with its dark-mode default between
 * Streamlit rerun cycles, causing a black flash on every button click. */
:root, html, body {
    color-scheme: light !important;
    background-color: #FAF9F5 !important;
}

:root {
    --pdb-primary: #CC785C;
    --pdb-primary-hover: #B86B50;
    --pdb-primary-soft: #F5E6DE;
    --pdb-bg: #FAF9F5;
    --pdb-surface: #FFFFFF;
    --pdb-sidebar: #F4F2EC;
    --pdb-text: #1A1A1A;
    --pdb-text-muted: #6B6B6B;
    --pdb-text-faint: #9A9A9A;
    --pdb-border: #E5E0D8;
    --pdb-border-strong: #D4CFC4;
    --pdb-success: #0E7C5A;
    --pdb-warning: #B45309;
    --pdb-error: #B91C1C;
}

/* ── Page chrome ─────────────────────────────────────────────── */

html, body, .stApp, [data-testid="stAppViewContainer"], .main {
    background: var(--pdb-bg) !important;
    color: var(--pdb-text);
    /* CJK fallbacks (PingFang for macOS, Microsoft YaHei for Windows,
     * Noto Sans CJK on Linux) so Chinese paper titles render correctly. */
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, system-ui,
                 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei',
                 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', sans-serif;
    font-feature-settings: "cv02", "cv03", "cv04", "cv11";
}

/* Kill Streamlit's status/spinner overlay flash during rerun */
[data-testid="stStatusWidget"] { background: transparent !important; }
.stApp > header { background: transparent !important; }

/* ── Rerun-flash mitigation ───────────────────────────────────
 *
 * Streamlit reruns the whole script on every interaction. When a slow
 * API call is in flight, React briefly unmounts the content area before
 * committing the new DOM. During that gap, inner containers can flash
 * a dark/transparent state even though the body bg is set.
 *
 * Two-part fix:
 *  1. `contain: paint` on the outer container — tells the browser to
 *     isolate paints from outside, preventing render thrash.
 *  2. A pseudo-element backstop on `.stApp` paints the cream bg as a
 *     fixed layer behind ALL content. Even if every other element is
 *     briefly removed, the backstop remains visible.
 */
[data-testid="stAppViewContainer"] {
    contain: paint;
    background: var(--pdb-bg) !important;
    min-height: 100vh;
}

.stApp::before {
    content: '';
    position: fixed;
    inset: 0;
    background: var(--pdb-bg);
    z-index: -1;
}

/* Subtler opacity transition (the bg is now rock-solid behind, so we
 * only need a tiny fade to soften React's DOM swap) */
[data-testid="stAppViewContainer"] > .main {
    animation: pdb-fade-in 80ms ease-out;
}
@keyframes pdb-fade-in {
    from { opacity: 0.96; }
    to   { opacity: 1; }
}

/* Hide Streamlit's own chrome — clutter we don't need */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"] { visibility: hidden; }

/* Main content padding — wider top margin and generous side space */
.main .block-container {
    padding-top: 3rem;
    padding-bottom: 5rem;
    max-width: 1100px;
}

/* ── Typography ──────────────────────────────────────────────── */

h1, h2, h3, h4 {
    /* Serif headings with CJK fallbacks — Songti/Source Han Serif on
     * macOS/Linux, Microsoft YaHei (default sans on Win since it's the
     * cleanest CJK on that platform) */
    font-family: 'Newsreader', Georgia, 'Times New Roman',
                 'Songti SC', 'Source Han Serif SC',
                 'Microsoft YaHei', serif;
    color: var(--pdb-text);
    font-weight: 500;
    letter-spacing: -0.015em;
    line-height: 1.2;
}

h1 { font-size: 2.4rem; margin-top: 0; margin-bottom: 1rem; font-weight: 500; }
h2 { font-size: 1.7rem; margin-top: 2.2rem; margin-bottom: 0.8rem; }
h3 { font-size: 1.25rem; margin-top: 1.6rem; margin-bottom: 0.6rem; font-weight: 600; }
h4 { font-size: 1.05rem; margin-top: 1.2rem; margin-bottom: 0.5rem; font-weight: 600; }

p, .stMarkdown p, .stMarkdown li {
    color: var(--pdb-text);
    line-height: 1.65;
    font-size: 0.95rem;
}

a, .stMarkdown a {
    color: var(--pdb-primary);
    text-decoration: none;
    border-bottom: 1px solid transparent;
    transition: border-color 150ms ease;
}
a:hover { border-bottom-color: var(--pdb-primary); }

code, .stMarkdown code, pre {
    font-family: 'JetBrains Mono', SF Mono, Menlo, monospace;
    font-size: 0.85em;
}
.stMarkdown code:not(pre code) {
    background: var(--pdb-primary-soft);
    color: #8B4A2E;
    padding: 0.1em 0.4em;
    border-radius: 4px;
}

hr, [data-testid="stMarkdownContainer"] hr {
    border: none;
    border-top: 1px solid var(--pdb-border);
    margin: 2rem 0;
}

/* The default Streamlit st.caption — make it look like editorial dek */
[data-testid="stCaptionContainer"], .stCaption, small {
    color: var(--pdb-text-muted) !important;
    font-size: 0.875rem !important;
    letter-spacing: 0.01em;
}

/* ── Sidebar ─────────────────────────────────────────────────── */

[data-testid="stSidebar"] {
    background: var(--pdb-sidebar) !important;
    border-right: 1px solid var(--pdb-border);
}
[data-testid="stSidebar"] .block-container { padding-top: 2.5rem; }

[data-testid="stSidebarNav"] {
    background: transparent !important;
}
[data-testid="stSidebarNav"] ul {
    padding: 0 0.5rem;
}
/* High-specificity selectors — Streamlit applies multiple inline styles to
 * the nav links; we need to override every inner text node, not just <a>. */
[data-testid="stSidebarNav"] li a,
[data-testid="stSidebarNav"] li a span,
[data-testid="stSidebarNav"] li a p {
    color: #1A1A1A !important;          /* solid near-black */
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    letter-spacing: 0.005em;
}
[data-testid="stSidebarNav"] li a {
    border-radius: 6px;
    padding: 0.5rem 0.75rem !important;
    transition: background 150ms ease;
    border-bottom: none !important;
}
[data-testid="stSidebarNav"] li a:hover,
[data-testid="stSidebarNav"] li a:hover span,
[data-testid="stSidebarNav"] li a:hover p {
    background: var(--pdb-primary-soft) !important;
    color: #5C2E18 !important;          /* darker copper on hover */
    border-bottom-color: transparent;
}
[data-testid="stSidebarNav"] li a[aria-current="page"],
[data-testid="stSidebarNav"] li a[aria-current="page"] span,
[data-testid="stSidebarNav"] li a[aria-current="page"] p {
    background: var(--pdb-primary-soft) !important;
    color: #5C2E18 !important;
    font-weight: 700 !important;
}

/* Sidebar body text (subheaders, labels etc.) also needs to read as solid */
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
    color: #1A1A1A !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: #1A1A1A !important;
}

/* ── Buttons ─────────────────────────────────────────────────── */

.stButton > button, .stDownloadButton > button {
    border-radius: 8px;
    border: 1px solid var(--pdb-border-strong);
    background: var(--pdb-surface);
    color: var(--pdb-text);
    font-weight: 500;
    font-size: 0.92rem;
    padding: 0.55rem 1.1rem;
    transition: all 150ms ease;
    box-shadow: none;
    cursor: pointer;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    border-color: var(--pdb-primary);
    color: var(--pdb-primary);
    background: var(--pdb-surface);
}
.stButton > button:focus { box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.18); }

/* Primary button (kind="primary") — copper fill */
.stButton > button[kind="primary"] {
    background: var(--pdb-primary);
    border-color: var(--pdb-primary);
    color: white;
}
.stButton > button[kind="primary"]:hover {
    background: var(--pdb-primary-hover);
    border-color: var(--pdb-primary-hover);
    color: white;
}

/* ── Inputs ──────────────────────────────────────────────────── */

.stTextInput input, .stTextArea textarea, .stNumberInput input,
.stSelectbox > div > div, .stMultiSelect > div > div {
    background: var(--pdb-surface) !important;
    border: 1px solid var(--pdb-border-strong) !important;
    border-radius: 8px !important;
    color: var(--pdb-text) !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.95rem !important;
    transition: border-color 150ms ease, box-shadow 150ms ease;
}
.stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
    border-color: var(--pdb-primary) !important;
    box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.15) !important;
    outline: none !important;
}

/* Labels above inputs */
.stTextInput label, .stTextArea label, .stNumberInput label,
.stSelectbox label, .stMultiSelect label, .stRadio label,
.stCheckbox label, .stFileUploader label {
    color: var(--pdb-text-muted) !important;
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.01em;
}

/* ── Metrics (st.metric) ─────────────────────────────────────── */

[data-testid="stMetric"] {
    background: var(--pdb-surface);
    border: 1px solid var(--pdb-border);
    border-radius: 12px;
    padding: 1.1rem 1.2rem;
    transition: border-color 200ms ease;
}
[data-testid="stMetric"]:hover { border-color: var(--pdb-border-strong); }
[data-testid="stMetricLabel"] {
    color: var(--pdb-text-muted) !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 500;
}
[data-testid="stMetricValue"] {
    color: var(--pdb-text) !important;
    font-family: 'Newsreader', Georgia, 'Songti SC',
                 'Source Han Serif SC', 'Microsoft YaHei', serif !important;
    font-size: 2rem !important;
    font-weight: 500;
    line-height: 1.1;
}
[data-testid="stMetricDelta"] { color: var(--pdb-text-faint) !important; }

/* ── Alerts ──────────────────────────────────────────────────── */

[data-testid="stAlert"] {
    border-radius: 10px;
    border: 1px solid var(--pdb-border);
    background: var(--pdb-surface);
    padding: 0.9rem 1rem;
    box-shadow: none;
}
/* Success */
[data-testid="stAlert"][data-baseweb="notification"][kind="success"],
[data-baseweb="notification"][role="alert"]:has([data-testid="stMarkdownContainer"]) {
    /* Stylings are mostly inherited via stAlert above */
}

/* ── Tabs ────────────────────────────────────────────────────── */

[data-baseweb="tab-list"] {
    border-bottom: 1px solid var(--pdb-border);
    gap: 0;
}
[data-baseweb="tab"] {
    color: var(--pdb-text-muted) !important;
    font-weight: 500;
    padding: 0.7rem 1.1rem !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
    transition: color 150ms ease, border-color 150ms ease;
}
[data-baseweb="tab"]:hover { color: var(--pdb-text) !important; }
[data-baseweb="tab"][aria-selected="true"] {
    color: var(--pdb-primary) !important;
    border-bottom-color: var(--pdb-primary) !important;
}

/* ── Expander ────────────────────────────────────────────────── */

.streamlit-expanderHeader, [data-testid="stExpander"] summary {
    background: var(--pdb-surface) !important;
    border: 1px solid var(--pdb-border) !important;
    border-radius: 10px !important;
    color: var(--pdb-text) !important;
    font-weight: 500;
    padding: 0.7rem 1rem !important;
    transition: border-color 150ms ease;
}
[data-testid="stExpander"] summary:hover { border-color: var(--pdb-border-strong) !important; }
[data-testid="stExpander"] > details[open] > summary {
    border-radius: 10px 10px 0 0 !important;
    border-bottom-color: transparent !important;
}
[data-testid="stExpanderDetails"] {
    background: var(--pdb-surface);
    border: 1px solid var(--pdb-border);
    border-top: none;
    border-radius: 0 0 10px 10px;
    padding: 1rem 1.1rem;
}

/* ── Tables (st.dataframe, st.table) ────────────────────────── */

.stDataFrame, [data-testid="stTable"] {
    border: 1px solid var(--pdb-border);
    border-radius: 10px;
    overflow: hidden;
    background: var(--pdb-surface);
}
.stDataFrame [data-testid="stDataFrameResizable"] { background: var(--pdb-surface); }

/* ── Code blocks ─────────────────────────────────────────────── */

[data-testid="stCodeBlock"] {
    background: #FAF7F2 !important;
    border: 1px solid var(--pdb-border) !important;
    border-radius: 10px !important;
}
[data-testid="stCodeBlock"] pre { background: transparent !important; }

/* ── Chat (Agent Chat page) ──────────────────────────────────── */

[data-testid="stChatMessage"] {
    background: transparent;
    border: none;
    padding: 0.5rem 0;
}
[data-testid="stChatMessage"][data-testid*="user"] {
    background: var(--pdb-surface);
    border: 1px solid var(--pdb-border);
    border-radius: 12px;
    padding: 1rem 1.2rem;
    margin: 0.6rem 0;
}

.stChatInputContainer, [data-testid="stChatInput"] {
    background: var(--pdb-surface) !important;
    border: 1px solid var(--pdb-border-strong) !important;
    border-radius: 12px !important;
}
.stChatInputContainer:focus-within {
    border-color: var(--pdb-primary) !important;
    box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.15) !important;
}

/* ── Dividers ─────────────────────────────────────────────────── */

[data-testid="stHorizontalBlock"] + [data-testid="stHorizontalBlock"] {
    margin-top: 1rem;
}

/* ── Misc cleanups ───────────────────────────────────────────── */

/* Better radio + checkbox alignment */
.stRadio > div { gap: 0.35rem; }
.stRadio label, .stCheckbox label { color: var(--pdb-text) !important; font-weight: 400 !important; }

/* File uploader */
[data-testid="stFileUploader"] section {
    background: var(--pdb-surface);
    border: 1px dashed var(--pdb-border-strong);
    border-radius: 10px;
    transition: border-color 150ms ease, background 150ms ease;
}
[data-testid="stFileUploader"] section:hover {
    border-color: var(--pdb-primary);
    background: #FDFCF8;
}

/* Spinners — use copper */
[data-testid="stSpinner"] > div > div { border-top-color: var(--pdb-primary) !important; }

/* Selected sidebar row in nav */
[data-testid="stSidebarNav"] li a[aria-current="page"] {
    background: var(--pdb-primary-soft) !important;
}

/* Top-of-page-title underline hint (Claude.ai style) */
h1::after {
    content: '';
    display: block;
    width: 32px;
    height: 2px;
    background: var(--pdb-primary);
    margin-top: 0.55rem;
    border-radius: 2px;
}

/* Smooth motion for prefers-reduced-motion users */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition: none !important; animation: none !important; }
}
</style>
"""


# ── Reusable layout helpers ───────────────────────────────────────


def hero(title: str, dek: str = "", *, eyebrow: str = "") -> None:
    """Editorial-style page header. Eyebrow is a small uppercase tag above
    the title (e.g. 'Library' / 'Search'). Dek is the subtitle paragraph."""
    eyebrow_html = (
        f'<div style="color:{TEXT_FAINT};font-size:0.78rem;'
        f'text-transform:uppercase;letter-spacing:0.12em;'
        f'font-weight:500;margin-bottom:0.4rem">{eyebrow}</div>'
        if eyebrow else ""
    )
    dek_html = (
        f'<p style="color:{TEXT_MUTED};font-size:1.05rem;'
        f'line-height:1.55;max-width:680px;margin-top:0.6rem;'
        f'margin-bottom:1.6rem">{dek}</p>'
        if dek else ""
    )
    st.markdown(
        f'{eyebrow_html}'
        f'<h1 style="margin-top:0">{title}</h1>'
        f'{dek_html}',
        unsafe_allow_html=True,
    )


def section_label(text: str) -> None:
    """Small uppercase section label — for organizing dense pages."""
    st.markdown(
        f'<div style="color:{TEXT_MUTED};font-size:0.78rem;'
        f'text-transform:uppercase;letter-spacing:0.1em;'
        f'font-weight:600;margin:2rem 0 0.6rem">{text}</div>',
        unsafe_allow_html=True,
    )


def card(body_md: str, *, padding: str = "1.2rem 1.4rem") -> None:
    """Light card surface wrapping a chunk of markdown."""
    st.markdown(
        f'<div style="background:{SURFACE};border:1px solid {BORDER};'
        f'border-radius:12px;padding:{padding};margin-bottom:0.8rem">'
        f'{body_md}</div>',
        unsafe_allow_html=True,
    )
