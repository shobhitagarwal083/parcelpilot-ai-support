"""Known-issue caveats: post-decision annotations keyed on record fields.

Not rules -- they never change an outcome. They change what a truthful answer
has to mention. KI-211 is the clearest case: an order showing BOOKED normally
means no pickup was confirmed, but on SwiftShip it may only mean the webhook has
not landed yet, and telling a customer their parcel was not collected when it is
already on a van is a real operational mistake.

Resolved issues are excluded from matching. The product guide warns against
reaching for one to explain a new incident, and a plausible stale explanation is
worse than no explanation.
"""

from __future__ import annotations

from typing import Any, Literal

from app.knowledge.loader import Rulebook, load
from app.policy.types import Caveat, Citation

Audience = Literal["internal", "customer"]


def match(facts: dict[str, Any], *, book: Rulebook | None = None) -> list:
    book = book or load()
    return [issue for issue in book.known_issues if issue.matches(facts)]


def caveats_for(facts: dict[str, Any], *, book: Rulebook | None = None) -> list[Caveat]:
    return [
        Caveat(issue_id=issue.id, text=issue.caveat, customer_safe_text=issue.customer_caveat)
        for issue in match(facts, book=book)
    ]


def citations_for(facts: dict[str, Any], *, book: Rulebook | None = None) -> list[Citation]:
    from app.policy.engine import _doc_title

    return [
        Citation(
            doc_id=issue.source.doc,
            doc_title=_doc_title(issue.source.doc),
            section=issue.source.section,
            quote=" ".join(issue.source.quote.split()),
            authority_tier=issue.authority_tier,
        )
        for issue in match(facts, book=book)
    ]
