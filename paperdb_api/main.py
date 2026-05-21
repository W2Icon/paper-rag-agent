"""FastAPI app factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="paperdb",
        description="Academic paper database + multi-agent research assistant",
        version="0.4.0",
    )

    # CORS: allow Streamlit (8501) and dev tools to call us
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # LAN-only deployment per design
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers (lazy import to keep cold-start fast and surface
    #    optional-deps errors clearly) ────────────────────────────
    from paperdb_api.routers import papers, search
    app.include_router(papers.router)
    app.include_router(search.router)

    from paperdb_api.routers import agent, analysis, cross, stats as stats_router
    app.include_router(agent.router)
    app.include_router(analysis.router)
    app.include_router(cross.router)
    app.include_router(stats_router.router)

    @app.get("/healthz", tags=["meta"])
    def healthz():
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root():
        from config import get_config
        cfg = get_config()
        return {
            "name": "paperdb",
            "version": "0.4.0",
            "db_path": str(cfg.db_path),
            "data_dir": str(cfg.data_dir),
            "config_source": cfg.config_source,
        }

    return app


app = create_app()
