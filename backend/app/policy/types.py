"""The contract between the policy engine and everything above it.

`Decision` is the *entire* interface the agent sees. It is handed a finished
answer to narrate, never the ingredients from which a different number could be
derived. That is what keeps the model out of arbitration and arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

Tier = Literal[1, 2, 3, 4]

Domain = Literal["cancellation", "service_credit", "sla"]

Outcome = Literal[
    "allowed",
    "denied",  # cancellation
    "eligible",
    "ineligible",  # service credit
    "breached",
    "within_target",
    "not_started",  # sla
    "indeterminate",  # any domain: we do not know
]

Coverage = Literal["24x7", "business"]

#: Why a rule did not decide the outcome.
#:
#: `outranked` — it matched the facts and lost on authority tier.
#: `replaced`  — a tier-1 rule declared `replaces:` on it, and it would
#:               otherwise have matched.
#:
#: Replaced rules are kept and disclosed rather than discarded. Dropping the SOP
#: silently is the same failure as never retrieving it (D-15).
OverrideKind = Literal["outranked", "replaced"]


@dataclass(frozen=True)
class Citation:
    doc_id: str
    doc_title: str
    section: str
    quote: str
    authority_tier: Tier

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "section": self.section,
            "quote": self.quote,
            "authority_tier": self.authority_tier,
        }


@dataclass(frozen=True)
class Override:
    winning_rule_id: str
    overridden_rule_id: str
    kind: OverrideKind
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "winning_rule_id": self.winning_rule_id,
            "overridden_rule_id": self.overridden_rule_id,
            "kind": self.kind,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class Contradiction:
    """A tier-4 historical resolution that disagrees with the winning rule.

    Never suppressed. The right behaviour is not to ignore a poisoned ticket but
    to say that a previous answer was wrong, so the same mistake is not repeated
    by a human reading the same history.
    """

    ticket_id: str
    recorded_resolution: str
    why_wrong: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "recorded_resolution": self.recorded_resolution,
            "why_wrong": self.why_wrong,
        }


@dataclass(frozen=True)
class Caveat:
    """A known-issue warning attached after the decision is made."""

    issue_id: str
    text: str
    customer_safe_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "text": self.text,
            "customer_safe_text": self.customer_safe_text,
        }


@dataclass
class Decision:
    domain: Domain
    outcome: Outcome
    amount_inr: Decimal | None = None
    facts_used: dict[str, Any] = field(default_factory=dict)
    unknowns: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    overrides: list[Override] = field(default_factory=list)
    contradicts: list[Contradiction] = field(default_factory=list)
    caveats: list[Caveat] = field(default_factory=list)
    #: Constraints that shape the answer without changing the outcome -- e.g. a
    #: monthly aggregate cap the pack gives us no ledger to check against.
    notes: list[str] = field(default_factory=list)
    requires_human: bool = False
    human_reason: str | None = None
    winning_rule_id: str | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "outcome": self.outcome,
            "amount_inr": None if self.amount_inr is None else _money(self.amount_inr),
            "facts_used": self.facts_used,
            "unknowns": self.unknowns,
            "citations": [c.to_dict() for c in self.citations],
            "overrides": [o.to_dict() for o in self.overrides],
            "contradicts": [c.to_dict() for c in self.contradicts],
            "caveats": [c.to_dict() for c in self.caveats],
            "notes": self.notes,
            "requires_human": self.requires_human,
            "human_reason": self.human_reason,
            "winning_rule_id": self.winning_rule_id,
            "summary": self.summary,
        }


def _money(amount: Decimal) -> float | int:
    normalised = amount.normalize()
    return int(normalised) if normalised == normalised.to_integral_value() else float(normalised)
