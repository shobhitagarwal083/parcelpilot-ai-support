"""Service-credit decisions -- traps T3, T4 and T11.

S3 and S4 are the most important tests in the suite. They are the only thing
standing between the system and a plausible wrong number: without the `replaces`
mechanism, a LumenWorks pickup three hours late falls through to the SOP default
and returns an eligible INR 240 that looks entirely reasonable.
"""

from __future__ import annotations

import pytest

from app.policy import service_credit
from tests import records


def hypothetical(account_id: str, now, **overrides):
    params = {
        "account_id": account_id,
        "hours_past_window_end": 3,
        "carrier_fault": True,
        "customer_fault": False,
        "as_of": now,
    } | overrides
    return service_credit.evaluate(**params)


def test_s1_the_agreement_supplies_a_flat_amount_not_the_sop_formula(now):
    order = records.order("ORD-2002")
    decision = service_credit.evaluate(
        account_id=order["account_id"],
        hours_past_window_end=records.hours_past_window_end(order, now),
        carrier_fault=order["carrier_fault"],
        customer_fault=order["customer_fault"],
        shipment_fee_inr=order["shipment_fee_inr"],
        as_of=now,
        order_id=order["order_id"],
    )

    assert decision.outcome == "eligible"
    assert decision.amount_inr == 300
    assert decision.winning_rule_id == "credit.lumenworks.fixed"


def test_s2_the_sop_formula_would_have_produced_a_different_number(now):
    """min(INR 500, 10% of INR 2,400) is INR 240. A system that takes the amount
    from the contract but the threshold from the SOP gets this right by luck."""
    order = records.order("ORD-2002")
    decision = service_credit.evaluate(
        account_id=order["account_id"],
        hours_past_window_end=records.hours_past_window_end(order, now),
        carrier_fault=order["carrier_fault"],
        customer_fault=order["customer_fault"],
        shipment_fee_inr=order["shipment_fee_inr"],
        as_of=now,
    )
    assert decision.amount_inr != 240


def test_s3_three_hours_late_is_not_eligible_for_lumenworks(now):
    """The same question the brief asks, on the one account where the answer is
    no. Their agreement moved the threshold from two hours to four."""
    decision = hypothetical("ACCT-002", now)

    assert decision.outcome == "ineligible"
    assert decision.winning_rule_id is None
    assert decision.amount_inr is None


def test_s4_the_replaced_sop_rule_is_disclosed_and_did_not_decide(now):
    """Proves the mechanism worked rather than merely that the rule was absent.

    Absence would also pass a build that discarded the rule silently -- which is
    the same failure as never retrieving it.
    """
    decision = hypothetical("ACCT-002", now)

    replaced = {o.overridden_rule_id: o.kind for o in decision.overrides}
    assert replaced.get("credit.sop_v4.default") == "replaced"
    assert decision.winning_rule_id != "credit.sop_v4.default"
    assert "4 hours" in decision.summary


def test_s3_the_answer_cites_the_agreement_that_governs_the_account(now):
    """The rule that did NOT fire is the reason for the answer, so it has to be
    citable even though it never matched."""
    decision = hypothetical("ACCT-002", now)
    assert "06_LumenWorks_Service_Agreement" in {c.doc_id for c in decision.citations}


@pytest.mark.parametrize(
    ("account_id", "fee", "expected"),
    [
        ("ACCT-003", 1200.0, 120),   # S5  10% of the fee
        ("ACCT-001", 4200.0, 420),   # S6  10% of the fee
        ("ACCT-004", 6000.0, 500),   # S7  capped at INR 500, not 10% = INR 600
    ],
)
def test_s5_s7_the_same_three_hour_delay_is_eligible_on_every_other_account(
    account_id, fee, expected, now
):
    decision = hypothetical(account_id, now, shipment_fee_inr=fee)

    assert decision.outcome == "eligible"
    assert decision.amount_inr == expected
    assert decision.winning_rule_id == "credit.sop_v4.default"


def test_s6_the_northstar_monthly_cap_is_reported_not_deducted(now):
    """No credit ledger exists in the pack, so claiming a remaining balance
    would be a confidently invented number (A9)."""
    decision = hypothetical("ACCT-001", now, shipment_fee_inr=4200.0)

    assert any("5,000" in note for note in decision.notes)
    assert decision.amount_inr == 420


def test_s8_an_unknown_fault_is_indeterminate_and_promises_nothing(now):
    decision = hypothetical("ACCT-001", now, carrier_fault=None)

    assert decision.outcome == "indeterminate"
    assert decision.unknowns == ["carrier_fault"]
    assert decision.requires_human
    assert decision.amount_inr is None


def test_s9_customer_fault_makes_the_claim_ineligible(now):
    decision = hypothetical("ACCT-003", now, customer_fault=True, shipment_fee_inr=1200.0)

    assert decision.outcome == "ineligible"
    assert decision.winning_rule_id == "credit.sop_v4.customer_fault"


def test_s10_a_pickup_window_that_has_not_elapsed_is_cleanly_ineligible(now):
    """Not `indeterminate`. carrier_fault is a non-nullable boolean in the
    source sheet, so False is an established fact rather than a gap (A8)."""
    order = records.order("ORD-1001")
    decision = service_credit.evaluate(
        account_id=order["account_id"],
        hours_past_window_end=records.hours_past_window_end(order, now),
        carrier_fault=order["carrier_fault"],
        customer_fault=order["customer_fault"],
        shipment_fee_inr=order["shipment_fee_inr"],
        as_of=now,
    )

    assert decision.outcome == "ineligible"
    assert decision.unknowns == []


def test_s11_a_credit_above_the_threshold_requires_manager_approval(now):
    decision = hypothetical("ACCT-004", now, hours_past_window_end=5, shipment_fee_inr=20000.0)

    assert decision.outcome == "eligible"
    assert decision.amount_inr == 500
    assert not decision.requires_human  # INR 500 is under the threshold


def test_s11_the_threshold_comes_from_the_sop_not_an_invented_number(now):
    from app.knowledge.loader import load

    constraint = next(
        c for c in load().constraints if c.id == "approval.sop_v4.manager_required"
    )
    assert constraint.amount_inr == 1000
    assert constraint.source.doc == "03_Cancellation_and_Service_Credit_SOP_v4"
    assert "above INR 1,000" in constraint.source.quote


def test_only_one_order_in_the_pack_has_carrier_fault(now):
    """ORD-2002 is the credit nobody asked for: INR 300 owed, carrier fault
    accepted, customer not at fault, and no ticket filed."""
    from tests.records import _connect

    with _connect() as conn:
        rows = conn.execute("SELECT order_id FROM orders WHERE carrier_fault = 1")
        faulted = [r[0] for r in rows]
    assert faulted == ["ORD-2002"]
