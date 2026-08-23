"""Proactive issue detection -- the client's Problem 1.

Every reactive support system answers questions it is asked. The argument for
this module is a single record: ORD-2002 is 4.5 hours past its pickup window,
the carrier has accepted fault, the customer is not at fault, INR 300 is owed
under their agreement -- and no ticket exists. Nobody asked, so nobody finds it.

Detectors are deterministic and reuse the same policy engine as the chat path,
so a signal and an answer can never disagree about what a customer is owed. Each
one carries its evidence and, where money is involved, the full Decision.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.auth.principals import SIGNALS, Principal
from app.auth.scope import require_capability
from app.knowledge.loader import load
from app.policy import cancellation, engine, known_issues, service_credit, severity, sla
from app.policy.types import Decision
from app.repo import accounts, orders, tickets

Rank = Literal["critical", "high", "medium", "low"]

RANK_ORDER: dict[Rank, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_ROW_COUNT = re.compile(r"([\d,]+)\s*[-\s]?row")


@dataclass(frozen=True)
class Signal:
    id: str
    detector: str
    rank: Rank
    title: str
    detail: str
    evidence: tuple[str, ...]
    account_id: str | None = None
    suggested_action: str | None = None
    decision: Decision | None = None
    citations: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "detector": self.detector,
            "rank": self.rank,
            "title": self.title,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "account_id": self.account_id,
            "suggested_action": self.suggested_action,
            "decision": self.decision.to_dict() if self.decision else None,
        }


def detect(principal: Principal, as_of: datetime) -> list[Signal]:
    """Run every detector. Requires the `signals` capability, which no customer has."""
    require_capability(principal, SIGNALS, action="detect_issues")

    all_orders = orders.list_for(principal)
    all_tickets = tickets.search(principal)
    all_accounts = {a["account_id"]: a for a in accounts.list_for(principal)}

    found: list[Signal] = []
    found += _sla_breaches(all_tickets, all_accounts, as_of)
    found += _security_incidents(all_tickets)
    found += _silent_credits(all_orders, all_tickets, as_of)
    found += _known_issue_clusters(all_tickets, all_accounts)
    found += _stalled_cancellations(all_orders)
    found += _carrier_concentration(all_orders)
    found += _contradicted_guidance(all_tickets, as_of)

    found.sort(key=lambda s: (RANK_ORDER[s.rank], s.id))
    return found


# ---------------------------------------------------------------- detectors


def _open(all_tickets: list[dict]) -> list[dict]:
    return [t for t in all_tickets if t["status"] == "open"]


def _sla_breaches(all_tickets, all_accounts, as_of) -> list[Signal]:
    signals = []
    for ticket in _open(all_tickets):
        account = all_accounts.get(ticket["account_id"])
        if account is None:
            continue
        decision = sla.evaluate(ticket=ticket, account=account, as_of=as_of)
        if decision.outcome != "breached":
            continue
        breach = decision.facts_used["breach_minutes"]
        signals.append(
            Signal(
                id=f"sla_breached:{ticket['ticket_id']}",
                detector="sla_breached",
                rank="critical",
                title=f"{ticket['ticket_id']} has breached its first-response target",
                detail=(
                    f"{decision.facts_used['severity']} on {account['account_name']}, target "
                    f"{decision.facts_used['target_minutes']} minutes "
                    f"({decision.facts_used['coverage']}). Overdue by {breach} minutes and no "
                    f"first response is recorded."
                ),
                evidence=(ticket["ticket_id"],),
                account_id=ticket["account_id"],
                suggested_action="Respond now and escalate; Policy v3 section 4 requires the "
                "breach to be stated rather than hidden.",
                decision=decision,
            )
        )
    return signals


def _security_incidents(all_tickets) -> list[Signal]:
    """Reuses the severity rule rather than matching keywords a second time, so
    a signal and an SLA target can never disagree about what a ticket is."""
    signals = []
    for ticket in _open(all_tickets):
        verdict = severity.classify(ticket["subject"], ticket["description"])
        if verdict.rule is None or verdict.rule.id != "severity.p1.credential_exposure":
            continue
        signals.append(
            Signal(
                id=f"security_incident:{ticket['ticket_id']}",
                detector="security_incident",
                rank="critical",
                title=f"{ticket['ticket_id']} reports a suspected credential exposure",
                detail=(
                    f"{ticket['subject']}. Policy v3 classifies suspected credential exposure "
                    f"as P1 regardless of how it was reported."
                ),
                evidence=(ticket["ticket_id"],),
                account_id=ticket["account_id"],
                suggested_action="Treat as a live security incident: revoke and rotate the "
                "exposed key, then confirm with the customer.",
            )
        )
    return signals


def _silent_credits(all_orders, all_tickets, as_of) -> list[Signal]:
    """The strongest argument for this whole module.

    A credit that is owed, that nobody has claimed, and that no reactive system
    will ever surface because no question was asked about it.
    """
    signals = []
    for order in all_orders:
        late = service_credit.hours_past_window_end(order, as_of)
        if late is None:
            continue
        decision = service_credit.evaluate(
            account_id=order["account_id"],
            hours_past_window_end=late,
            carrier_fault=order["carrier_fault"],
            customer_fault=order["customer_fault"],
            shipment_fee_inr=order["shipment_fee_inr"],
            as_of=as_of,
            order_id=order["order_id"],
        )
        if decision.outcome != "eligible":
            continue
        if _referenced_by_a_ticket(order["order_id"], all_tickets):
            continue

        signals.append(
            Signal(
                id=f"silent_credit_eligible:{order['order_id']}",
                detector="silent_credit_eligible",
                rank="high",
                title=f"{order['order_id']} is owed a service credit that nobody has claimed",
                detail=(
                    f"Pickup is {late:.1f} hours past the scheduled window, the carrier has "
                    f"accepted fault and the customer is not at fault. "
                    f"{decision.summary} No ticket has been raised about this order."
                ),
                evidence=(order["order_id"],),
                account_id=order["account_id"],
                suggested_action=(
                    f"Propose a service credit of INR {decision.amount_inr:g} for approval, "
                    f"and contact the customer before they notice."
                ),
                decision=decision,
            )
        )
    return signals


def _referenced_by_a_ticket(order_id: str, all_tickets: list[dict]) -> bool:
    return any(
        order_id in f"{t.get('subject', '')} {t.get('description', '')}" for t in all_tickets
    )


def _known_issue_clusters(all_tickets, all_accounts) -> list[Signal]:
    """Two tickets on the same defect is a recurrence, not a coincidence."""
    book = load()
    by_issue: dict[str, list[dict]] = {}

    for ticket in all_tickets:
        account = all_accounts.get(ticket["account_id"])
        if account is None:
            continue
        facts = _issue_facts(ticket, account)
        for issue in known_issues.match(facts, book=book):
            by_issue.setdefault(issue.id, []).append(ticket)

    signals = []
    for issue_id, matched in sorted(by_issue.items()):
        if len(matched) < 2:
            continue
        issue = book.known_issue(issue_id)
        ids = tuple(t["ticket_id"] for t in sorted(matched, key=lambda t: t["ticket_id"]))
        accounts_hit = {t["account_id"] for t in matched}
        signals.append(
            Signal(
                id=f"known_issue_cluster:{issue_id}",
                detector="known_issue_cluster",
                rank="high",
                title=f"{len(ids)} tickets match {issue_id} ({issue.title})",
                detail=(
                    f"{', '.join(ids)} across {len(accounts_hit)} account(s). "
                    f"Status {issue.issue_status}. {issue.caveat}"
                ),
                evidence=ids,
                suggested_action="Link both tickets to the known issue and send the documented "
                "workaround rather than diagnosing each one separately.",
            )
        )
    return signals


def _issue_facts(ticket: dict, account: dict) -> dict[str, Any]:
    text = f"{ticket.get('subject', '')} {ticket.get('description', '')}".lower()
    facts: dict[str, Any] = {"plan": account["plan"]}
    if "bulk upload" in text or "csv" in text:
        facts["topic"] = "bulk_upload"
        rows = _ROW_COUNT.search(text)
        if rows:
            facts["rows"] = int(rows.group(1).replace(",", ""))
    return facts


def _stalled_cancellations(all_orders) -> list[Signal]:
    stalled = [
        o for o in all_orders if o.get("cancellation_requested_at") and o["status"] == "BOOKED"
    ]
    if not stalled:
        return []
    ids = tuple(o["order_id"] for o in stalled)
    return [
        Signal(
            id="stalled_cancellation",
            detector="stalled_cancellation",
            rank="medium",
            title=f"{len(ids)} cancellation requests are still showing BOOKED",
            detail=(
                f"{', '.join(ids)} each have a cancellation request recorded and remain in "
                f"BOOKED status, so the bookings have not been released."
            ),
            evidence=ids,
            suggested_action="Work through the backlog: confirm each cancellation, apply the "
            "fee the rules give, and release the booking.",
        )
    ]


def _carrier_concentration(all_orders) -> list[Signal]:
    """Deliberately framed as a lead, not a detection.

    Six orders is nowhere near enough to call a concentration statistically
    unusual, and claiming otherwise is the kind of thing that does not survive
    a reviewer pushing on it. What it is worth saying is how many records a
    carrier appears in, and letting a human decide whether that means anything.
    """
    if not all_orders:
        return []
    counts = Counter(o["carrier"] for o in all_orders)
    carrier, appearances = counts.most_common(1)[0]
    if appearances < 2:
        return []
    involved = tuple(o["order_id"] for o in all_orders if o["carrier"] == carrier)
    return [
        Signal(
            id=f"carrier_concentration:{carrier}",
            detector="carrier_concentration",
            rank="medium",
            title=f"{carrier} appears in {appearances} of {len(all_orders)} orders",
            detail=(
                f"{carrier} carries {', '.join(involved)}. This is a count worth checking, not "
                f"a statistical anomaly -- at this volume it could easily be routine. Worth a "
                f"look if complaints cluster on the same carrier."
            ),
            evidence=involved,
            suggested_action="Check whether any open issues share this carrier before treating "
            "them as unrelated.",
        )
    ]


def _contradicted_guidance(all_tickets, as_of) -> list[Signal]:
    """Past answers that the current rules say were wrong.

    Worth surfacing on its own, separately from the chat path: a human reading
    the same ticket history will repeat the same error, and nobody is going to
    re-litigate a closed ticket unprompted.
    """
    book = load()
    signals = []

    for ticket in all_tickets:
        recorded = ticket.get("historical_resolution")
        if not recorded:
            continue

        decision_facts = _current_cancellation_facts(ticket["account_id"], as_of)
        for check in book.contradiction_checks:
            if not check.flags(recorded, decision_facts):
                continue
            signals.append(
                Signal(
                    id=f"contradicted_guidance:{ticket['ticket_id']}",
                    detector="contradicted_guidance",
                    rank="low",
                    title=f"{ticket['ticket_id']} was closed with guidance the current rules "
                    f"contradict",
                    detail=f'Recorded resolution: "{recorded}" {check.why_wrong}',
                    evidence=(ticket["ticket_id"],),
                    account_id=ticket["account_id"],
                    suggested_action="Re-contact the customer with the correct position, and "
                    "check whether anything was charged in error.",
                )
            )
            break
    return signals


def _current_cancellation_facts(account_id: str, as_of: datetime) -> dict[str, Any]:
    """What the rules say *today* about a late cancellation on this account.

    The contradiction check compares recorded guidance against a current
    outcome, so the outcome has to be computed rather than assumed.
    """
    resolution = engine.resolve(
        cancellation.DOMAIN,
        {"order_status": "BOOKED", "minutes_since_booking": 10_000},
        account_id=account_id,
        as_of=as_of,
    )
    if resolution.winner is None:
        return {}
    return {"fee_inr": resolution.winner.then.get("fee_inr")}
