"""FastAPI application.

One container serves the API and the built frontend, which keeps deployment to
a single process and a single URL.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import config
from app.api import actions, session, signals


def create_app() -> FastAPI:
    app = FastAPI(
        title="ParcelPilot AI Support",
        description="Authority-aware support agent over the ParcelPilot data pack.",
        version="0.1.0",
    )

    app.include_router(session.router)
    app.include_router(actions.router)
    app.include_router(signals.router)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "snapshot_at": config.SNAPSHOT_AT.isoformat(),
            "model": config.PRIMARY_MODEL,
        }

    return app


app = create_app()
