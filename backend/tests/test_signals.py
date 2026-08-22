"""Proactive detection -- the client's Problem 1, and trap T11.

G3 is the one that matters. Every reactive system answers what it is asked;
ORD-2002 is owed INR 300 that nobody has claimed, on an order with no ticket. A
system that only responds never finds it, and that single record is the whole
argument for building this.
"""

from __future__ import annotations

import pytest

from app.auth import principals
from app.auth.scope import AccessDenied
from app.ops import signals


@pytest.fixture
def feed(now):
    return signals.detect(principals.get("staff-rohit"), now)


def by_detector(feed, detector):
    return [s for s in feed if s.detector == detector]


def test_g1_both_breached_tickets_surface_as_critical(feed):
    breaches = by_detector(feed, "sla_breached")

    assert {s.evidence[0] for s in breaches} == {"TKT-501", "TKT-505"}
    assert all(s.rank == "critical" for s in breaches)
    assert all(s.decision.outcome == "breached" for s in breaches)


def test_g1_a_breach_signal_carries_the_target_it_missed(feed):
    northstar = next(s for s in feed if s.id == "sla_breached:TKT-501")

    assert northstar.decision.facts_used["target_minutes"] == 15
    assert northstar.decision.facts_used["breach_minutes"] == 15
    assert "15 minutes" in northstar.detail


def test_g2_the_credential_exposure_is_flagged_separately(feed):
    incidents = by_detector(feed, "security_incident")

    assert [s.evidence[0] for s in incidents] == ["TKT-505"]
    assert incidents[0].rank == "critical"


def test_g2_the_security_signal_reuses_the_severity_rule(feed):
    """Not a second round of keyword matching. A signal and an SLA target must
    never disagree about what a ticket is."""
    from app.policy import severity
    from tests import records

    ticket = records.ticket("TKT-505")
    verdict = severity.classify(ticket["subject"], ticket["description"])
    assert verdict.rule.id == "severity.p1.credential_exposure"
    assert verdict.level == "P1"


def test_g3_the_credit_nobody_asked_for(feed):
    """INR 300 owed, carrier fault accepted, customer not at fault, no ticket."""
    silent = by_detector(feed, "silent_credit_eligible")

    assert len(silent) == 1
    signal = silent[0]
    assert signal.evidence == ("ORD-2002",)
    assert signal.rank == "high"
    assert signal.decision.outcome == "eligible"
    assert signal.decision.amount_inr == 300
    assert signal.decision.winning_rule_id == "credit.lumenworks.fixed"
    assert "No ticket has been raised" in signal.detail


def test_g3_the_signal_agrees_with_what_the_chat_path_would_say(now):
    """Same engine, same rulebook, same number. If the triage board and the
    customer answer could disagree, neither would be trustworthy."""
    from app.policy import service_credit
    from tests import records

    order = records.order("ORD-2002")
    chat_answer = service_credit.evaluate(
        account_id=order["account_id"],
        hours_past_window_end=service_credit.hours_past_window_end(order, now),
        carrier_fault=order["carrier_fault"],
        customer_fault=order["customer_fault"],
        shipment_fee_inr=order["shipment_fee_inr"],
        as_of=now,
    )
    board = signals.detect(principals.get("staff-rohit"), now)
    signal = next(s for s in board if s.detector == "silent_credit_eligible")

    assert signal.decision.amount_inr == chat_answer.amount_inr
    assert signal.decision.winning_rule_id == chat_answer.winning_rule_id


def test_g3_an_order_with_no_carrier_fault_raises_nothing(feed):
    silent = by_detector(feed, "silent_credit_eligible")
    assert "ORD-1001" not in {s.evidence[0] for s in silent}


def test_g4_the_same_defect_five_days_apart_is_a_cluster(feed):
    clusters = by_detector(feed, "known_issue_cluster")

    assert len(clusters) == 1
    assert clusters[0].evidence == ("TKT-451", "TKT-502")
    assert "KI-208" in clusters[0].title
    assert clusters[0].rank == "high"


def test_g4_the_cluster_carries_the_documented_workaround(feed):
    cluster = by_detector(feed, "known_issue_cluster")[0]
    assert "below 3,000 rows" in cluster.detail
    assert "not a plan limit" in cluster.detail


def test_g5_three_cancellations_are_stalled(feed):
    stalled = by_detector(feed, "stalled_cancellation")

    assert len(stalled) == 1
    assert stalled[0].evidence == ("ORD-1001", "ORD-2001", "ORD-3001")
    assert stalled[0].rank == "medium"


def test_g6_carrier_concentration_is_worded_as_a_lead_not_a_detection(feed):
    """Six orders cannot support a claim of statistical anomaly, and a reviewer
    who pushes on one wins. Reporting the count is defensible; inferring a
    pattern from it is not."""
    concentration = by_detector(feed, "carrier_concentration")

    assert len(concentration) == 1
    signal = concentration[0]
    assert "SwiftShip appears in 3 of 6 orders" == signal.title
    assert "not a statistical anomaly" in signal.detail
    assert set(signal.evidence) == {"ORD-1001", "ORD-2001", "ORD-4001"}


def test_g7_both_poisoned_resolutions_are_flagged(feed):
    contradicted = by_detector(feed, "contradicted_guidance")

    assert {s.evidence[0] for s in contradicted} == {"TKT-450", "TKT-451"}
    assert all(s.rank == "low" for s in contradicted)


def test_g7_each_says_why_the_recorded_answer_was_wrong(feed):
    detail = {s.evidence[0]: s.detail for s in by_detector(feed, "contradicted_guidance")}

    assert "waives the cancellation fee" in detail["TKT-450"]
    assert "5,000 rows" in detail["TKT-451"]


def test_g7_contradiction_is_computed_against_the_current_rules(now):
    """TKT-450 is only wrong because Northstar's agreement waives the fee. The
    identical sentence on a LumenWorks ticket would be correct guidance."""
    from app.ops.signals import _current_cancellation_facts

    assert _current_cancellation_facts("ACCT-001", now) == {"fee_inr": 0}
    assert _current_cancellation_facts("ACCT-002", now) == {"fee_inr": 250}


def test_g8_the_feed_is_ranked_with_criticals_first(feed):
    ranks = [signals.RANK_ORDER[s.rank] for s in feed]
    assert ranks == sorted(ranks)
    assert feed[0].rank == "critical"


def test_g9_a_customer_cannot_run_detection_at_all(now):
    for persona in ("cust-northstar", "cust-lumenworks", "cust-beacon", "cust-axis"):
        with pytest.raises(AccessDenied, match="signals"):
            signals.detect(principals.get(persona), now)


def test_g9_the_denial_is_audited(now):
    from app.repo import audit

    with pytest.raises(AccessDenied):
        signals.detect(principals.get("cust-northstar"), now)

    assert audit.count(outcome="denied") == 1


def test_every_signal_carries_evidence_and_is_serialisable(feed):
    for signal in feed:
        assert signal.evidence
        payload = signal.to_dict()
        assert payload["rank"] in ("critical", "high", "medium", "low")
        assert payload["evidence"]
        assert payload["title"]


def test_the_feed_finds_nine_signals_across_seven_detectors(feed):
    assert len(feed) == 9
    assert len({s.detector for s in feed}) == 7
