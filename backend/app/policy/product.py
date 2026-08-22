"""Tier-3 product capability lookups.

Kept separate from the rule engine because these are facts about what the
product does, not decisions about what a customer is owed. The distinction
matters for trap T8: "Growth supports 5,000 rows" is a product fact, "uploads
above ~3,000 rows fail" is a known defect, and a closed ticket calling the
second one a plan limit is tier-4 guidance that was wrong when it was given.
"""

from __future__ import annotations

from typing import Any

from app.knowledge.loader import ProductFact, Rulebook, load


def capability(
    topic: str, facts: dict[str, Any], *, book: Rulebook | None = None
) -> ProductFact | None:
    book = book or load()
    for fact in book.product_facts:
        if fact.topic == topic and fact.matches(facts):
            return fact
    return None
