"""Structured-data lookup tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.tools.registry import _object, tool
from app.auth.principals import Principal
from app.auth.scope import resolve_subject_account
from app.repo import accounts, orders, tickets


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in record.items()
    }


@tool(
    name="get_account",
    description=(
        "Look up an account: plan, status, dedicated CSM, and whether a signed agreement "
        "exists. Omit account_id to use the account this session belongs to."
    ),
    parameters=_object(
        {"account_id": {"type": "string", "description": "e.g. ACCT-001. Optional."}}
    ),
    trace_label="looking up the account",
)
def get_account(
    principal: Principal, *, as_of: datetime, account_id: str | None = None
) -> dict[str, Any]:
    resolved = resolve_subject_account(principal, account_id)
    return _serialise(accounts.get(principal, resolved))


@tool(
    name="get_order",
    description="Look up one order by ID, including pickup window, fault flags and fee.",
    parameters=_object(
        {"order_id": {"type": "string", "description": "e.g. ORD-1001."}},
        required=["order_id"],
    ),
    trace_label="looking up the order",
)
def get_order(principal: Principal, *, as_of: datetime, order_id: str) -> dict[str, Any]:
    return _serialise(orders.get(principal, order_id))


@tool(
    name="list_orders",
    description=(
        "List orders visible to this session. Optionally filter by account or status "
        "(DRAFT, BOOKED, PICKED_UP, DELIVERED)."
    ),
    parameters=_object(
        {
            "account_id": {"type": "string"},
            "status": {"type": "string"},
        }
    ),
    trace_label="listing orders",
)
def list_orders(
    principal: Principal,
    *,
    as_of: datetime,
    account_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    found = orders.list_for(principal, account_id, status=status)
    return {"count": len(found), "orders": [_serialise(o) for o in found]}


@tool(
    name="get_ticket",
    description=(
        "Look up one support ticket. If it carries a historical_resolution, that is tier-4 "
        "evidence of what was said before -- context only, never authority, and known to "
        "contain incorrect past guidance."
    ),
    parameters=_object(
        {"ticket_id": {"type": "string", "description": "e.g. TKT-501."}},
        required=["ticket_id"],
    ),
    trace_label="looking up the ticket",
)
def get_ticket(principal: Principal, *, as_of: datetime, ticket_id: str) -> dict[str, Any]:
    return _serialise(tickets.get(principal, ticket_id))


@tool(
    name="search_tickets",
    description=(
        "Search support tickets visible to this session by text, status or account. Recorded "
        "resolutions on closed tickets are tier-4 context, not policy."
    ),
    parameters=_object(
        {
            "query": {"type": "string", "description": "Text to match in subject or description."},
            "status": {"type": "string", "description": "open or closed."},
            "account_id": {"type": "string"},
        }
    ),
    trace_label="searching tickets",
)
def search_tickets(
    principal: Principal,
    *,
    as_of: datetime,
    query: str | None = None,
    status: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    found = tickets.search(principal, query=query, status=status, account_id=account_id)
    return {"count": len(found), "tickets": [_serialise(t) for t in found]}
