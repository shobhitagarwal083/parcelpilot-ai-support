"""Account lookups, scoped to the session's principal."""

from __future__ import annotations

from typing import Any

from app.auth.principals import Principal
from app.auth.scope import NotFound, assert_account_access, resolve_account_scope
from app.repo import _hydrate
from app.repo.db import connect


def get(principal: Principal, account_id: str) -> dict[str, Any]:
    assert_account_access(principal, account_id, resource=f"account {account_id}")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
    if row is None:
        raise NotFound(f"no such account: {account_id}")
    return _hydrate.account(row)


def list_for(principal: Principal, account_id: str | None = None) -> list[dict[str, Any]]:
    allowed = resolve_account_scope(principal, account_id)
    with connect() as conn:
        if allowed is None:
            rows = conn.execute("SELECT * FROM accounts ORDER BY account_id")
        else:
            placeholders = ",".join("?" * len(allowed))
            rows = conn.execute(
                f"SELECT * FROM accounts WHERE account_id IN ({placeholders}) ORDER BY account_id",
                tuple(sorted(allowed)),
            )
        return [_hydrate.account(row) for row in rows]
