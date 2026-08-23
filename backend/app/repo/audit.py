"""Append-only record of what was asked, what was refused, and what was executed.

Every denial writes a row. A guard that fails silently is indistinguishable from
one that was never reached, and the difference matters when someone asks whether
the boundary actually held.

On timestamps: rows carry `config.SNAPSHOT_AT`, because the whole system runs at
a pinned instant and no module outside config.py may read a clock. In a real
deployment this is the one line that would change -- and it changes in exactly
one place, which is the point of the design.
"""

from __future__ import annotations

from typing import Any

from app import config
from app.repo.db import connect


def record(
    *,
    principal_id: str,
    action: str,
    outcome: str,
    resource: str | None = None,
    detail: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (at, principal_id, action, resource, outcome, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (config.SNAPSHOT_AT.isoformat(), principal_id, action, resource, outcome, detail),
        )


def entries(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        ]


def count(*, outcome: str | None = None) -> int:
    with connect() as conn:
        if outcome is None:
            return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE outcome = ?", (outcome,)
        ).fetchone()[0]
