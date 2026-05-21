"""Thin httpx wrapper around the paperdb HTTP API.

Streamlit pages import `client()` to make calls. Base URL is read from
PAPERDB_API_URL env var (defaults to http://localhost:8765).

Read-only GETs are wrapped in `@st.cache_data` (30s TTL) so re-renders
triggered by page navigation or sidebar toggles serve from cache instead
of re-firing identical HTTP requests. This eliminates the slow-request
window that causes a black flash during Streamlit's full-script rerun
between page switches. CLI-side changes still propagate within ~30s.
Mutating endpoints (ingest, search, agent) are NOT cached.
"""

from __future__ import annotations

import json
import os
from typing import Iterator, Optional

import httpx
import streamlit as st


def base_url() -> str:
    return os.environ.get("PAPERDB_API_URL", "http://localhost:8765").rstrip("/")


_client: Optional[httpx.Client] = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=base_url(), timeout=300.0)
    return _client


# ── REST helpers ──────────────────────────────────────────────────


@st.cache_data(ttl=30, show_spinner=False)
def list_papers(limit: int = 50, offset: int = 0, tag: Optional[str] = None) -> list[dict]:
    params: dict = {"limit": limit, "offset": offset}
    if tag:
        params["tag"] = tag
    r = client().get("/papers", params=params)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=30, show_spinner=False)
def get_paper(paper_id: int) -> dict:
    r = client().get(f"/papers/{paper_id}")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=30, show_spinner=False)
def get_sections(paper_id: int) -> list[dict]:
    r = client().get(f"/papers/{paper_id}/sections")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=30, show_spinner=False)
def get_references(paper_id: int) -> list[dict]:
    r = client().get(f"/papers/{paper_id}/references")
    r.raise_for_status()
    return r.json()


def search(query: str, mode: str = "hybrid", top_k: int = 10,
            rerank: bool = False, **filters) -> dict:
    body = {"query": query, "mode": mode, "top_k": top_k, "rerank": rerank, **filters}
    r = client().post("/search", json={k: v for k, v in body.items() if v is not None})
    r.raise_for_status()
    return r.json()


def ingest_pdf(file_bytes: bytes, filename: str, embed: bool = False) -> dict:
    r = client().post(
        "/papers/ingest",
        files={"file": (filename, file_bytes, "application/pdf")},
        params={"embed": str(embed).lower()},
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=30, show_spinner=False)
def stats() -> dict:
    r = client().get("/stats")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=10, show_spinner=False)
def health() -> tuple[bool, str]:
    """Lightweight check; returns (ok, message). Cached briefly — a few
    seconds of stale 'healthy' is fine and prevents per-page reconnect chatter."""
    try:
        r = client().get("/healthz", timeout=2.0)
        r.raise_for_status()
        return True, "API reachable"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── Agent SSE streaming ──────────────────────────────────────────


def agent_stream(query: str, task: str = "auto",
                  no_synth: bool = False) -> Iterator[tuple[str, dict]]:
    """Yields (event_name, payload_dict) as the agent runs."""
    body = {"query": query, "task": task, "no_synth": no_synth}
    with httpx.stream("POST", f"{base_url()}/agent", json=body,
                       timeout=None) as r:
        r.raise_for_status()
        current_event = "message"
        for line in r.iter_lines():
            if line is None:
                continue
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"raw": payload}
                yield current_event, data
                current_event = "message"
