"""Ingest — drag-drop PDF upload + analyze + embed."""

import streamlit as st

from paperdb_ui.api_client import client, ingest_pdf
from paperdb_ui.i18n import t
from paperdb_ui.theme import apply_theme, hero


apply_theme(page_title="paperdb · Ingest", layout="wide")
hero(
    title=t("ingest.title"),
    eyebrow=t("ingest.eyebrow"),
    dek=t("ingest.dek"),
)

uploaded = st.file_uploader(
    t("ingest.dropzone"),
    type=["pdf"],
    accept_multiple_files=True,
)

with st.sidebar:
    auto_embed = st.checkbox(t("ingest.auto_embed"), value=True)
    auto_analyze = st.checkbox(
        t("ingest.auto_analyze"), value=False,
        help=t("ingest.auto_analyze.help"),
    )

if uploaded:
    if st.button(t("ingest.button", n=len(uploaded)), type="primary",
                  use_container_width=True):
        progress = st.progress(0.0)
        results = []
        for i, f in enumerate(uploaded, 1):
            status_box = st.empty()
            status_box.info(t(
                "ingest.processing", name=f.name, i=i, total=len(uploaded),
            ))
            try:
                resp = ingest_pdf(f.getvalue(), f.name, embed=auto_embed)
                if resp["status"] == "success":
                    msg = (f"✓ [{resp['paper_id']}] {resp['title'][:60]}  ·  "
                           f"sections={resp['sections_count']}  refs={resp['references_count']}"
                           f"  citations={resp['citations_count']}")
                    if resp.get("embeddings_count"):
                        msg += f"  embeddings={resp['embeddings_count']}"
                    status_box.success(msg)

                    if auto_analyze:
                        with st.spinner(t("ingest.analyzing", id=resp['paper_id'])):
                            try:
                                r2 = client().post(
                                    f"/papers/{resp['paper_id']}/analyze",
                                    timeout=300.0,
                                )
                                r2.raise_for_status()
                                a = r2.json()
                                st.caption(
                                    f"  · LLM analyzed: field='{a.get('research_field')}', "
                                    f"refs_scored={a.get('refs_scored')}, "
                                    f"lit_review={a.get('lit_review_entries')}"
                                )
                            except Exception as e:
                                st.warning(t("ingest.analyze_failed", err=str(e)))
                elif resp["status"] == "skipped":
                    status_box.warning(t(
                        "ingest.skipped",
                        id=resp['paper_id'], title=resp['title'][:60],
                    ))
                else:
                    status_box.error(t(
                        "ingest.failed",
                        name=f.name,
                        err=resp.get('error_message', 'unknown error'),
                    ))
                results.append(resp)
            except Exception as e:
                status_box.error(t("ingest.failed", name=f.name, err=str(e)))
                results.append({"status": "error", "error_message": str(e)})

            progress.progress(i / len(uploaded))

        st.divider()
        ok = sum(1 for r in results if r.get("status") == "success")
        skip = sum(1 for r in results if r.get("status") == "skipped")
        fail = sum(1 for r in results if r.get("status") == "error")
        st.success(t("ingest.done_summary", ok=ok, skip=skip, fail=fail))
        st.page_link("pages/1_Library.py", label=t("ingest.go_to_library"))
