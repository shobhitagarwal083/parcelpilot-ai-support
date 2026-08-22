from __future__ import annotations

import shutil
import sqlite3

import pytest

from app import config
from app.repo import db


@pytest.fixture
def now():
    """The pinned snapshot: 2026-08-16 11:00 IST, a Sunday.

    Injected as a fixture and never read from the system clock, so these tests
    return the same answers in 2030 as they do today.
    """
    return config.SNAPSHOT_AT


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Give every test its own copy of the database.

    Denials write audit rows, so tests that assert on the audit log need a clean
    one -- and no test should leave state behind in the developer's working copy.
    """
    if not config.DB_PATH.exists():  # pragma: no cover - a clean checkout
        pytest.skip("run `python -m app.ingest` first")
    working = tmp_path / "parcelpilot.db"
    shutil.copy(config.DB_PATH, working)
    # Source tables come from ingest; application tables start empty, so audit
    # assertions see only what the test itself caused.
    with sqlite3.connect(working) as conn:
        conn.execute("DROP TABLE IF EXISTS audit_log")
        conn.execute("DROP TABLE IF EXISTS pending_actions")
    monkeypatch.setattr(config, "DB_PATH", working)
    db.reset_initialisation()
    yield working
    db.reset_initialisation()
