"""Parse and validate `rulebook.yaml` at startup.

The validation is the point. A rulebook that has drifted from the documents, or
that has quietly acquired a hardcoded order ID in a condition, is worse than no
rulebook -- it looks authoritative while being wrong. Every check below fails
loudly at import rather than producing a plausible answer later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app import config
from app.ingest.documents import load_manifest

#: Identifiers that must never appear inside a `when:` block. Scoping an
#: agreement to an account is legitimate and belongs in `applies_to.accounts`;
#: a condition that fires on a specific record is a hardcoded answer wearing a
#: rulebook's clothes, and the brief warns against exactly that.
_RECORD_ID = re.compile(r"\b(ORD|TKT|ACCT)-\d+\b")

_ALL_ACCOUNTS = "*"


class RulebookError(RuntimeError):
    """Raised when the rulebook is internally inconsistent or unsourced."""


@dataclass(frozen=True)
class Source:
    doc: str
    section: str
    quote: str = ""


def _compare(fact: Any, op: str, expected: Any) -> bool:
    if fact is None:
        return False
    # `1 == True` in Python. Facts and conditions must agree on type, or a row
    # storing 1 would satisfy a condition meaning "carrier is at fault".
    if isinstance(expected, bool) != isinstance(fact, bool) and op in ("eq", "ne"):
        return op == "ne"
    try:
        match op:
            case "eq":
                return fact == expected
            case "ne":
                return fact != expected
            case "gt":
                return fact > expected
            case "gte":
                return fact >= expected
            case "lt":
                return fact < expected
            case "lte":
                return fact <= expected
            case "in":
                return fact in expected
            case "not_in":
                return fact not in expected
    except TypeError:
        return False
    raise RulebookError(f"unknown operator: {op!r}")


def _matches_conditions(when: dict[str, dict[str, Any]], facts: dict[str, Any]) -> bool:
    for fact_name, condition in when.items():
        value = facts.get(fact_name)
        for op, expected in condition.items():
            if not _compare(value, op, expected):
                return False
    return True


@dataclass(frozen=True)
class Rule:
    id: str
    domain: str
    authority_tier: int
    status: str
    effective_from: date
    effective_to: date | None
    accounts: frozenset[str] | None  # None means every account
    when: dict[str, dict[str, Any]]
    then: dict[str, Any]
    replaces: tuple[str, ...]
    source: Source

    def applies_to_account(self, account_id: str | None) -> bool:
        if self.accounts is None:
            return True
        return account_id in self.accounts

    def is_in_force(self, as_of: datetime) -> bool:
        day = as_of.date()
        if self.status not in ("CURRENT", "ACTIVE"):
            return False
        if day < self.effective_from:
            return False
        return not (self.effective_to and day > self.effective_to)

    def matches(self, facts: dict[str, Any]) -> bool:
        return _matches_conditions(self.when, facts)

    def prescribes_the_same_as(self, other: Rule) -> bool:
        return self.then == other.then


@dataclass(frozen=True)
class Constraint:
    id: str
    domain: str
    kind: str
    authority_tier: int
    accounts: frozenset[str] | None
    amount_inr: float
    source: Source
    human_reason: str | None = None
    note: str | None = None

    def applies_to_account(self, account_id: str | None) -> bool:
        return self.accounts is None or account_id in self.accounts


@dataclass(frozen=True)
class SeverityRule:
    id: str
    level: str
    any_phrase: tuple[str, ...]
    all_of: tuple[tuple[str, ...], ...]
    source: Source

    def matches(self, text: str) -> bool:
        haystack = text.lower()
        if self.any_phrase:
            return any(p in haystack for p in self.any_phrase)
        return all(any(p in haystack for p in group) for group in self.all_of)


@dataclass(frozen=True)
class KnownIssue:
    id: str
    title: str
    issue_status: str
    authority_tier: int
    when: dict[str, dict[str, Any]]
    caveat: str
    customer_caveat: str
    source: Source
    excluded_from_matching: bool = False

    def matches(self, facts: dict[str, Any]) -> bool:
        if self.excluded_from_matching or not self.when:
            return False
        return _matches_conditions(self.when, facts)


@dataclass(frozen=True)
class ProductFact:
    id: str
    topic: str
    when: dict[str, dict[str, Any]]
    then: dict[str, Any]
    authority_tier: int
    source: Source

    def matches(self, facts: dict[str, Any]) -> bool:
        return _matches_conditions(self.when, facts)


@dataclass(frozen=True)
class ContradictionCheck:
    id: str
    domain: str
    resolution_matches_any: tuple[str, ...]
    why_wrong: str
    when_decision: dict[str, dict[str, Any]] = field(default_factory=dict)
    topic: str | None = None

    def flags(self, resolution_text: str, decision_facts: dict[str, Any]) -> bool:
        if not any(p in resolution_text.lower() for p in self.resolution_matches_any):
            return False
        return _matches_conditions(self.when_decision, decision_facts)


@dataclass(frozen=True)
class EscalationTrigger:
    id: str
    when: dict[str, dict[str, Any]]
    reason: str
    source: Source | None
    origin: str

    def fires(self, facts: dict[str, Any]) -> bool:
        return _matches_conditions(self.when, facts)


@dataclass(frozen=True)
class Rulebook:
    rules: tuple[Rule, ...]
    constraints: tuple[Constraint, ...]
    severity_rules: tuple[SeverityRule, ...]
    known_issues: tuple[KnownIssue, ...]
    product_facts: tuple[ProductFact, ...]
    contradiction_checks: tuple[ContradictionCheck, ...]
    escalation: tuple[EscalationTrigger, ...]

    def rules_for(self, domain: str) -> list[Rule]:
        return [r for r in self.rules if r.domain == domain]

    def constraints_for(self, domain: str, account_id: str | None) -> list[Constraint]:
        return [
            c for c in self.constraints if c.domain == domain and c.applies_to_account(account_id)
        ]

    def known_issue(self, issue_id: str) -> KnownIssue | None:
        return next((k for k in self.known_issues if k.id == issue_id), None)

    def rule(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)


# ---------------------------------------------------------------- parsing


def _accounts(entry: dict) -> frozenset[str] | None:
    raw = (entry.get("applies_to") or {}).get("accounts", _ALL_ACCOUNTS)
    return None if raw == _ALL_ACCOUNTS else frozenset(raw)


def _source(entry: dict, where: str) -> Source:
    raw = entry.get("source")
    if not raw:
        raise RulebookError(f"{where}: every entry must carry source.doc and source.section")
    if not raw.get("doc") or not raw.get("section"):
        raise RulebookError(f"{where}: source needs both doc and section")
    return Source(doc=raw["doc"], section=raw["section"], quote=raw.get("quote", "").strip())


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def parse(raw: dict) -> Rulebook:
    rules = tuple(
        Rule(
            id=e["id"],
            domain=e["domain"],
            authority_tier=e["authority_tier"],
            status=e["status"],
            effective_from=_as_date(e["effective_from"]),
            effective_to=_as_date(e.get("effective_to")),
            accounts=_accounts(e),
            when=e.get("when") or {},
            then=e.get("then") or {},
            replaces=tuple(e.get("replaces") or ()),
            source=_source(e, e["id"]),
        )
        for e in raw.get("rules", [])
    )
    constraints = tuple(
        Constraint(
            id=e["id"],
            domain=e["domain"],
            kind=e["kind"],
            authority_tier=e["authority_tier"],
            accounts=_accounts(e),
            amount_inr=e["amount_inr"],
            source=_source(e, e["id"]),
            human_reason=e.get("human_reason"),
            note=e.get("note"),
        )
        for e in raw.get("constraints", [])
    )
    severity_rules = tuple(
        SeverityRule(
            id=e["id"],
            level=e["level"],
            any_phrase=tuple(p.lower() for p in e.get("any_phrase", ())),
            all_of=tuple(
                tuple(p.lower() for p in group["any_phrase"]) for group in e.get("all_of", ())
            ),
            source=_source(e, e["id"]),
        )
        for e in raw.get("severity_rules", [])
    )
    known_issues = tuple(
        KnownIssue(
            id=e["id"],
            title=e["title"],
            issue_status=e["issue_status"],
            authority_tier=e["authority_tier"],
            when=e.get("when") or {},
            caveat=" ".join(e["caveat"].split()),
            customer_caveat=" ".join(e["customer_caveat"].split()),
            source=_source(e, e["id"]),
            excluded_from_matching=bool(e.get("excluded_from_matching", False)),
        )
        for e in raw.get("known_issues", [])
    )
    product_facts = tuple(
        ProductFact(
            id=e["id"],
            topic=e["topic"],
            when=e.get("when") or {},
            then=e["then"],
            authority_tier=e["authority_tier"],
            source=_source(e, e["id"]),
        )
        for e in raw.get("product_facts", [])
    )
    contradiction_checks = tuple(
        ContradictionCheck(
            id=e["id"],
            domain=e["domain"],
            resolution_matches_any=tuple(p.lower() for p in e["resolution_matches_any"]),
            why_wrong=" ".join(e["why_wrong"].split()),
            when_decision=e.get("when_decision") or {},
            topic=e.get("topic"),
        )
        for e in raw.get("contradiction_checks", [])
    )
    escalation = tuple(
        EscalationTrigger(
            id=e["id"],
            when=e["when"],
            reason=e["reason"],
            source=_source(e, e["id"]) if e.get("source") else None,
            origin=e.get("origin", "document"),
        )
        for e in raw.get("escalation", [])
    )
    return Rulebook(
        rules=rules,
        constraints=constraints,
        severity_rules=severity_rules,
        known_issues=known_issues,
        product_facts=product_facts,
        contradiction_checks=contradiction_checks,
        escalation=escalation,
    )


# ---------------------------------------------------------------- validation


def validate(book: Rulebook, *, manifest: dict | None = None) -> None:
    entries = (
        list(book.rules)
        + list(book.constraints)
        + list(book.severity_rules)
        + list(book.known_issues)
        + list(book.product_facts)
        + list(book.contradiction_checks)
        + list(book.escalation)
    )
    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            raise RulebookError(f"duplicate id: {entry.id}")
        seen.add(entry.id)

    rule_ids = {r.id for r in book.rules}
    manifest = manifest if manifest is not None else load_manifest()

    for rule in book.rules:
        if rule.authority_tier not in (1, 2, 3, 4):
            raise RulebookError(f"{rule.id}: authority_tier must be 1-4")
        if rule.authority_tier == 1 and rule.accounts is None:
            raise RulebookError(
                f"{rule.id}: a tier-1 rule comes from a signed customer agreement and must be "
                f"scoped to specific accounts, never applies_to.accounts: '*'"
            )
        if rule.effective_to and rule.effective_to < rule.effective_from:
            raise RulebookError(f"{rule.id}: effective_to precedes effective_from")
        for target in rule.replaces:
            if target not in rule_ids:
                raise RulebookError(f"{rule.id}: replaces unknown rule {target!r}")
            if target == rule.id:
                raise RulebookError(f"{rule.id}: replaces itself")

    for entry in entries:
        source = getattr(entry, "source", None)
        if source is None:
            continue
        meta = manifest.get(source.doc)
        if meta is None:
            raise RulebookError(f"{entry.id}: source doc {source.doc!r} is not in documents.yaml")
        if meta.status == "DEPRECATED":
            raise RulebookError(
                f"{entry.id}: sourced from {source.doc!r}, which the manifest marks DEPRECATED. "
                f"A superseded document cannot back a current rule."
            )

    _assert_no_record_ids_in_conditions(book)
    _assert_sla_coverage(book)


def _assert_no_record_ids_in_conditions(book: Rulebook) -> None:
    holders = list(book.rules) + list(book.known_issues) + list(book.product_facts)
    for entry in holders:
        for fact_name, condition in entry.when.items():
            leaked = _RECORD_ID.findall(f"{fact_name} {condition}")
            if leaked:
                raise RulebookError(
                    f"{entry.id}: condition on {fact_name!r} references specific records "
                    f"{sorted(set(leaked))}. Rules must reference fields, never IDs -- account "
                    f"scoping belongs in applies_to.accounts. This is the invariant that keeps "
                    f"the rulebook from becoming a lookup table of hardcoded answers."
                )


def _assert_sla_coverage(book: Rulebook) -> None:
    """Every (plan, severity) pair must have a tier-2 default.

    Tier-1 agreements are partial by nature -- Northstar states P1/P2/P3 but says
    nothing about other plans. If a default were missing, a ticket would resolve
    to `indeterminate` for a reason nobody intended.
    """
    have = {
        (r.when.get("plan", {}).get("eq"), r.when.get("severity", {}).get("eq"))
        for r in book.rules
        if r.domain == "sla" and r.authority_tier == 2
    }
    missing = [
        (plan, severity)
        for plan in ("Enterprise", "Growth", "Standard")
        for severity in ("P1", "P2", "P3")
        if (plan, severity) not in have
    ]
    if missing:
        raise RulebookError(f"sla rules missing tier-2 defaults for: {missing}")


@lru_cache(maxsize=1)
def load(path: Path | None = None) -> Rulebook:
    book = parse(yaml.safe_load((path or config.RULEBOOK_PATH).read_text()))
    validate(book)
    return book
