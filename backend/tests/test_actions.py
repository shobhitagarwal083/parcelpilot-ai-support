"""The action gate -- requirement 4, in its strong form.

The requirement can be read weakly (instruct the model to ask first) or strongly
(make it impossible for the model to act). The weak reading relies on the model
behaving and fails under prompt injection or a confused loop. N2 is the test
that pins the strong reading: no code path from the agent package reaches
execution at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.auth import principals
from app.auth.scope import AccessDenied
from app.repo import actions, audit, tickets


@pytest.fixture
def rohit():
    return principals.get("staff-rohit")


@pytest.fixture
def priya():
    return principals.get("staff-priya")


@pytest.fixture
def northstar():
    return principals.get("cust-northstar")


def propose_credit(principal, amount, account_id="ACCT-002"):
    return actions.propose(
        principal,
        account_id=account_id,
        action_type="issue_service_credit",
        payload={"amount_inr": amount, "reason": "failed pickup"},
        target_id="ORD-2002",
    )


def test_n1_proposing_creates_a_pending_row_and_changes_nothing_else(rohit):
    before = tickets.get(rohit, "TKT-501")["status"]

    action = actions.propose(
        rohit,
        account_id="ACCT-001",
        action_type="update_ticket",
        payload={"status": "escalated"},
        target_id="TKT-501",
    )

    assert action.status == "PENDING"
    assert tickets.get(rohit, "TKT-501")["status"] == before


def test_n2_no_path_from_the_agent_package_reaches_execution():
    """Walks the agent package rather than trusting a comment.

    The model's only reachable verb is `propose`. If a future tool ever imports
    or calls confirm/reject/_apply, this fails.
    """
    from app import config

    agent_dir = config.BACKEND_DIR / "app" / "agent"
    # `actions.propose` is legitimate and expected -- it is the model's one verb.
    # Anything that transitions an action's status is not.
    forbidden = (
        "actions.confirm",
        "actions.reject",
        "actions._apply",
        "import confirm",
        "import reject",
    )

    offenders = []
    for path in sorted(agent_dir.rglob("*.py")):
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(config.BACKEND_DIR)}: {token}")

    assert not offenders, "the agent must not be able to execute an action:\n  " + "\n  ".join(
        offenders
    )


def test_n2_execution_lives_behind_the_api_not_a_tool():
    """The registry exposes propose_action and nothing that transitions status."""
    import inspect

    from app.repo import actions as store

    executors = {
        name
        for name, fn in vars(store).items()
        if inspect.isfunction(fn) and name in ("confirm", "reject", "_apply")
    }
    assert executors == {"confirm", "reject", "_apply"}


def test_n3_confirming_executes_and_writes_an_audit_row(rohit):
    action = actions.propose(
        rohit,
        account_id="ACCT-001",
        action_type="update_ticket",
        payload={"status": "escalated"},
        target_id="TKT-501",
    )
    confirmed = actions.confirm(rohit, action.action_id)

    assert confirmed.status == "EXECUTED"
    assert confirmed.resolved_by == "staff-rohit"
    assert tickets.get(rohit, "TKT-501")["status"] == "escalated"

    executed = [e for e in audit.entries() if e["outcome"] == "executed"]
    assert len(executed) == 1
    assert executed[0]["resource"] == action.action_id


def test_n4_capability_is_rechecked_at_confirmation_not_only_at_proposal(rohit, priya):
    """The session that confirms is not necessarily the one that proposed. An
    approval requirement enforced only at proposal is not an approval
    requirement at all."""
    action = propose_credit(rohit, 1500)
    assert action.status == "NEEDS_APPROVAL"

    with pytest.raises(AccessDenied, match="approve:credit"):
        actions.confirm(rohit, action.action_id)

    assert actions.get(rohit, action.action_id).status == "NEEDS_APPROVAL"
    assert actions.confirm(priya, action.action_id).status == "EXECUTED"


def test_n5_the_threshold_comes_from_the_sop(rohit):
    assert actions.approval_threshold_inr() == Decimal("1000")

    assert propose_credit(rohit, 1000).status == "PENDING"  # "above INR 1,000"
    assert propose_credit(rohit, 1000.01).status == "NEEDS_APPROVAL"
    assert propose_credit(rohit, 300).status == "PENDING"


def test_n5_the_ord_2002_credit_needs_no_approval(rohit):
    """INR 300 sits under the threshold, so the demo path is a single confirm."""
    action = propose_credit(rohit, 300)
    assert action.status == "PENDING"
    assert actions.confirm(rohit, action.action_id).status == "EXECUTED"


def test_n6_rejecting_closes_without_side_effects(rohit):
    before = tickets.get(rohit, "TKT-501")["status"]
    action = actions.propose(
        rohit,
        account_id="ACCT-001",
        action_type="update_ticket",
        payload={"status": "escalated"},
        target_id="TKT-501",
    )

    rejected = actions.reject(rohit, action.action_id, reason="not warranted")

    assert rejected.status == "REJECTED"
    assert tickets.get(rohit, "TKT-501")["status"] == before

    with pytest.raises(actions.ActionError, match="rejected"):
        actions.confirm(rohit, action.action_id)


def test_n7_confirming_twice_is_a_no_op(rohit):
    action = actions.propose(
        rohit,
        account_id="ACCT-001",
        action_type="update_ticket",
        payload={"status": "escalated"},
        target_id="TKT-501",
    )
    first = actions.confirm(rohit, action.action_id)
    second = actions.confirm(rohit, action.action_id)

    assert first.status == second.status == "EXECUTED"
    assert first.resolved_at == second.resolved_at
    assert len([e for e in audit.entries() if e["outcome"] == "executed"]) == 1
    assert len([e for e in audit.entries() if e["outcome"] == "ignored"]) == 1


def test_an_action_cannot_be_proposed_outside_the_sessions_scope(northstar):
    with pytest.raises(AccessDenied):
        propose_credit(northstar, 300, account_id="ACCT-002")


def test_an_action_cannot_be_confirmed_from_another_account(rohit):
    action = propose_credit(rohit, 300, account_id="ACCT-002")
    lumenworks = principals.get("cust-lumenworks")
    beacon = principals.get("cust-beacon")

    assert actions.get(lumenworks, action.action_id).action_id == action.action_id
    with pytest.raises(AccessDenied):
        actions.confirm(beacon, action.action_id)


def test_a_customer_sees_only_their_own_pending_actions(rohit):
    propose_credit(rohit, 300, account_id="ACCT-002")
    actions.propose(
        rohit,
        account_id="ACCT-001",
        action_type="create_escalation",
        payload={"reason": "P1 breach"},
        target_id="TKT-501",
    )

    northstar = principals.get("cust-northstar")
    assert [a.account_id for a in actions.list_for(northstar)] == ["ACCT-001"]
    assert len(actions.list_for(rohit)) == 2


def test_an_unknown_action_type_is_rejected(rohit):
    with pytest.raises(actions.ActionError, match="unknown action type"):
        actions.propose(rohit, account_id="ACCT-001", action_type="delete_everything", payload={})


def test_the_proposal_carries_the_decision_that_justified_it(rohit, now):
    """A confirmation card without the reasoning behind it is a button that
    somebody clicks without knowing what they are agreeing to."""
    from app.policy import service_credit
    from tests import records

    order = records.order("ORD-2002")
    decision = service_credit.evaluate(
        account_id=order["account_id"],
        hours_past_window_end=service_credit.hours_past_window_end(order, now),
        carrier_fault=order["carrier_fault"],
        customer_fault=order["customer_fault"],
        shipment_fee_inr=order["shipment_fee_inr"],
        as_of=now,
    )
    action = actions.propose(
        rohit,
        account_id="ACCT-002",
        action_type="issue_service_credit",
        payload={"amount_inr": 300},
        target_id="ORD-2002",
        decision=decision,
    )

    stored = actions.get(rohit, action.action_id).decision
    assert stored["amount_inr"] == 300
    assert stored["winning_rule_id"] == "credit.lumenworks.fixed"
    assert stored["citations"]
