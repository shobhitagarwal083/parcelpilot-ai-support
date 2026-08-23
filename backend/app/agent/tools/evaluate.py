"""Decision tools -- where every outcome in this system is actually decided.

These return a finished `Decision`. The model narrates it; it never receives the
ingredients from which a different number could be derived, and it never picks
between conflicting sources. If it could do either, the design would be wrong.

All three scope their `account_id` argument, including the hypothetical path.
`evaluate_service_credit` takes free parameters, so its account_id is attached
to no row any repository guards -- and unscoped, a Northstar customer could ask
what ACCT-002 would get for a 3-hour delay and read LumenWorks' contractual
4-hour threshold back out of the answer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.tools.registry import _object, tool
from app.auth import disclosure
from app.auth.principals import Principal
from app.auth.scope import resolve_subject_account
from app.policy import cancellation, service_credit, sla
from app.repo import accounts, orders, tickets


@tool(
    name="evaluate_cancellation",
    description=(
        "Decide whether an order can be cancelled and what fee applies. Resolves the customer "
        "agreement against the SOP deterministically and returns the outcome with citations, "
        "any rules it overrode, contradicted past guidance, and known-issue caveats. Use this "
        "rather than reading a fee out of a document."
    ),
    parameters=_object(
        {"order_id": {"type": "string", "description": "e.g. ORD-1001."}},
        required=["order_id"],
    ),
    trace_label="evaluating cancellation terms",
)
def evaluate_cancellation(
    principal: Principal, *, as_of: datetime, order_id: str
) -> dict[str, Any]:
    order = orders.get(principal, order_id)
    history = tickets.resolution_history(principal, order["account_id"])
    decision = cancellation.evaluate(order=order, as_of=as_of, history=history)
    return disclosure.for_principal(decision.to_dict(), principal)


@tool(
    name="evaluate_service_credit",
    description=(
        "Decide whether a failed pickup earns a service credit and how much. The answer "
        "depends on the account, because a signed agreement can replace both the delay "
        "threshold and the amount -- so if the account is not obvious from the conversation, "
        "ask rather than assuming.\n"
        "IF THE USER STATES THE DELAY THEMSELVES ('a pickup is three hours late'), pass their "
        "stated figures as hours_past_window_end/carrier_fault/customer_fault and answer about "
        "that. Do NOT look up one of their orders and answer about its delay instead: a real "
        "order with a longer delay gives a different verdict, and reporting it as though it "
        "answered their question is wrong even though every number in it is right. "
        "Pass order_id only when the user asked about that specific order."
    ),
    parameters=_object(
        {
            "order_id": {"type": "string", "description": "Use a real order's facts."},
            "account_id": {
                "type": "string",
                "description": "Whose terms apply. Optional for a customer session.",
            },
            "hours_past_window_end": {
                "type": "number",
                "description": "Hours past the end of the scheduled pickup window.",
            },
            "carrier_fault": {"type": "boolean"},
            "customer_fault": {"type": "boolean"},
            "shipment_fee_inr": {
                "type": "number",
                "description": "Needed only when the amount is a percentage of the fee.",
            },
        }
    ),
    trace_label="evaluating service credit",
)
def evaluate_service_credit(
    principal: Principal,
    *,
    as_of: datetime,
    order_id: str | None = None,
    account_id: str | None = None,
    hours_past_window_end: float | None = None,
    carrier_fault: bool | None = None,
    customer_fault: bool | None = None,
    shipment_fee_inr: float | None = None,
) -> dict[str, Any]:
    if order_id:
        order = orders.get(principal, order_id)
        decision = service_credit.evaluate(
            account_id=order["account_id"],
            hours_past_window_end=service_credit.hours_past_window_end(order, as_of),
            carrier_fault=order["carrier_fault"],
            customer_fault=order["customer_fault"],
            shipment_fee_inr=order["shipment_fee_inr"],
            as_of=as_of,
            order_id=order_id,
            history=tickets.resolution_history(principal, order["account_id"]),
        )
        return disclosure.for_principal(decision.to_dict(), principal)

    subject = resolve_subject_account(principal, account_id)
    decision = service_credit.evaluate(
        account_id=subject,
        hours_past_window_end=hours_past_window_end,
        carrier_fault=carrier_fault,
        customer_fault=customer_fault,
        shipment_fee_inr=shipment_fee_inr,
        as_of=as_of,
    )
    return disclosure.for_principal(decision.to_dict(), principal)


@tool(
    name="evaluate_sla",
    description=(
        "Decide the first-response target for a ticket and whether it has been met. The clock "
        "runs from when the ticket was created, and coverage comes from the governing rule -- "
        "some targets are 24x7 and some only run during business hours, which means a ticket "
        "raised at a weekend may not have started its clock at all."
    ),
    parameters=_object(
        {"ticket_id": {"type": "string", "description": "e.g. TKT-501."}},
        required=["ticket_id"],
    ),
    trace_label="evaluating the response target",
)
def evaluate_sla(principal: Principal, *, as_of: datetime, ticket_id: str) -> dict[str, Any]:
    ticket = tickets.get(principal, ticket_id)
    account = accounts.get(principal, ticket["account_id"])
    decision = sla.evaluate(ticket=ticket, account=account, as_of=as_of)
    return disclosure.for_principal(decision.to_dict(), principal)
