"""HTTP surface.

The principal arrives as a header and is resolved server-side. There is no
request shape that lets a caller name someone else's account and be believed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def as_persona(client, persona):
    client.headers.update({"X-Principal-Id": persona})
    return client


def test_health_reports_the_pinned_snapshot(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["snapshot_at"] == "2026-08-16T11:00:00+05:30"


def test_personas_cover_both_user_contexts(client):
    body = client.get("/api/session/personas").json()
    kinds = {p["kind"] for p in body["personas"]}
    assert kinds == {"customer", "internal"}
    assert len(body["personas"]) == 6


def test_an_unknown_persona_is_rejected(client):
    response = client.get("/api/session/me", headers={"X-Principal-Id": "admin"})
    assert response.status_code == 401


def test_signals_are_internal_only(client):
    denied = as_persona(client, "cust-northstar").get("/api/signals")
    assert denied.status_code == 403

    allowed = as_persona(client, "staff-rohit").get("/api/signals")
    assert allowed.status_code == 200
    assert len(allowed.json()["signals"]) == 9


def test_the_silent_credit_reaches_the_board(client):
    body = as_persona(client, "staff-rohit").get("/api/signals").json()
    silent = next(s for s in body["signals"] if s["detector"] == "silent_credit_eligible")

    assert silent["evidence"] == ["ORD-2002"]
    assert silent["decision"]["amount_inr"] == 300
    assert silent["decision"]["winning_rule_id"] == "credit.lumenworks.fixed"


def test_confirming_an_action_from_the_wrong_account_is_forbidden(client):
    from app.auth import principals
    from app.repo import actions

    proposed = actions.propose(
        principals.get("staff-rohit"),
        account_id="ACCT-002",
        action_type="issue_service_credit",
        payload={"amount_inr": 300},
        target_id="ORD-2002",
    )

    denied = as_persona(client, "cust-northstar").post(
        f"/api/actions/{proposed.action_id}/confirm"
    )
    assert denied.status_code == 403

    allowed = as_persona(client, "staff-rohit").post(
        f"/api/actions/{proposed.action_id}/confirm"
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "EXECUTED"


def test_a_credit_needing_approval_is_refused_to_an_agent(client):
    from app.auth import principals
    from app.repo import actions

    proposed = actions.propose(
        principals.get("staff-rohit"),
        account_id="ACCT-002",
        action_type="issue_service_credit",
        payload={"amount_inr": 1500},
        target_id="ORD-2002",
    )
    assert proposed.status == "NEEDS_APPROVAL"

    refused = as_persona(client, "staff-rohit").post(f"/api/actions/{proposed.action_id}/confirm")
    assert refused.status_code == 403

    approved = as_persona(client, "staff-priya").post(f"/api/actions/{proposed.action_id}/confirm")
    assert approved.status_code == 200


def test_an_unknown_action_is_a_404(client):
    response = as_persona(client, "staff-rohit").get("/api/actions/deadbeef")
    assert response.status_code == 404
