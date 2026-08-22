"""Failed-pickup service credit adapter.

Takes hypothetical parameters as well as real orders, because the second example
question in the brief -- "a pickup is three hours late because of carrier fault,
should I get a service credit?" -- names no order at all. The answer depends
entirely on the account: 2 hours under the SOP, 4 hours under the LumenWorks
agreement. Answering it generically is wrong 25% of the time.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.knowledge.loader import load
from app.policy import engine
from app.policy.types import Decision

DOMAIN = "service_credit"

#: SOP v4 section 3: "Do not promise a credit when carrier fault, pickup timing,
#: or customer fault is unknown." A null in any of these is not a `False`.
REQUIRED_FACTS = ("hours_past_window_end", "carrier_fault", "customer_fault")


def hours_past_window_end(order: dict[str, Any], as_of: datetime) -> float | None:
    """How late a pickup is, measured to the actual pickup if one happened.

    A pickup that has not happened yet is measured to now, because the delay is
    still accruing. A negative result means the window has not closed.
    """
    window_end = order.get("pickup_window_end")
    if window_end is None:
        return None
    reference = order.get("pickup_actual_at") or as_of
    return (reference - window_end).total_seconds() / 3600


def evaluate(
    *,
    account_id: str,
    hours_past_window_end: float | None,
    carrier_fault: bool | None,
    customer_fault: bool | None,
    as_of: datetime,
    shipment_fee_inr: float | None = None,
    order_id: str | None = None,
    history: Sequence[dict[str, Any]] = (),
) -> Decision:
    book = load()

    facts = {
        "hours_past_window_end": hours_past_window_end,
        "carrier_fault": carrier_fault,
        "customer_fault": customer_fault,
        "shipment_fee_inr": shipment_fee_inr,
    }
    facts_used = {"account_id": account_id, "order_id": order_id, **facts}

    unknowns = [name for name in REQUIRED_FACTS if facts.get(name) is None]
    if unknowns:
        return Decision(
            domain=DOMAIN,
            outcome="indeterminate",
            facts_used=facts_used,
            unknowns=unknowns,
            requires_human=True,
            human_reason=(
                "Cannot promise a credit: "
                + ", ".join(unknowns)
                + " unknown. SOP v4 section 3 requires carrier fault, pickup timing and "
                "customer fault to be established first."
            ),
            summary=(
                "Not enough information to decide. "
                + ", ".join(unknowns)
                + " would need to be confirmed before any credit is discussed."
            ),
        )

    resolution = engine.resolve(DOMAIN, facts, account_id=account_id, as_of=as_of, book=book)
    citations = engine.citations_for(resolution)
    overrides = engine.overrides_for(resolution)

    # No rule granted a credit, and every required fact is known -- so this is
    # `ineligible`, not `indeterminate` (D-16). The two are different answers and
    # conflating them either over-promises or refuses to answer at all.
    if resolution.winner is None:
        governing = [
            engine.citation_for(rule)
            for rule in resolution.replacing_rules
            if not any(c.doc_id == rule.source.doc for c in citations)
        ]
        return Decision(
            domain=DOMAIN,
            outcome="ineligible",
            facts_used=facts_used,
            citations=governing + citations,
            overrides=overrides,
            summary=_ineligible_summary(resolution, hours_past_window_end),
        )

    winner = resolution.winner
    outcome = winner.then["outcome"]
    amount = _amount(winner.then.get("amount"), facts)

    decision = Decision(
        domain=DOMAIN,
        outcome=outcome,
        amount_inr=amount,
        facts_used=facts_used,
        citations=citations,
        overrides=overrides,
        contradicts=engine.contradictions_for(DOMAIN, {"amount_inr": amount}, history),
        winning_rule_id=winner.id,
    )

    conflict = engine.conflict_reason(resolution)
    if conflict:
        decision.requires_human = True
        decision.human_reason = conflict

    _apply_constraints(decision, account_id=account_id, book=book)
    decision.summary = _summary(decision)
    return decision


def _amount(spec: dict[str, Any] | None, facts: dict[str, Any]) -> Decimal | None:
    if not spec:
        return None
    if spec["kind"] == "fixed":
        return Decimal(str(spec["fixed_inr"]))
    if spec["kind"] == "lesser_of":
        ceiling = Decimal(str(spec["fixed_inr"]))
        share = spec["percent_of"]
        base = facts.get(share["field"])
        if base is None:
            return ceiling
        proportion = Decimal(str(base)) * Decimal(str(share["percent"])) / Decimal(100)
        return min(ceiling, proportion)
    raise ValueError(f"unknown amount kind: {spec['kind']!r}")


def _apply_constraints(decision: Decision, *, account_id: str, book) -> None:
    for constraint in book.constraints_for(DOMAIN, account_id):
        if constraint.kind == "approval_threshold":
            if decision.amount_inr is not None and decision.amount_inr > Decimal(
                str(constraint.amount_inr)
            ):
                decision.requires_human = True
                decision.human_reason = constraint.human_reason
                decision.citations.append(engine.citation_for_constraint(constraint))
        elif constraint.kind == "monthly_aggregate_cap":
            if decision.outcome == "eligible":
                decision.notes.append(constraint.note or "")
                decision.citations.append(engine.citation_for_constraint(constraint))


def _ineligible_summary(resolution, hours_past_window_end: float | None) -> str:
    replacing = resolution.replacing_rules
    if replacing:
        rule = replacing[0]
        threshold = rule.when.get("hours_past_window_end", {}).get("gt")
        if threshold is not None:
            return (
                f"Not eligible. This account's agreement sets the threshold at {threshold:g} "
                f"hours past the pickup window, and the delay is {hours_past_window_end:g} hours. "
                f"The agreement replaces the standard SOP threshold and amount, so the default "
                f"2-hour rule does not apply here."
            )
    return "Not eligible: no service-credit rule applies to these facts."


def _summary(decision: Decision) -> str:
    if decision.outcome == "ineligible":
        return "Not eligible for a service credit on these facts."
    amount = decision.amount_inr
    text = f"Eligible for a service credit of INR {amount:g}."
    if decision.requires_human:
        text += " Manager approval is required before it can be issued."
    return text
