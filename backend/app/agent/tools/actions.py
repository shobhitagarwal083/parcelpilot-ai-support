"""The one state-changing verb the model can reach -- and it changes nothing.

`propose_action` writes a row with status PENDING. Execution is an HTTP endpoint
with no tool bound to it, so there is no sequence of tool calls, however
confused or adversarial the conversation, that commits anything.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.agent.tools.registry import _object, tool
from app.auth.principals import Principal
from app.auth.scope import resolve_subject_account
from app.repo import actions


@tool(
    name="propose_action",
    description=(
        "Prepare a state-changing action for a human to confirm. This does NOT execute "
        "anything: it creates a pending proposal that a person must explicitly approve.\n"
        "  create_escalation     - route to a human colleague\n"
        "  update_ticket         - change a TICKET's status. target_id must be a ticket "
        "(TKT-xxx); an order id will be rejected\n"
        "  create_followup_task  - record work for someone to pick up\n"
        "  issue_service_credit  - credit an account, using the amount an evaluate tool "
        "returned\n"
        "THERE IS NO ACTION THAT CANCELS, MODIFIES OR RELEASES AN ORDER. If someone asks you "
        "to cancel one, explain whether a fee would apply, then raise a ticket or escalate so "
        "a person can carry it out. Never say an order will be cancelled, and never reach for "
        "update_ticket to do it -- that changes a ticket, not an order.\n"
        "Always tell the user what you have prepared and that it awaits their confirmation."
    ),
    parameters=_object(
        {
            "action_type": {
                "type": "string",
                "enum": list(actions.ACTION_TYPES),
            },
            "account_id": {"type": "string", "description": "Whose account this affects."},
            "target_id": {
                "type": "string",
                "description": (
                    "What the action applies to. For update_ticket this must be a ticket id "
                    "(TKT-xxx) -- passing an order id fails, because it updates the tickets "
                    "table."
                ),
            },
            "reason": {"type": "string", "description": "Why this action is warranted."},
            "amount_inr": {
                "type": "number",
                "description": "For issue_service_credit only. Use the amount the evaluate "
                "tool returned; do not compute one.",
            },
            "status": {
                "type": "string",
                "description": "For update_ticket only: the new ticket status.",
            },
        },
        required=["action_type", "reason"],
    ),
    trace_label="preparing an action for confirmation",
)
def propose_action(
    principal: Principal,
    *,
    as_of: datetime,
    action_type: str,
    reason: str,
    account_id: str | None = None,
    target_id: str | None = None,
    amount_inr: float | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    subject = resolve_subject_account(principal, account_id)

    payload: dict[str, Any] = {"reason": reason}
    if amount_inr is not None:
        payload["amount_inr"] = amount_inr
    if status is not None:
        payload["status"] = status

    action = actions.propose(
        principal,
        account_id=subject,
        action_type=action_type,
        payload=payload,
        target_id=target_id,
    )
    return {
        **action.to_dict(),
        "executed": False,
        "next_step": (
            "Nothing has changed yet. Tell the user what this would do and ask them to "
            "confirm it; only they can execute it."
        ),
    }


def summarise_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)
