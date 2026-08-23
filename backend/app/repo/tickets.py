"""Ticket lookups, scoped to the session's principal.

Recorded resolutions are returned, never hidden -- but always carrying their
tier-4 warning. The right behaviour when history is wrong is to say so, and that
is only possible if the history is visible in the first place.
"""

from __future__ import annotations

from typing import Any

from app.auth.principals import Principal
from app.auth.scope import NotFound, assert_account_access, resolve_account_scope
from app.repo import _hydrate
from app.repo.db import connect


def get(principal: Principal, ticket_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise NotFound(f"no such ticket: {ticket_id}")
    assert_account_access(
        principal, row["account_id"], resource=f"ticket {ticket_id}", conceal_existence=True
    )
    return _hydrate.ticket(row)


def search(
    principal: Principal,
    *,
    account_id: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    allowed = resolve_account_scope(principal, account_id)

    clauses, params = [], []
    if allowed is not None:
        clauses.append(f"account_id IN ({','.join('?' * len(allowed))})")
        params.extend(sorted(allowed))
    if status:
        clauses.append("status = ?")
        params.append(status)
    if query:
        clauses.append("(LOWER(subject) LIKE ? OR LOWER(description) LIKE ?)")
        params.extend([f"%{query.lower()}%"] * 2)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM tickets{where} ORDER BY created_at DESC", tuple(params))
        return [_hydrate.ticket(row) for row in rows]


def resolution_history(principal: Principal, account_id: str) -> list[dict[str, Any]]:
    """Closed tickets on this account that recorded an answer.

    Fed to the policy engine so a decision can flag its own history as wrong.
    """
    assert_account_access(principal, account_id, resource=f"account {account_id}")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE account_id = ? AND historical_resolution IS NOT NULL "
            "ORDER BY created_at",
            (account_id,),
        )
        return [_hydrate.ticket(row) for row in rows]


def all_with_resolutions() -> list[dict[str, Any]]:
    """Unscoped -- for the signals engine, which runs under an internal principal
    that has already been checked for the `signals` capability."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE historical_resolution IS NOT NULL ORDER BY ticket_id"
        )
        return [_hydrate.ticket(row) for row in rows]
