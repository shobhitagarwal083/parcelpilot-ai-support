"""Access control -- requirement 2.

The brief is explicit: "access controls should be enforced in the data/tool
layer rather than relying only on model instructions." A prompt-only guard fails
this, so every test here runs with no model in the loop at all. What is being
tested is that the repository raises, not that an agent chose to decline.

A11 is the requirement-2 test: the model supplies an account ID it should not
have, and the intersection with the session's own scope denies it anyway.
"""

from __future__ import annotations

import pytest

from app.auth import principals
from app.auth.principals import APPROVE_CREDIT, READ_DEPRECATED, SIGNALS
from app.auth.scope import (
    AccessDenied,
    require_capability,
    resolve_subject_account,
)
from app.repo import accounts, audit, orders, tickets


@pytest.fixture
def lumenworks():
    return principals.get("cust-lumenworks")


@pytest.fixture
def northstar():
    return principals.get("cust-northstar")


@pytest.fixture
def rohit():
    return principals.get("staff-rohit")


@pytest.fixture
def priya():
    return principals.get("staff-priya")


def test_a1_a_customer_cannot_read_another_accounts_order(lumenworks):
    with pytest.raises(AccessDenied) as denied:
        orders.get(lumenworks, "ORD-1001")

    assert "ACCT-001" in str(denied.value)
    assert "outside this session's scope" in str(denied.value)


def test_a1_the_denial_says_out_of_scope_not_not_found(lumenworks):
    """Filtering the query instead would answer "no such order" for a record
    that plainly exists, and would make the boundary invisible in the audit."""
    from app.auth.scope import NotFound

    with pytest.raises(AccessDenied):
        orders.get(lumenworks, "ORD-1001")
    with pytest.raises(NotFound):
        orders.get(lumenworks, "ORD-9999")


def test_a2_listing_returns_only_the_sessions_own_orders(lumenworks):
    assert [o["order_id"] for o in orders.list_for(lumenworks)] == ["ORD-2001", "ORD-2002"]


def test_a3_a_customer_cannot_read_another_accounts_ticket(northstar):
    with pytest.raises(AccessDenied):
        tickets.get(northstar, "TKT-502")


def test_a3_ticket_search_is_scoped_too(northstar):
    found = {t["ticket_id"] for t in tickets.search(northstar)}
    assert found == {"TKT-501", "TKT-504", "TKT-450"}


def test_a6_an_internal_agent_reads_across_accounts(rohit):
    assert orders.get(rohit, "ORD-1001")["order_id"] == "ORD-1001"
    assert len(orders.list_for(rohit)) == 6
    assert len(accounts.list_for(rohit)) == 4


def test_a7_a_customer_lacks_the_signals_capability(northstar, rohit):
    with pytest.raises(AccessDenied, match="signals"):
        require_capability(northstar, SIGNALS, action="detect_issues")

    require_capability(rohit, SIGNALS, action="detect_issues")  # does not raise


def test_a8_an_agent_cannot_approve_a_credit_above_the_threshold(rohit):
    with pytest.raises(AccessDenied, match="approve:credit"):
        require_capability(rohit, APPROVE_CREDIT, action="confirm credit of INR 1,500")


def test_a9_a_manager_can(priya):
    require_capability(priya, APPROVE_CREDIT, action="confirm credit of INR 1,500")


def test_a10_every_denial_writes_an_audit_row(lumenworks):
    assert audit.count() == 0

    with pytest.raises(AccessDenied):
        orders.get(lumenworks, "ORD-1001")

    entries = audit.entries()
    assert len(entries) == 1
    assert entries[0]["principal_id"] == "cust-lumenworks"
    assert entries[0]["outcome"] == "denied"
    assert "ORD-1001" in entries[0]["resource"]


def test_a10_an_allowed_read_does_not_write_a_denial(rohit):
    orders.get(rohit, "ORD-1001")
    assert audit.count(outcome="denied") == 0


def test_a11_a_model_supplied_account_id_is_intersected_not_trusted(lumenworks):
    """The requirement-2 test. The model asks for ACCT-001 while the session
    belongs to a customer of ACCT-002, and the repository denies it regardless
    of what any prompt said."""
    with pytest.raises(AccessDenied):
        orders.list_for(lumenworks, account_id="ACCT-001")
    with pytest.raises(AccessDenied):
        tickets.search(lumenworks, account_id="ACCT-001")
    with pytest.raises(AccessDenied):
        accounts.get(lumenworks, "ACCT-001")
    with pytest.raises(AccessDenied):
        tickets.resolution_history(lumenworks, "ACCT-001")

    assert audit.count(outcome="denied") == 4


def test_a12_the_hypothetical_path_is_scoped_as_well(northstar):
    """Closes the hole A11 misses.

    Evaluate tools take hypothetical parameters, so their account_id touches no
    row a repository guards. Unscoped, a Northstar customer could ask "what
    would ACCT-002 get for a 3-hour delay?" and read LumenWorks' contractual
    4-hour threshold back out of the answer -- a contract-terms leak that trips
    no repository check.
    """
    with pytest.raises(AccessDenied):
        resolve_subject_account(northstar, "ACCT-002")

    assert resolve_subject_account(northstar, None) == "ACCT-001"
    assert resolve_subject_account(northstar, "ACCT-001") == "ACCT-001"


def test_a12_an_internal_user_must_name_the_account(rohit):
    """They have no account of their own, and the answer differs per account --
    2 hours under the SOP, 4 under the LumenWorks agreement. Guessing would be
    wrong 25% of the time."""
    assert resolve_subject_account(rohit, "ACCT-002") == "ACCT-002"

    with pytest.raises(ValueError, match="which account"):
        resolve_subject_account(rohit, None)


def test_the_principal_is_never_a_model_supplied_parameter():
    """Every repository entry point takes the principal as its first positional
    argument, so there is no code path that reaches data without one."""
    import inspect

    for module in (orders, tickets, accounts):
        for name, function in vars(module).items():
            if name.startswith("_") or not inspect.isfunction(function):
                continue
            if function.__module__ != module.__name__:
                continue
            parameters = list(inspect.signature(function).parameters)
            if name == "all_with_resolutions":
                continue  # documented as unscoped; guarded by capability upstream
            assert parameters[0] == "principal", f"{module.__name__}.{name}"


def test_personas_cover_both_user_contexts():
    everyone = principals.PERSONAS.values()
    assert sum(1 for p in everyone if p.kind == "customer") == 4
    assert sum(1 for p in everyone if p.kind == "internal") == 2
    assert all(p.account_ids or p.reads_any_account for p in everyone)
    assert not any(p.can(READ_DEPRECATED) for p in everyone if p.kind == "customer")
