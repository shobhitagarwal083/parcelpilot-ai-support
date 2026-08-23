"""Generic precedence resolution. Knows nothing about ParcelPilot specifically.

The order of operations matters, and step 2 is the subtle one:

  1. filter candidates by domain, in-force status, effective dates, account scope
  2. collect `replaces` from every APPLICABLE rule -- whether or not it matches --
     and drop those rules from winner selection
  3. sort what remains by authority tier; the first match wins
  4. record everything else: rules that matched and lost, and replaced rules that
     would have matched

Step 2 is what defeats the quietest trap in the pack. A LumenWorks pickup three
hours late does not match their tier-1 rule (which needs four), so without it the
resolution falls through to the SOP default and returns an eligible INR 240 that
looks entirely reasonable. It is the single highest-risk behaviour in the build.

Step 4 exists because discarding a superseded rule silently is the same failure
as never retrieving it. A replaced rule is disclosed, not forgotten (D-15).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ingest.documents import load_manifest
from app.knowledge.loader import Rule, Rulebook, load
from app.policy.types import Citation, Override


@dataclass(frozen=True)
class Resolution:
    """What the rules say, before any domain interpretation."""

    winner: Rule | None
    outranked: tuple[Rule, ...] = ()
    replaced: tuple[Rule, ...] = ()
    conflicts: tuple[Rule, ...] = ()
    #: Every rule that was in force and in scope, matched or not. Kept so an
    #: adapter can cite the agreement that governs an account even when its
    #: conditions are not met -- "not eligible, because your agreement sets the
    #: threshold at four hours" needs the rule that did not fire.
    considered: tuple[Rule, ...] = ()

    @property
    def has_equal_authority_conflict(self) -> bool:
        return bool(self.conflicts)

    @property
    def replacing_rules(self) -> tuple[Rule, ...]:
        """Applicable rules that displaced something, whether or not they matched."""
        return tuple(r for r in self.considered if r.replaces)


def resolve(
    domain: str,
    facts: dict[str, Any],
    *,
    account_id: str | None,
    as_of: datetime,
    book: Rulebook | None = None,
) -> Resolution:
    book = book or load()

    candidates = [
        rule
        for rule in book.rules_for(domain)
        if rule.is_in_force(as_of) and rule.applies_to_account(account_id)
    ]

    superseded_ids: set[str] = set()
    for rule in candidates:
        superseded_ids.update(rule.replaces)

    active = [r for r in candidates if r.id not in superseded_ids]
    suppressed = [r for r in candidates if r.id in superseded_ids]

    matched = sorted(
        (r for r in active if r.matches(facts)),
        key=lambda r: (r.authority_tier, r.id),
    )
    would_have_matched = tuple(r for r in suppressed if r.matches(facts))

    if not matched:
        return Resolution(winner=None, replaced=would_have_matched, considered=tuple(candidates))

    winner, rest = matched[0], tuple(matched[1:])
    conflicts = tuple(
        r
        for r in rest
        if r.authority_tier == winner.authority_tier and not r.prescribes_the_same_as(winner)
    )
    return Resolution(
        winner=winner,
        outranked=rest,
        replaced=would_have_matched,
        conflicts=conflicts,
        considered=tuple(candidates),
    )


# ---------------------------------------------------------------- presentation


def _doc_title(doc_id: str) -> str:
    meta = load_manifest().get(doc_id)
    return meta.title if meta else doc_id


def _short(rule: Rule) -> str:
    return f"{_doc_title(rule.source.doc)} §{rule.source.section.split('.')[0]}"


def citation_for(rule: Rule) -> Citation:
    return Citation(
        doc_id=rule.source.doc,
        doc_title=_doc_title(rule.source.doc),
        section=rule.source.section,
        quote=" ".join(rule.source.quote.split()),
        authority_tier=rule.authority_tier,
    )


def citation_for_constraint(constraint) -> Citation:
    return Citation(
        doc_id=constraint.source.doc,
        doc_title=_doc_title(constraint.source.doc),
        section=constraint.source.section,
        quote=" ".join(constraint.source.quote.split()),
        authority_tier=constraint.authority_tier,
    )


def citations_for(resolution: Resolution) -> list[Citation]:
    """The winner first, then everything it displaced.

    Losing rules are cited too. An answer that shows only its winning source
    cannot be checked; one that shows what it set aside, and why, can be.
    """
    rules = []
    if resolution.winner:
        rules.append(resolution.winner)
    rules.extend(resolution.outranked)
    rules.extend(resolution.replaced)

    seen: set[str] = set()
    citations = []
    for rule in rules:
        key = f"{rule.source.doc}#{rule.source.section}"
        if key in seen:
            continue
        seen.add(key)
        citations.append(citation_for(rule))
    return citations


def overrides_for(resolution: Resolution) -> list[Override]:
    if resolution.winner is None:
        winner_label = "no applicable rule"
        return [
            Override(
                winning_rule_id="",
                overridden_rule_id=rule.id,
                kind="replaced",
                explanation=(
                    f"{_short(rule)} would have applied, but a customer agreement replaces it "
                    f"for this account, and no replacement rule matches these facts. "
                    f"Result: {winner_label}."
                ),
            )
            for rule in resolution.replaced
        ]

    winner = resolution.winner
    overrides = []

    for rule in resolution.replaced:
        overrides.append(
            Override(
                winning_rule_id=winner.id,
                overridden_rule_id=rule.id,
                kind="replaced",
                explanation=f"{_short(winner)} replaces {_short(rule)} for this account.",
            )
        )

    for rule in resolution.outranked:
        if rule.prescribes_the_same_as(winner):
            explanation = (
                f"{_short(rule)} states the same thing; {_short(winner)} is cited as the "
                f"governing source because it carries higher authority."
            )
        else:
            explanation = (
                f"{_short(winner)} (tier {winner.authority_tier}) outranks "
                f"{_short(rule)} (tier {rule.authority_tier})."
            )
        overrides.append(
            Override(
                winning_rule_id=winner.id,
                overridden_rule_id=rule.id,
                kind="outranked",
                explanation=explanation,
            )
        )

    return overrides


def conflict_reason(resolution: Resolution) -> str | None:
    if not resolution.conflicts:
        return None
    names = ", ".join(_short(r) for r in resolution.conflicts)
    return (
        f"Conflicting rules at equal authority: {_short(resolution.winner)} and {names} "
        f"prescribe different outcomes. A human decides rather than the system picking one."
    )


def contradictions_for(
    domain: str,
    decision_facts: dict[str, Any],
    history,
    *,
    book: Rulebook | None = None,
    topic: str | None = None,
):
    """Tier-4 history that disagrees with the winning rule.

    Never suppressed, and never followed. The useful behaviour is to say a
    previous answer was wrong -- a human reading the same ticket history would
    otherwise repeat it.

    Matched on the decision's own output rather than on ticket IDs, so the check
    generalises: any account whose agreement waives the fee catches any recorded
    resolution claiming one was charged.
    """
    from app.policy.types import Contradiction

    book = book or load()
    found = []
    for check in book.contradiction_checks:
        if check.domain != domain:
            continue
        if topic is not None and check.topic not in (None, topic):
            continue
        for ticket in history:
            recorded = ticket.get("historical_resolution")
            if not recorded:
                continue
            if check.flags(recorded, decision_facts):
                found.append(
                    Contradiction(
                        ticket_id=ticket["ticket_id"],
                        recorded_resolution=recorded,
                        why_wrong=check.why_wrong,
                    )
                )
    return found
