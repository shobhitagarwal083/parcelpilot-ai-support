"""Order lookups, scoped to the session's principal.

The scope check runs on the row's own account, after the fetch. Filtering the
query instead would return "not found" for a record that exists, which is a
different and less honest answer than "outside your scope" -- and it would make
the boundary invisible in the audit log.
"""

from __future__ import annotations

from typing import Any

from app.auth.principals import Principal
from app.auth.scope import NotFound, assert_account_access, resolve_account_scope
from app.repo import _hydrate
from app.repo.db import connect


def get(principal: Principal, order_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if row is None:
        raise NotFound(f"no such order: {order_id}")
    assert_account_access(
        principal, row["account_id"], resource=f"order {order_id}", conceal_existence=True
    )
    return _hydrate.order(row)


def list_for(
    principal: Principal,
    account_id: str | None = None,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    allowed = resolve_account_scope(principal, account_id)

    clauses, params = [], []
    if allowed is not None:
        clauses.append(f"account_id IN ({','.join('?' * len(allowed))})")
        params.extend(sorted(allowed))
    if status:
        clauses.append("status = ?")
        params.append(status)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM orders{where} ORDER BY order_id", tuple(params))
        return [_hydrate.order(row) for row in rows]
