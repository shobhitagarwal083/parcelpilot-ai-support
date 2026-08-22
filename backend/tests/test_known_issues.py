"""Known-issue matching -- traps T8, T9 and T10.

These never change an outcome. They change what a truthful answer has to
mention, and in T9's case they invert what the obvious answer would have been.
"""

from __future__ import annotations

from app.policy import known_issues, product
from tests import records


def test_i1_a_4200_row_upload_matches_the_known_defect():
    matched = known_issues.match({"topic": "bulk_upload", "rows": 4200, "plan": "Growth"})
    assert [k.id for k in matched] == ["KI-208"]


def test_i2_the_supported_limit_is_5000_not_the_bug_threshold():
    """TKT-451 told the same customer their plan supports 3,000 rows. Roughly
    3,000 is where the defect starts; the product limit is 5,000. A retriever
    finds the ticket first because it is the same customer with the same symptom
    five days earlier."""
    fact = product.capability("bulk_upload", {"plan": "Growth"})

    assert fact.id == "product.bulk_upload.available"
    assert fact.then["max_rows"] == 5000
    assert fact.authority_tier == 3

    caveat = known_issues.caveats_for({"topic": "bulk_upload", "rows": 4200, "plan": "Growth"})[0]
    assert "5,000" in caveat.text
    assert "not a plan limit" in caveat.text


def test_i2_the_wrong_historical_guidance_is_flagged_as_contradicted():
    from app.policy import engine

    history = records.resolution_history("ACCT-002")
    found = engine.contradictions_for("product", {}, history, topic="bulk_upload")

    assert [c.ticket_id for c in found] == ["TKT-451"]
    assert "5,000 rows" in found[0].why_wrong


def test_i3_a_swiftship_ticket_matches_the_webhook_delay():
    matched = known_issues.match({"carrier": "SwiftShip", "order_status": "BOOKED"})
    assert [k.id for k in matched] == ["KI-211"]


def test_i4_the_same_caveat_attaches_to_the_order_behind_it(now):
    """ORD-1001 is SwiftShip and BOOKED too, so the cancellation answer carries
    it. Cancelling a parcel that has physically been collected is a worse
    mistake than charging the wrong fee."""
    order = records.order("ORD-1001")
    matched = known_issues.match({"carrier": order["carrier"], "order_status": order["status"]})
    assert [k.id for k in matched] == ["KI-211"]


def test_i5_a_different_carrier_does_not_match():
    """Asserting a SwiftShip-specific defect about a RoadRunner order would be a
    different error, not a safer one."""
    order = records.order("ORD-2002")
    matched = known_issues.match({"carrier": order["carrier"], "order_status": order["status"]})
    assert matched == []


def test_i5_a_delivered_swiftship_order_does_not_match():
    order = records.order("ORD-4001")
    matched = known_issues.match({"carrier": order["carrier"], "order_status": order["status"]})
    assert matched == []


def test_i6_the_resolved_issue_never_matches_anything():
    """The guide warns against using it to explain new incidents. A plausible
    stale explanation is worse than no explanation."""
    from app.knowledge.loader import load

    ki176 = load().known_issue("KI-176")
    assert ki176 is not None
    assert ki176.excluded_from_matching

    probes = [
        {"topic": "bulk_upload", "rows": 9999, "plan": "Enterprise"},
        {"carrier": "SwiftShip", "order_status": "BOOKED"},
        {"topic": "address_validation"},
        {},
    ]
    for facts in probes:
        assert all(k.id != "KI-176" for k in known_issues.match(facts))


def test_i7_bulk_upload_is_not_included_on_standard():
    fact = product.capability("bulk_upload", {"plan": "Standard"})

    assert fact.id == "product.bulk_upload.unavailable"
    assert fact.then["available"] is False
    assert known_issues.match({"topic": "bulk_upload", "rows": 4200, "plan": "Standard"}) == []


def test_caveats_have_a_customer_register_without_the_tracker_id():
    """A customer has no use for an internal issue ID and its investigation
    state; the behaviour is what affects them."""
    caveat = known_issues.caveats_for({"carrier": "SwiftShip", "order_status": "BOOKED"})[0]

    assert "KI-211" in caveat.text
    assert "KI-211" not in caveat.customer_safe_text
    assert "20 minutes" in caveat.customer_safe_text


def test_every_known_issue_is_sourced_to_the_product_guide():
    from app.knowledge.loader import load

    for issue in load().known_issues:
        assert issue.source.doc == "04_Product_Operations_Guide_and_Known_Issues"
        assert issue.authority_tier == 3
