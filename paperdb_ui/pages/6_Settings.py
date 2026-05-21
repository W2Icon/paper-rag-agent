"""Settings — show config, env, and runtime info. Read-only for now."""

import os

import streamlit as st

from paperdb_ui.api_client import base_url, client
from paperdb_ui.i18n import t
from paperdb_ui.theme import apply_theme, hero, section_label


apply_theme(page_title="paperdb · Settings", layout="wide")
hero(
    title=t("settings.title"),
    eyebrow=t("settings.eyebrow"),
    dek=t("settings.dek"),
)

section_label(t("settings.frontend"))
st.code(f"PAPERDB_API_URL = {base_url()}")
st.caption(t("settings.frontend_hint"))

section_label(t("settings.backend"))
try:
    r = client().get("/")
    r.raise_for_status()
    info = r.json()
    st.json(info)
except Exception as e:
    st.error(t("settings.backend_failed", err=str(e)))
    st.stop()

section_label(t("settings.llm_env"))
sensitive = {"LLM_API_KEY", "LLM_EMBEDDING_API_KEY"}
env_keys = [
    "LLM_PROVIDER", "LLM_BASE_URL",
    "LLM_API_KEY", "LLM_EMBEDDING_API_KEY",
    "LLM_COMPLEX_MODEL", "LLM_SIMPLE_MODEL",
    "LLM_COMPLEX_THINKING", "LLM_SIMPLE_THINKING",
    "LLM_COMPLEX_REASONING_EFFORT",
    "LLM_EMBEDDING_MODEL", "LLM_EMBEDDING_DIMENSIONS",
    "LLM_TIER_ANALYZE_PAPER", "LLM_TIER_SCORE_REFS",
    "LLM_TIER_LIT_REVIEW", "LLM_TIER_RERANK",
]
env_data = {}
for k in env_keys:
    v = os.environ.get(k, "")
    if k in sensitive and v:
        v = v[:6] + "…" + v[-4:] if len(v) > 12 else t("settings.llm_env.set")
    env_data[k] = v or t("settings.llm_env.unset")
st.table([
    {t("settings.llm_env.col_var"): k, t("settings.llm_env.col_val"): v}
    for k, v in env_data.items()
])

st.caption(t("settings.llm_env.caption"))
