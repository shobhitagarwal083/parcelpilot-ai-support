"""Read-only order and ticket lookups.

Used by the triage board to open the evidence behind a signal. Scoped exactly
like every other path -- and denials here are flattened to 404 so the status
codes cannot be walked to learn another account's record volume.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, to_http
from app.repo import orders, tickets

router = APIRouter(prefix="/api", tags=["records"])


def _serialise(record: dict) -> dict:
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in record.items()
    }


@router.get("/orders")
def list_orders(principal: CurrentPrincipal, account_id: str | None = None) -> dict:
    try:
        found = orders.list_for(principal, account_id)
    except Exception as exc:
        raise to_http(exc) from exc
    return {"orders": [_serialise(o) for o in found]}


@router.get("/orders/{order_id}")
def get_order(order_id: str, principal: CurrentPrincipal) -> dict:
    try:
        return _serialise(orders.get(principal, order_id))
    except Exception as exc:
        raise to_http(exc) from exc


@router.get("/tickets")
def list_tickets(
    principal: CurrentPrincipal, account_id: str | None = None, status: str | None = None
) -> dict:
    try:
        found = tickets.search(principal, account_id=account_id, status=status)
    except Exception as exc:
        raise to_http(exc) from exc
    return {"tickets": [_serialise(t) for t in found]}


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, principal: CurrentPrincipal) -> dict:
    try:
        return _serialise(tickets.get(principal, ticket_id))
    except Exception as exc:
        raise to_http(exc) from exc
