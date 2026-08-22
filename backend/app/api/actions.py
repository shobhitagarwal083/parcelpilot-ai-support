"""Confirm and reject endpoints.

This is the only place an action is executed, and it is reachable only from a
human clicking a button. No tool is bound to it.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentPrincipal, to_http
from app.repo import actions

router = APIRouter(prefix="/api/actions", tags=["actions"])


class RejectBody(BaseModel):
    reason: str | None = None


@router.get("")
def list_actions(principal: CurrentPrincipal, status: str | None = None) -> dict:
    return {"actions": [a.to_dict() for a in actions.list_for(principal, status=status)]}


@router.get("/{action_id}")
def get_action(action_id: str, principal: CurrentPrincipal) -> dict:
    try:
        return actions.get(principal, action_id).to_dict()
    except Exception as exc:
        raise to_http(exc) from exc


@router.post("/{action_id}/confirm")
def confirm_action(action_id: str, principal: CurrentPrincipal) -> dict:
    try:
        return actions.confirm(principal, action_id).to_dict()
    except Exception as exc:
        raise to_http(exc) from exc


@router.post("/{action_id}/reject")
def reject_action(action_id: str, principal: CurrentPrincipal, body: RejectBody) -> dict:
    try:
        return actions.reject(principal, action_id, reason=body.reason).to_dict()
    except Exception as exc:
        raise to_http(exc) from exc
