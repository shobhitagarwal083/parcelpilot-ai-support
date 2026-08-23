# One image, one process, one URL. See app/api/main.py for why the frontend is
# served from the API rather than deployed separately -- the short version is
# that SSE hates extra hops.

# ---------------------------------------------------------------- frontend build

FROM node:20-slim AS frontend

WORKDIR /build

# Copy the manifests alone first, so the dependency layer is cached and a source
# edit does not reinstall node_modules.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime

FROM python:3.12-slim AS runtime

# PYTHONUNBUFFERED so logs appear in `fly logs` as they happen rather than when
# a buffer happens to flush -- the same class of problem as a buffered SSE
# stream, and just as confusing to debug.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/pyproject.toml backend/README.md* ./backend/
COPY backend/app ./backend/app
RUN pip install --no-cache-dir -e ./backend

# The source pack: PDFs and the workbook. Ingest reads these.
COPY data/source ./data/source

# Build the index and database into the image.
#
# Doing this at build time rather than on boot means a cold start serves
# immediately instead of parsing six PDFs first, and it fails the *build* if the
# source pack is malformed rather than failing the first request in production.
# The data is a fixed snapshot, so there is nothing to refresh at runtime.
RUN cd backend && python -m app.ingest

COPY --from=frontend /build/dist ./frontend/dist

# Non-root. Nothing here needs to write outside the app directory: the snapshot
# is read-only and the audit and action tables live in the database file that
# ingest already created.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

EXPOSE 8080

# The port is read at runtime, not baked in. Render injects $PORT and expects
# the process to bind it; 8080 is the fallback so `docker run -p 8080:8080`
# still works locally and on any host that does not inject one.
ENV PORT=8080

# One worker, deliberately. The rate limiter holds its counters in process and a
# second worker would silently double every limit; SQLite's single writer makes
# multiple workers a bad idea here regardless. Scaling past this means Postgres
# and a shared limiter store, not more workers.
#
# `sh -c` so $PORT is expanded -- the plain exec form would pass the literal
# string. `exec` then replaces the shell with uvicorn, which matters more than
# it looks: without it the shell stays PID 1 and swallows SIGTERM, so a deploy
# or scale-down would hard-kill the process mid-stream instead of draining it.
CMD ["sh", "-c", "exec python -m uvicorn app.api.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --timeout-keep-alive 75"]
