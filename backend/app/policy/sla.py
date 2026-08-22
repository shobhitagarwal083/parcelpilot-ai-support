"""First-response SLA adapter.

Two traps intersect here.

The clock starts at `created_at`, and at nothing else. The workbook has no
`first_response_at` column at all -- nothing in it records a ParcelPilot reply --
but it does have `last_customer_message_at`, which is real, recent, and adjacent.
Reaching for "the most recent timestamp on this record" moves TKT-501 from 30
minutes elapsed to 8, and the headline breach in the dataset silently disappears.
A further message from the customer is evidence the first response has not
happened; it is not a reset.

And the snapshot is a Sunday, so coverage decides everything. `24x7` bypasses the
calendar; `business` means three of the five open tickets have not started their
clocks at all. `not_started` is therefore a distinct outcome from
`within_target`, not a nicety: at this snapshot it is one of only two fields that
tell a correct implementation apart from a wall-clock one (D-13).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.policy import calendar, engine
from app.policy import severity as severity_module
from app.policy.types import Decision

DOMAIN = "sla"


def evaluate(
    *,
    ticket: dict[str, Any],
    account: dict[str, Any],
    as_of: datetime,
    severity_override: str | None = None,
) -> Decision:
    created_at = ticket["created_at"]
    plan = account["plan"]

    verdict = severity_module.classify(ticket.get("subject", ""), ticket.get("description", ""))
    level = severity_override or verdict.level

    facts_used: dict[str, Any] = {
        "ticket_id": ticket.get("ticket_id"),
        "account_id": account["account_id"],
        "plan": plan,
        # Named explicitly so it is visible in every answer that the clock runs
        # from ticket creation. last_customer_message_at is never read.
        "created_at": created_at.isoformat(),
        "clock_starts_at": "created_at",
        "severity": level,
        "severity_rule_id": verdict.rule.id if verdict.rule else None,
    }

    if level is None:
        return Decision(
            domain=DOMAIN,
            outcome="indeterminate",
            facts_used=facts_used,
            unknowns=["severity"],
            requires_human=True,
            human_reason=(
                "Severity could not be classified from the ticket text, and the response target "
                "depends on it. Asking is better than guessing a target we would then be held to."
            ),
            summary="Severity is unclear, so no response target can be stated yet.",
        )

    resolution = engine.resolve(
        DOMAIN, {"plan": plan, "severity": level}, account_id=account["account_id"], as_of=as_of
    )

    if resolution.winner is None:
        return Decision(
            domain=DOMAIN,
            outcome="indeterminate",
            facts_used=facts_used,
            unknowns=["response_target"],
            citations=engine.citations_for(resolution),
            requires_human=True,
            human_reason=f"No first-response target is defined for plan {plan!r} at {level}.",
            summary=f"No response target is defined for a {level} on the {plan} plan.",
        )

    winner = resolution.winner
    target_minutes = winner.then["minutes"]
    coverage = winner.then["coverage"]

    due_at = calendar.add_minutes(created_at, target_minutes, coverage=coverage)
    elapsed = calendar.elapsed_minutes(created_at, as_of, coverage=coverage)

    if as_of > due_at:
        outcome = "breached"
        breach_by = elapsed - target_minutes
    elif elapsed == 0:
        outcome = "not_started"
        breach_by = 0
    else:
        outcome = "within_target"
        breach_by = 0

    facts_used |= {
        "target_minutes": target_minutes,
        "coverage": coverage,
        "due_at": due_at.isoformat(),
        "elapsed_minutes": elapsed,
        "breach_minutes": breach_by,
    }

    decision = Decision(
        domain=DOMAIN,
        outcome=outcome,
        facts_used=facts_used,
        citations=engine.citations_for(resolution),
        overrides=engine.overrides_for(resolution),
        winning_rule_id=winner.id,
        summary=_summary(outcome, level, target_minutes, coverage, due_at, breach_by),
    )

    conflict = engine.conflict_reason(resolution)
    if conflict:
        decision.requires_human = True
        decision.human_reason = conflict
    elif outcome == "breached":
        # Policy v3 section 4: state the breach plainly and recommend escalation
        # rather than hiding uncertainty.
        decision.requires_human = True
        decision.human_reason = (
            f"First-response target breached by {breach_by} minutes. Policy v3 section 4 "
            f"requires the breach to be stated and escalation recommended."
        )
    elif level == "P1":
        decision.requires_human = True
        decision.human_reason = "P1 incidents are escalated immediately (Policy v3 section 4)."

    return decision


def _summary(
    outcome: str,
    level: str,
    target_minutes: int,
    coverage: str,
    due_at: datetime,
    breach_by: int,
) -> str:
    target = _describe_target(target_minutes, coverage)
    when = calendar.describe(due_at)
    if outcome == "breached":
        return (
            f"{level}, target {target}. Response was due {when} and has not happened -- "
            f"breached by {breach_by} minutes."
        )
    if outcome == "not_started":
        return (
            f"{level}, target {target}. This account has no weekend or after-hours coverage, "
            f"so the response clock has not started yet; it begins on the next business day "
            f"and the response is due {when}."
        )
    return f"{level}, target {target}. Within target; the response is due {when}."


def _describe_target(minutes: int, coverage: str) -> str:
    if coverage == "24x7":
        unit = f"{minutes} minutes" if minutes < 60 else f"{minutes // 60} hours"
        return f"{unit}, 24x7"
    if minutes % 540 == 0:
        days = minutes // 540
        return f"{days} business day{'s' if days > 1 else ''}"
    return f"{minutes // 60} business hours" if minutes >= 60 else f"{minutes} business minutes"
