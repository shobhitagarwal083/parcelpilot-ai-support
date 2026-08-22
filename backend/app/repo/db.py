"""SQLite access, plus the tables the application owns rather than ingests.

`python -m app.ingest` rebuilds the database from source and drops everything in
it, so application tables are created lazily on first connect rather than by the
ingest. Source data and application state have different lifecycles and it
should be impossible to confuse them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app import config

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    action       TEXT NOT NULL,
    resource     TEXT,
    outcome      TEXT NOT NULL,
    detail       TEXT
);

CREATE TABLE IF NOT EXISTS pending_actions (
    action_id    TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    target_id    TEXT,
    payload      TEXT NOT NULL,
    decision     TEXT,
    status       TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT
);
"""

_initialised = False


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    global _initialised
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if not _initialised:
            conn.executescript(APP_SCHEMA)
            conn.commit()
            _initialised = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def reset_initialisation() -> None:
    """Force the schema check to run again -- used when tests swap databases."""
    global _initialised
    _initialised = False
