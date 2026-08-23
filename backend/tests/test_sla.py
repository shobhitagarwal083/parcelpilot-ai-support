"""First-response SLA -- traps T5, T6, T7 and T12.

Two failure modes are asymmetric here, and the asymmetry is why the due column
is load-bearing. A business-hours-everywhere build fails loudly: it reports both
real breaches as not-started. A wall-clock-everywhere build passes every breach
assertion and fails only on due timestamps and the `not_started` outcome -- so
an outcome-only test suite ships the Sunday bug green.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import config
from app.policy import sla
from tests import records

OPEN_TICKETS = ("TKT-501", "TKT-502", "TKT-503", "TKT-504", "TKT-505")


def decide(ticket_id: str, now, **kwargs):
    ticket = records.ticket(ticket_id)
    return sla.evaluate(
        ticket=ticket, account=records.account(ticket["account_id"]), as_of=now, **kwargs
    )


def test_l1_the_contract_sla_is_tighter_than_the_plan_default(now):
    """30 minutes elapsed on a P1 sitting exactly on the Enterprise default
    boundary is not an accident. Northstar negotiated 15."""
    decision = decide("TKT-501", now)

    assert decision.facts_used["severity"] == "P1"
    assert decision.facts_used["target_minutes"] == 15
    assert decision.facts_used["coverage"] == "24x7"
    assert decision.facts_used["due_at"] == "2026-08-16T10:45:00+05:30"
    assert decision.outcome == "breached"
    assert decision.facts_used["breach_minutes"] == 15


def test_l2_an_account_with_no_agreement_falls_back_to_policy(now):
    decision = decide("TKT-505", now)

    assert decision.facts_used["severity"] == "P1"
    assert decision.facts_used["target_minutes"] == 30
    assert decision.facts_used["due_at"] == "2026-08-16T09:00:00+05:30"
    assert decision.outcome == "breached"
    assert decision.facts_used["breach_minutes"] == 120


@pytest.mark.parametrize(
    ("ticket_id", "severity", "target", "due"),
    [
        ("TKT-502", "P2", 240, "2026-08-17T13:00:00+05:30"),  # L3
        ("TKT-504", "P3", 480, "2026-08-17T17:00:00+05:30"),  # L4
        ("TKT-503", "P3", 1080, "2026-08-18T18:00:00+05:30"),  # L5
    ],
)
def test_l3_l5_business_coverage_clocks_have_not_started_on_a_sunday(
    ticket_id, severity, target, due, now
):
    """The due timestamp is asserted, not just the outcome.

    A wall-clock build returns "not breached" for all three as well -- elapsed
    times of 10-150 minutes against targets of 240-1080 cannot breach. It gets
    the due times wrong by roughly 22 hours, which is the only thing that
    separates it from a correct implementation at this snapshot.
    """
    decision = decide(ticket_id, now)

    assert decision.facts_used["severity"] == severity
    assert decision.facts_used["target_minutes"] == target
    assert decision.facts_used["coverage"] == "business"
    assert decision.facts_used["due_at"] == due
    assert decision.outcome == "not_started"
    assert decision.facts_used["elapsed_minutes"] == 0


def test_l6_the_displaced_policy_target_is_recorded(now):
    decision = decide("TKT-501", now)

    assert decision.winning_rule_id == "sla.northstar.p1"
    displaced = {o.overridden_rule_id for o in decision.overrides}
    assert "sla.v3.enterprise.p1" in displaced


def test_l7_axis_labs_is_enterprise_with_no_agreement(now):
    """The control case. Enterprise plan, no contract -- so the system has to
    fall back to policy rather than inventing contract terms."""
    decision = decide("TKT-505", now)

    assert decision.winning_rule_id == "sla.v3.enterprise.p1"
    assert all(c.authority_tier != 1 for c in decision.citations)


def test_l8_a_breach_always_requires_a_human(now):
    for ticket_id in OPEN_TICKETS:
        decision = decide(ticket_id, now)
        if decision.outcome == "breached":
            assert decision.requires_human
            assert "breach" in (decision.human_reason or "").lower()


def test_l9_no_decision_anywhere_cites_the_deprecated_policy(now):
    """Policy v2 contains a complete, plausible SLA table where Enterprise P1 is
    one hour. Using it makes TKT-505 look comfortably within target when it is
    breached by two hours."""
    for ticket_id in OPEN_TICKETS:
        decision = decide(ticket_id, now)
        assert all(c.doc_id != "02_Support_Policy_v2_DEPRECATED" for c in decision.citations)


def test_l10_even_a_lumenworks_p1_waits_for_monday(now):
    """Their agreement disclaims weekend and after-hours coverage. That is
    contractually correct and worth saying out loud."""
    raised = datetime(2026, 8, 16, 9, 45, tzinfo=config.TZ)
    ticket = records.ticket("TKT-502") | {"created_at": raised}
    decision = sla.evaluate(
        ticket=ticket,
        account=records.account("ACCT-002"),
        as_of=now,
        severity_override="P1",
    )

    assert decision.facts_used["due_at"] == "2026-08-17T11:00:00+05:30"
    assert decision.outcome == "not_started"


def test_l11_within_target_is_reachable_on_a_weekday(now):
    """No ticket in this pack is `within_target`, so this exists purely to keep
    that branch covered."""
    raised = datetime(2026, 8, 17, 10, 0, tzinfo=config.TZ)
    ticket = records.ticket("TKT-504") | {"created_at": raised}
    decision = sla.evaluate(
        ticket=ticket,
        account=records.account("ACCT-001"),
        as_of=datetime(2026, 8, 17, 12, 0, tzinfo=config.TZ),
        severity_override="P3",
    )

    assert decision.facts_used["due_at"] == "2026-08-17T18:00:00+05:30"
    assert decision.outcome == "within_target"


def test_l12_no_ticket_in_the_pack_is_within_target(now):
    outcomes = {decide(t, now).outcome for t in OPEN_TICKETS}
    assert outcomes == {"breached", "not_started"}


def test_l13_the_clock_starts_at_created_at_not_the_last_customer_message(now):
    """The trap that quietly rescues the most urgent ticket in the dataset.

    TKT-501 last heard from the customer at 10:52; the snapshot is 11:00. Read
    that field and the elapsed time is 8 minutes, comfortably inside the
    15-minute target. Read created_at and it is 30 minutes -- breached by 15.
    A further customer message is evidence the response has not happened.
    """
    decision = decide("TKT-501", now)

    assert decision.facts_used["elapsed_minutes"] == 30
    assert decision.facts_used["created_at"] == "2026-08-16T10:30:00+05:30"
    assert decision.facts_used["clock_starts_at"] == "created_at"


def test_l14_no_sla_decision_reads_the_customer_message_timestamp(now):
    for ticket_id in OPEN_TICKETS:
        facts = decide(ticket_id, now).facts_used
        assert "last_customer_message_at" not in facts
        assert "created_at" in facts


def test_l13_the_workbook_has_no_first_response_column_at_all(now):
    """The reason A12 holds: nothing in the pack records a ParcelPilot reply, so
    an un-started clock is the only consistent reading."""
    from app.ingest.structured import EXPECTED_COLUMNS

    assert not any("first_response" in c for c in EXPECTED_COLUMNS["tickets"])
    assert "last_customer_message_at" in EXPECTED_COLUMNS["tickets"]


def test_unclassifiable_severity_asks_instead_of_guessing(now):
    ticket = records.ticket("TKT-503") | {"subject": "zzz", "description": "zzz"}
    decision = sla.evaluate(ticket=ticket, account=records.account("ACCT-003"), as_of=now)

    assert decision.outcome == "indeterminate"
    assert decision.unknowns == ["severity"]
    assert decision.requires_human


@pytest.mark.parametrize(
    ("ticket_id", "severity"),
    [("TKT-501", "P1"), ("TKT-502", "P2"), ("TKT-503", "P3"), ("TKT-504", "P3"), ("TKT-505", "P1")],
)
def test_severity_classification_matches_policy_v3(ticket_id, severity, now):
    assert decide(ticket_id, now).facts_used["severity"] == severity
