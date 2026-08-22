"""Read source records straight from the ingested database.

Deliberately does not go through the repository layer: these are tests of the
policy engine, and they should keep passing whether or not access control is
wired up. Phase 04's tests cover the repositories on their own.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app import config

_ORDER_TIMES = (
    "booked_at", "pickup_window_start", "pickup_window_end",
    "pickup_actual_at", "cancellation_requested_at",
)
_TICKET_TIMES = ("created_at", "last_customer_message_at")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(config.TZ) if value else None


def order(order_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = dict(conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone())
    for column in _ORDER_TIMES:
        row[column] = _parse(row[column])
    row["carrier_fault"] = bool(row["carrier_fault"])
    row["customer_fault"] = bool(row["customer_fault"])
    return row


def account(account_id: str) -> dict[str, Any]:
    with _connect() as conn:
        return dict(
            conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        )


def ticket(ticket_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = dict(
            conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        )
    for column in _TICKET_TIMES:
        row[column] = _parse(row[column])
    return row


def resolution_history(account_id: str) -> list[dict[str, Any]]:
    """Closed tickets carrying a recorded resolution -- tier 4, context only."""
    with _connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM tickets WHERE account_id = ? AND historical_resolution IS NOT NULL",
                (account_id,),
            )
        ]


def hours_past_window_end(row: dict[str, Any], as_of: datetime) -> float:
    reference = row["pickup_actual_at"] or as_of
    return (reference - row["pickup_window_end"]).total_seconds() / 3600
