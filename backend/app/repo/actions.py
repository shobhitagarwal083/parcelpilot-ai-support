"""Two-phase actions -- requirement 4, read in its strong form.

Requirement 4 says a state-changing action needs explicit user confirmation.
That can be read weakly (instruct the model to ask first) or strongly (make it
impossible for the model to act). The weak reading relies on the model behaving,
and fails under prompt injection or a confused loop. The strong reading survives
both, so that is the one implemented here:

    propose_action()   writes a row with status PENDING and changes nothing else
    POST /confirm      re-checks capability, applies the effect, writes an audit row

`confirm` is not bound to any tool, and there is no tool that reaches it. The
model's only reachable verb is `propose`. `tests/test_actions.py` walks the
agent package and asserts that stays true.

Effects the supplied data can express are applied for real -- `update_ticket`
writes to the tickets table. The rest (paging a human, crediting a billing
system) are external systems the brief explicitly permits mocking, so they are
recorded rather than simulated.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app import config
from app.auth.principals import APPROVE_CREDIT, PROPOSE, Principal
from app.auth.scope import AccessDenied, NotFound, assert_account_access, require_capability
from app.knowledge.loader import load
from app.policy.types import Decision
from app.repo import audit
from app.repo.db import connect

ActionType = Literal[
    "create_escalation", "update_ticket", "create_followup_task", "issue_service_credit"
]

Status = Literal["PENDING", "NEEDS_APPROVAL", "EXECUTED", "REJECTED"]

ACTION_TYPES: tuple[ActionType, ...] = (
    "create_escalation", "update_ticket", "create_followup_task", "issue_service_credit",
)

#: Effects that touch the supplied data are applied; everything else is an
#: external system the brief permits mocking.
_REAL_EFFECTS = {"update_ticket"}


class ActionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    created_at: str
    principal_id: str
    account_id: str
    action_type: ActionType
    target_id: str | None
    payload: dict[str, Any]
    decision: dict[str, Any] | None
    status: Status
    resolved_at: str | None = None
    resolved_by: str | None = None

    @property
    def needs_approval(self) -> bool:
        return self.status == "NEEDS_APPROVAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "created_at": self.created_at,
            "principal_id": self.principal_id,
            "account_id": self.account_id,
            "action_type": self.action_type,
            "target_id": self.target_id,
            "payload": self.payload,
            "decision": self.decision,
            "status": self.status,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "needs_approval": self.needs_approval,
        }


def approval_threshold_inr() -> Decimal:
    """From SOP v4 section 3, via the rulebook -- not an invented number."""
    constraint = next(
        c for c in load().constraints if c.kind == "approval_threshold"
    )
    return Decimal(str(constraint.amount_inr))


def _row(row: Any) -> PendingAction:
    record = dict(row)
    record["payload"] = json.loads(record["payload"])
    record["decision"] = json.loads(record["decision"]) if record["decision"] else None
    return PendingAction(**record)


def propose(
    principal: Principal,
    *,
    account_id: str,
    action_type: ActionType,
    payload: dict[str, Any],
    target_id: str | None = None,
    decision: Decision | None = None,
) -> PendingAction:
    """Prepare an action for a human to confirm. Nothing else changes."""
    require_capability(principal, PROPOSE, action=f"propose {action_type}")
    assert_account_access(principal, account_id, resource=f"account {account_id}")

    if action_type not in ACTION_TYPES:
        raise ActionError(f"unknown action type: {action_type!r}")

    status: Status = "PENDING"
    if action_type == "issue_service_credit":
        amount = Decimal(str(payload.get("amount_inr", 0)))
        if amount > approval_threshold_inr():
            status = "NEEDS_APPROVAL"

    action = PendingAction(
        action_id=uuid.uuid4().hex[:12],
        created_at=config.SNAPSHOT_AT.isoformat(),
        principal_id=principal.id,
        account_id=account_id,
        action_type=action_type,
        target_id=target_id,
        payload=payload,
        decision=decision.to_dict() if decision else None,
        status=status,
    )

    with connect() as conn:
        conn.execute(
            "INSERT INTO pending_actions (action_id, created_at, principal_id, account_id, "
            "action_type, target_id, payload, decision, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action.action_id, action.created_at, action.principal_id, action.account_id,
                action.action_type, action.target_id, json.dumps(action.payload),
                json.dumps(action.decision) if action.decision else None, action.status,
            ),
        )

    audit.record(
        principal_id=principal.id,
        action=f"propose:{action_type}",
        resource=action.action_id,
        outcome="proposed",
        detail=f"status={status} target={target_id}",
    )
    return action


def get(principal: Principal, action_id: str) -> PendingAction:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
    if row is None:
        raise NotFound(f"no such action: {action_id}")
    action = _row(row)
    assert_account_access(principal, action.account_id, resource=f"action {action_id}")
    return action


def list_for(principal: Principal, *, status: str | None = None) -> list[PendingAction]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pending_actions ORDER BY created_at, action_id")
        actions = [_row(row) for row in rows]
    visible = [a for a in actions if principal.can_access(a.account_id)]
    return [a for a in visible if status is None or a.status == status]


def confirm(principal: Principal, action_id: str) -> PendingAction:
    """Execute a proposed action. Reachable only from the API, never from a tool.

    Capability is re-checked here rather than trusted from proposal time: the
    session that confirms is not necessarily the one that proposed, and an
    approval requirement that was only enforced at proposal is not an approval
    requirement at all.
    """
    action = get(principal, action_id)

    if action.status == "EXECUTED":
        audit.record(
            principal_id=principal.id,
            action=f"confirm:{action.action_type}",
            resource=action_id,
            outcome="ignored",
            detail="already executed",
        )
        return action

    if action.status == "REJECTED":
        raise ActionError(f"{action_id} was rejected and cannot be confirmed")

    if action.needs_approval:
        require_capability(
            principal,
            APPROVE_CREDIT,
            action=f"confirm {action.action_type} of INR {action.payload.get('amount_inr')}",
        )

    _apply(action)

    with connect() as conn:
        conn.execute(
            "UPDATE pending_actions SET status = ?, resolved_at = ?, resolved_by = ? "
            "WHERE action_id = ?",
            ("EXECUTED", config.SNAPSHOT_AT.isoformat(), principal.id, action_id),
        )

    audit.record(
        principal_id=principal.id,
        action=f"confirm:{action.action_type}",
        resource=action_id,
        outcome="executed",
        detail=json.dumps(action.payload),
    )
    return get(principal, action_id)


def reject(principal: Principal, action_id: str, reason: str | None = None) -> PendingAction:
    action = get(principal, action_id)

    if action.status == "EXECUTED":
        raise ActionError(f"{action_id} has already been executed and cannot be rejected")
    if action.status == "REJECTED":
        return action

    with connect() as conn:
        conn.execute(
            "UPDATE pending_actions SET status = ?, resolved_at = ?, resolved_by = ? "
            "WHERE action_id = ?",
            ("REJECTED", config.SNAPSHOT_AT.isoformat(), principal.id, action_id),
        )

    audit.record(
        principal_id=principal.id,
        action=f"reject:{action.action_type}",
        resource=action_id,
        outcome="rejected",
        detail=reason,
    )
    return get(principal, action_id)


def _apply(action: PendingAction) -> None:
    if action.action_type not in _REAL_EFFECTS:
        return
    if action.action_type == "update_ticket":
        new_status = action.payload.get("status")
        if not new_status or not action.target_id:
            raise ActionError("update_ticket needs a target ticket and a status")
        with connect() as conn:
            changed = conn.execute(
                "UPDATE tickets SET status = ? WHERE ticket_id = ?",
                (new_status, action.target_id),
            ).rowcount
        if changed == 0:
            raise ActionError(f"no such ticket: {action.target_id}")


__all__ = [
    "ACTION_TYPES",
    "AccessDenied",
    "ActionError",
    "PendingAction",
    "approval_threshold_inr",
    "confirm",
    "get",
    "list_for",
    "propose",
    "reject",
]
