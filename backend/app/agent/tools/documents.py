"""Document retrieval tool."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.tools.registry import _object, tool
from app.auth.principals import Principal
from app.retrieval.search import search


@tool(
    name="search_policy_documents",
    description=(
        "Search ParcelPilot's policies, SOPs, product documentation and signed customer "
        "agreements. Results carry an authority tier: 1 is a signed customer agreement, "
        "2 is current policy or SOP, 3 is product documentation. Superseded documents are "
        "excluded. Use this to quote a source, never to decide an outcome -- the evaluate_* "
        "tools decide outcomes."
    ),
    parameters=_object(
        {
            "query": {"type": "string", "description": "What to look for, in plain words."},
            "limit": {"type": "integer", "description": "Maximum results (default 5)."},
        },
        required=["query"],
    ),
    trace_label="searching policy documents",
)
def search_policy_documents(
    principal: Principal, *, as_of: datetime, query: str, limit: int = 5
) -> dict[str, Any]:
    hits = search(principal, query, limit=min(int(limit), 10))
    return {
        "query": query,
        "results": [h.to_dict() for h in hits],
        "note": (
            "Authority tier orders these for reading. It does not decide anything: "
            "precedence between conflicting rules is resolved by the evaluate_* tools."
        ),
    }
