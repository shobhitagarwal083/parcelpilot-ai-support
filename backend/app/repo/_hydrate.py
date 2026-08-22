"""Turn SQLite rows into domain-shaped dicts, once, in one place."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app import config

ORDER_TIMESTAMPS = (
    "booked_at", "pickup_window_start", "pickup_window_end",
    "pickup_actual_at", "cancellation_requested_at",
)
TICKET_TIMESTAMPS = ("created_at", "last_customer_message_at")
ORDER_BOOLEANS = ("carrier_fault", "customer_fault")

#: Attached to every recorded resolution the repositories hand out. Two of the
#: values in this dataset are wrong, and both are wrong in ways that a retriever
#: finds compelling -- same customer, same question, apparently authoritative.
TIER_4_WARNING = (
    "Tier 4: a recorded resolution from a past ticket. Context only, never "
    "authority, and known to contain incorrect past guidance."
)


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(config.TZ) if value else None


def order(row: Any) -> dict[str, Any]:
    record = dict(row)
    for column in ORDER_TIMESTAMPS:
        record[column] = _timestamp(record[column])
    for column in ORDER_BOOLEANS:
        record[column] = bool(record[column])
    return record


def ticket(row: Any) -> dict[str, Any]:
    record = dict(row)
    for column in TICKET_TIMESTAMPS:
        record[column] = _timestamp(record[column])
    if record.get("historical_resolution"):
        record["historical_resolution_authority"] = TIER_4_WARNING
    return record


def account(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["premium_support"] = bool(record["premium_support"])
    record["has_agreement"] = bool(record.get("contract_file"))
    return record
