"""Cancellation decisions -- traps T1, T2 and T9.

C1 is the brief's own headline example: "Can Northstar cancel ORD-1001 without a
cancellation fee?" The naive answer is INR 250, because the SOP is longer, more
specific about fees, and retrieves strongly on "cancellation fee". The correct
answer is no fee, from one sentence in a different document.
"""

from __future__ import annotations

import pytest

from app.policy import cancellation
from tests import records


def decide(order_id: str, now):
    order = records.order(order_id)
    return cancellation.evaluate(
        order=order, as_of=now, history=records.resolution_history(order["account_id"])
    )


def test_c1_northstar_agreement_waives_the_fee_two_hours_after_booking(now):
    decision = decide("ORD-1001", now)

    assert decision.outcome == "allowed"
    assert decision.amount_inr == 0
    assert decision.winning_rule_id == "cancel.northstar.waiver"
    # 120 minutes past the SOP's 30-minute grace window, and it makes no
    # difference: the clause waives the fee "regardless of how long ago".
    assert decision.facts_used["minutes_since_booking"] == 120
    assert decision.citations[0].authority_tier == 1

    displaced = {o.overridden_rule_id: o.kind for o in decision.overrides}
    assert displaced["cancel.sop_v4.booked_after_grace"] == "replaced"


def test_c1_the_superseded_sop_rule_is_still_cited(now):
    """Disclosed, not dropped. An answer that hides what it set aside cannot be
    checked by the person reading it."""
    decision = decide("ORD-1001", now)
    cited = {c.doc_id for c in decision.citations}
    assert "03_Cancellation_and_Service_Credit_SOP_v4" in cited
    assert "05_Northstar_Logistics_Enterprise_Agreement" in cited


def test_c2_lumenworks_gets_no_waiver_and_pays_the_sop_fee(now):
    decision = decide("ORD-2001", now)

    assert decision.outcome == "allowed"
    assert decision.amount_inr == 250
    assert decision.winning_rule_id == "cancel.sop_v4.booked_after_grace"
    assert decision.facts_used["minutes_since_booking"] == 75
    assert all(c.authority_tier != 1 for c in decision.citations)


def test_c3_beacon_pays_nothing_because_of_the_grace_window_not_a_waiver(now):
    """Same INR 0 as C1, arrived at for an entirely different reason. A system
    that cannot tell them apart will get the next case wrong."""
    decision = decide("ORD-3001", now)

    assert decision.outcome == "allowed"
    assert decision.amount_inr == 0
    assert decision.winning_rule_id == "cancel.sop_v4.booked_within_grace"
    assert decision.facts_used["minutes_since_booking"] == 15


def test_c3_elapsed_time_is_measured_to_the_request_not_to_now(now):
    """ORD-3001 was booked 10:25 and cancellation requested 10:40. Measuring to
    the 11:00 snapshot instead gives 35 minutes and charges a fee never owed."""
    decision = decide("ORD-3001", now)
    assert decision.facts_used["measured_to"] == "cancellation request"
    assert decision.facts_used["minutes_since_booking"] == 15


def test_c4_a_picked_up_shipment_routes_to_return_to_origin(now):
    """Northstar's agreement defers to the standard process here, so it gets no
    rule of its own -- the SOP rule must survive the waiver's replaces list."""
    decision = decide("ORD-1002", now)

    assert decision.outcome == "denied"
    assert decision.winning_rule_id == "cancel.sop_v4.picked_up"
    assert decision.facts_used["next_step"] == "return_to_origin"
    assert "return-to-origin" in decision.summary


def test_c5_a_delivered_shipment_cannot_be_cancelled(now):
    decision = decide("ORD-4001", now)
    assert decision.outcome == "denied"
    assert decision.winning_rule_id == "cancel.sop_v4.delivered"


def test_c6_lumenworks_hypothetical_cancellation_now_costs_250(now):
    decision = decide("ORD-2002", now)
    assert decision.outcome == "allowed"
    assert decision.amount_inr == 250
    assert decision.facts_used["measured_to"] == "now"


def test_c7_the_poisoned_ticket_is_surfaced_as_a_past_error(now):
    """TKT-450: same customer, same question, closed with the opposite answer.

    A retriever finds it as confirming precedent and gains false confidence. The
    right behaviour is not to ignore it but to say a previous answer was wrong.
    """
    decision = decide("ORD-1001", now)

    assert [c.ticket_id for c in decision.contradicts] == ["TKT-450"]
    contradiction = decision.contradicts[0]
    assert "250" in contradiction.recorded_resolution
    assert "waives" in contradiction.why_wrong


def test_c7_a_poisoned_ticket_does_not_leak_into_an_unrelated_account(now):
    decision = decide("ORD-2001", now)
    assert decision.contradicts == []


def test_c8_a_swiftship_order_carries_the_webhook_caveat(now):
    """Cancelling a parcel that has physically been collected is a worse
    operational mistake than charging the wrong fee."""
    decision = decide("ORD-1001", now)
    assert [c.issue_id for c in decision.caveats] == ["KI-211"]
    assert "20 minutes" in decision.caveats[0].text


def test_c8_a_non_swiftship_order_does_not(now):
    decision = decide("ORD-2002", now)
    assert decision.caveats == []


def test_an_unknown_status_is_indeterminate_rather_than_guessed(now):
    order = records.order("ORD-1001") | {"status": "IN_TRANSIT"}
    decision = cancellation.evaluate(order=order, as_of=now)

    assert decision.outcome == "indeterminate"
    assert decision.requires_human
    assert decision.amount_inr is None


@pytest.mark.parametrize(
    "order_id", ["ORD-1001", "ORD-1002", "ORD-2001", "ORD-2002", "ORD-3001", "ORD-4001"]
)
def test_every_decision_cites_a_source(order_id, now):
    decision = decide(order_id, now)
    assert decision.citations
    assert all(c.doc_id and c.section for c in decision.citations)
