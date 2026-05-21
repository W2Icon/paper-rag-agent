"""FastAPI dependencies — DB connection + LLM/Embedder providers (lazy)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from config import get_config
from db_connection import DatabaseConnection


_db_singleton: Optional[DatabaseConnection] = None


def get_db() -> DatabaseConnection:
    """One persistent connection per process. SQLite WAL + check_same_thread=False
    makes this safe across FastAPI's threaded request handlers."""
    global _db_singleton
    if _db_singleton is None:
        cfg = get_config()
        cfg.ensure_dirs()
        _db_singleton = DatabaseConnection(cfg.db_path)
        _db_singleton.connect()
        # Ensure schema exists
        from schema import init_schema
        init_schema(_db_singleton)
    return _db_singleton


@lru_cache(maxsize=1)
def get_llm_provider_cached():
    """Cache the LLM provider so we don't re-init OpenAI clients per request."""
    from llm_interface import LLMConfig, get_llm_provider
    cfg = LLMConfig.from_env()
    if cfg.provider in ("none", ""):
        return None
    try:
        return get_llm_provider(cfg)
    except Exception as e:  # pragma: no cover
        raise HTTPException(
            status_code=503,
            detail=f"LLM provider unavailable: {e}",
        )


@lru_cache(maxsize=1)
def get_embedder_cached():
    from embedder import PaperEmbedder
    provider = get_llm_provider_cached()
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="LLM/embedder not configured. Set LLM_PROVIDER + LLM_API_KEY.",
        )
    return PaperEmbedder(provider)
