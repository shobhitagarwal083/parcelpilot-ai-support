"""Proactive detection, exposed as a tool as well as an endpoint.

Offered to every persona and refused inside the handler rather than hidden from
customers. Hiding it would be a prompt-level guard; being refused by the
capability check is a data-layer one, and the refusal shows up in the tool trace
where a reviewer can see the boundary working.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.tools.registry import _object, tool
from app.auth.principals import Principal
from app.ops import signals


@tool(
    name="detect_issues",
    description=(
        "Scan every account for recurring, urgent or unusual issues: breached response "
        "targets, security incidents, service credits that are owed but unclaimed, clusters "
        "of tickets on one known defect, stalled cancellations and past guidance the current "
        "rules contradict. Internal staff only."
    ),
    parameters=_object({}),
    trace_label="scanning for issues across accounts",
)
def detect_issues(principal: Principal, *, as_of: datetime) -> dict[str, Any]:
    found = signals.detect(principal, as_of)
    return {"count": len(found), "signals": [s.to_dict() for s in found]}
