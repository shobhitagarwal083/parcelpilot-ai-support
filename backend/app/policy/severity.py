"""Severity classification -- Policy v3 section 2, kept deterministic.

Severity drives the SLA target, which drives breach detection, which drives
escalation. A model judgement at the root of that chain would reintroduce
exactly the non-determinism the engine exists to remove (D-12).

This is openly the weakest link in the design: phrase matching is brittle on
tickets it has not seen. The mitigation is the escape hatch -- no match returns
None, and the caller reports `indeterminate` and asks rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.knowledge.loader import Rulebook, SeverityRule, load


@dataclass(frozen=True)
class SeverityVerdict:
    level: str | None
    rule: SeverityRule | None

    @property
    def is_known(self) -> bool:
        return self.level is not None


def classify(
    subject: str, description: str = "", *, book: Rulebook | None = None
) -> SeverityVerdict:
    book = book or load()
    text = f"{subject or ''} {description or ''}"
    for rule in book.severity_rules:
        if rule.matches(text):
            return SeverityVerdict(level=rule.level, rule=rule)
    return SeverityVerdict(level=None, rule=None)
