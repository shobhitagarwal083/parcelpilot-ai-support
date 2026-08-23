"""FastAPI application.

One container serves the API and the built frontend, which keeps deployment to
a single process and a single URL.

That is a deliberate choice and the deciding reason is streaming. The interface
has to show tool calls *as they happen* (requirement 6), and every hop between
the browser and this process is somewhere a proxy can buffer an SSE stream --
which does not error, it just quietly turns a live trace into one delayed dump.
The chat endpoint already sets `X-Accel-Buffering: no` for that reason; adding a
CDN in front would add another place to get it wrong.

Same-origin also means no CORS, one secret store, and one deploy that cannot
drift out of step with itself. The frontend addresses the API as `/api`, with no
host anywhere in it, so it is same-origin by construction.

At scale the move is to put the static assets on an edge while keeping a single
origin and path-routing `/api` here -- not to split the deploy. The frontend is
53KB of cacheable static output and will never be the bottleneck; the ceiling is
SQLite's single writer and the in-process rate limiter, neither of which is
helped by moving React somewhere else.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.api import actions, chat, limits, records, session, signals

#: Built by `npm run build`; produced at image build time in the Dockerfile.
FRONTEND_DIST = config.ROOT_DIR / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(
        title="ParcelPilot AI Support",
        description="Authority-aware support agent over the ParcelPilot data pack.",
        version="0.1.0",
    )

    # Before the routers: a public URL in front of a model endpoint needs a
    # ceiling, and it should reject before any work is done.
    app.middleware("http")(limits.rate_limit)

    app.include_router(session.router)
    app.include_router(records.router)
    app.include_router(actions.router)
    app.include_router(signals.router)
    app.include_router(chat.router)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "snapshot_at": config.SNAPSHOT_AT.isoformat(),
            "provider": config.PROVIDER,
            "model": config.primary_model(),
            # Whether a key is present, never the key itself. Enough to diagnose
            # a misconfigured deployment without putting a secret in a response
            # any visitor can fetch.
            "credentials": bool(config.api_key()),
        }

    _serve_frontend(app)
    return app


def _serve_frontend(app: FastAPI) -> None:
    """Serve the built interface, if one has been built.

    Absent in a fresh checkout and during backend-only development, where the
    Vite dev server owns the frontend and proxies here. Mounting conditionally
    keeps `uvicorn app.api.main:app` working in both cases rather than failing
    on a missing directory.
    """
    if not FRONTEND_DIST.is_dir():
        return

    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        """Return index.html for any non-API path, so a refresh works anywhere.

        Registered last, so the API routers match first -- a catch-all declared
        before them would swallow every endpoint. An unmatched /api path must
        still 404 rather than silently returning the HTML shell, which would
        turn a typo'd endpoint into a confusing parse error in the client.
        """
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")

        candidate = (FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)

        return FileResponse(FRONTEND_DIST / "index.html")


app = create_app()
