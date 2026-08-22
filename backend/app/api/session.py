"""Persona switching -- the demo control that makes the access boundary visible."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentPrincipal
from app.auth import principals

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/personas")
def list_personas() -> dict:
    return {
        "default": principals.DEFAULT_PERSONA,
        "personas": [p.to_dict() for p in principals.PERSONAS.values()],
    }


@router.get("/me")
def whoami(principal: CurrentPrincipal) -> dict:
    return principal.to_dict()
