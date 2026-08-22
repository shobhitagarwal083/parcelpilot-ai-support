"""Cancellation adapter.

The headline trap lives here. The SOP is longer, more specific about fees, and
retrieves strongly on "cancellation fee"; the Northstar clause that waives it is
one sentence in a different document. Tier precedence is what separates them,
and the SOP is disclosed rather than dropped so the answer can show its working.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.policy import engine, known_issues
from app.policy.calendar import wall_minutes_between
from app.policy.types import Decision

DOMAIN = "cancellation"


def evaluate(
    *,
    order: dict[str, Any],
    as_of: datetime,
    history: Sequence[dict[str, Any]] = (),
) -> Decision:
    booked_at = order["booked_at"]

    # The elapsed time that matters is measured to when the customer ASKED, not
    # to now. ORD-3001 was booked at 10:25 and cancellation requested at 10:40 --
    # inside the 30-minute grace window. Measuring to the snapshot instead would
    # put it at 35 minutes and charge a fee that was never owed.
    requested_at = order.get("cancellation_requested_at") or as_of
    minutes_since_booking = wall_minutes_between(booked_at, requested_at)

    facts = {
        "order_status": order["status"],
        "minutes_since_booking": minutes_since_booking,
    }

    resolution = engine.resolve(
        DOMAIN, facts, account_id=order["account_id"], as_of=as_of, book=None
    )

    facts_used = {
        "order_id": order.get("order_id"),
        "order_status": order["status"],
        "booked_at": booked_at.isoformat(),
        "cancellation_requested_at": (
            order["cancellation_requested_at"].isoformat()
            if order.get("cancellation_requested_at")
            else None
        ),
        "minutes_since_booking": minutes_since_booking,
        "measured_to": "cancellation request" if order.get("cancellation_requested_at") else "now",
    }

    caveat_facts = {"carrier": order.get("carrier"), "order_status": order["status"]}
    caveats = known_issues.caveats_for(caveat_facts)

    if resolution.winner is None:
        return Decision(
            domain=DOMAIN,
            outcome="indeterminate",
            facts_used=facts_used,
            unknowns=["order_status"],
            citations=engine.citations_for(resolution),
            overrides=engine.overrides_for(resolution),
            caveats=caveats,
            requires_human=True,
            human_reason=(
                f"No cancellation rule covers status {order['status']!r}. "
                f"The SOP defines DRAFT, BOOKED, PICKED_UP and DELIVERED only."
            ),
            summary=f"Cannot determine cancellation terms for status {order['status']}.",
        )

    winner = resolution.winner
    outcome = winner.then["outcome"]
    fee = winner.then.get("fee_inr")
    next_step = winner.then.get("next_step")

    facts_used["fee_inr"] = fee
    if next_step:
        facts_used["next_step"] = next_step

    contradicts = engine.contradictions_for(DOMAIN, {"fee_inr": fee}, history)

    conflict = engine.conflict_reason(resolution)

    return Decision(
        domain=DOMAIN,
        outcome=outcome,
        amount_inr=None if fee is None else Decimal(str(fee)),
        facts_used=facts_used,
        citations=engine.citations_for(resolution) + known_issues.citations_for(caveat_facts),
        overrides=engine.overrides_for(resolution),
        contradicts=contradicts,
        caveats=caveats,
        requires_human=bool(conflict),
        human_reason=conflict,
        winning_rule_id=winner.id,
        summary=_summary(outcome, fee, next_step),
    )


def _summary(outcome: str, fee: float | None, next_step: str | None) -> str:
    if outcome == "denied":
        if next_step == "return_to_origin":
            return (
                "This shipment cannot be cancelled -- it has already been picked up. "
                "Use the return-to-origin workflow if the parcel needs to come back."
            )
        return "This shipment cannot be cancelled -- it has already been delivered."
    if fee == 0:
        return "Cancellation is allowed with no fee."
    return f"Cancellation is allowed; a INR {fee:g} fee applies."
