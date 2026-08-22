"""The triage board feed."""

from __future__ import annotations

from fastapi import APIRouter

from app import config
from app.api.deps import CurrentPrincipal, to_http
from app.ops import signals as detector

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("")
def list_signals(principal: CurrentPrincipal) -> dict:
    try:
        found = detector.detect(principal, config.SNAPSHOT_AT)
    except Exception as exc:
        raise to_http(exc) from exc
    return {
        "as_of": config.SNAPSHOT_AT.isoformat(),
        "signals": [s.to_dict() for s in found],
    }
