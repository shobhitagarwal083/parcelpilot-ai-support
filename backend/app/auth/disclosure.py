"""What a principal may be *told*, as distinct from what is true.

Access control decides which records a principal can reach. This decides how
much of a reachable answer they see -- a narrower question, and one the pack
forces: KI-211 is a real caveat that a customer genuinely needs ("your parcel
may already have been collected"), carrying an internal tracker id they have no
business seeing.

`Caveat` already ships both registers, which is why this module can exist at
all. The mistake worth avoiding is letting the *client* choose between them.
Serialising the internal text and trusting the browser not to render it puts the
secret in the response body and the enforcement in the UI -- the same class of
error as enforcing access control in the prompt, which requirement 2 rules out
explicitly. So the redaction happens here, at the tool boundary, before the
payload is built.

Applied in `agent/tools/evaluate.py` and `agent/tools/actions.py`. The signals
path needs no equivalent: `detect_issues` requires the `signals` capability,
which no customer holds.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.auth.principals import Principal


@lru_cache(maxsize=1)
def _known_issue_sources() -> frozenset[tuple[str, str]]:
    """(doc_id, section) pairs whose section labels name an internal issue.

    Read from the rulebook rather than pattern-matched out of the section
    string, so adding a known issue cannot quietly open a hole.
    """
    from app.knowledge.loader import load

    return frozenset((issue.source.doc, issue.source.section) for issue in load().known_issues)


def for_principal(decision: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Redact a serialised Decision down to what this principal may be told.

    Internal principals get it whole. Correctness is never touched: the outcome,
    the amount, the facts and the overrides are identical either way. Only the
    register of the caveat changes, and the citation that would reintroduce the
    tracker id is dropped -- the customer-safe text carries the substance
    without it.
    """
    if principal.kind != "customer":
        return decision

    redacted = dict(decision)

    redacted["caveats"] = [
        {
            "issue_id": "",
            "text": caveat.get("customer_safe_text", ""),
            "customer_safe_text": caveat.get("customer_safe_text", ""),
        }
        for caveat in decision.get("caveats") or []
    ]

    sources = _known_issue_sources()
    redacted["citations"] = [
        citation
        for citation in decision.get("citations") or []
        if (citation.get("doc_id"), citation.get("section")) not in sources
    ]

    return redacted
