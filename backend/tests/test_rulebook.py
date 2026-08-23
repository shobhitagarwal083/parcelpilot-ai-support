"""The rulebook's invariants -- and the claim that it is not a lookup table.

The brief warns directly: "we may test your system using other records and
questions from the same source pack. Your implementation should therefore load
and reason over the supplied data rather than hard-coding the example IDs or
answers."

Encoding "Northstar waives the fee" as a rule *looks* like hardcoding. What
makes it not is that rules reference fields and never IDs, with account scoping
in `applies_to` exactly mirroring how the source document scopes itself. The
last test in this file is the proof: a fictional fifth account with novel
contract terms works with no code change at all.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from app import config
from app.ingest.documents import DocumentMeta, load_manifest
from app.knowledge.loader import RulebookError, load, parse, validate
from app.policy import engine


@pytest.fixture
def raw():
    return yaml.safe_load(config.RULEBOOK_PATH.read_text())


def test_no_condition_references_a_specific_record():
    """The invariant that separates a rulebook from hardcoded answers."""
    for rule in load().rules:
        assert not any(token in str(rule.when) for token in ("ORD-", "TKT-", "ACCT-")), rule.id


def test_a_leaked_record_id_in_a_condition_is_rejected(raw):
    raw = copy.deepcopy(raw)
    raw["rules"][0]["when"]["order_id"] = {"eq": "ORD-1001"}

    with pytest.raises(RulebookError, match="references specific records"):
        validate(parse(raw))


def test_account_scoping_is_allowed_because_the_documents_scope_themselves():
    northstar = load().rule("cancel.northstar.waiver")
    assert northstar.accounts == frozenset({"ACCT-001"})
    assert "ACCT-" not in str(northstar.when)


def test_a_tier_one_rule_may_not_apply_to_every_account(raw):
    """A tier-1 rule comes from a signed agreement. A global one would let one
    customer's negotiated terms outrank policy for everybody."""
    raw = copy.deepcopy(raw)
    for rule in raw["rules"]:
        if rule["id"] == "credit.lumenworks.fixed":
            rule["applies_to"] = {"accounts": "*"}

    with pytest.raises(RulebookError, match="scoped to specific accounts"):
        validate(parse(raw))


def test_a_replaces_target_that_does_not_exist_is_rejected(raw):
    raw = copy.deepcopy(raw)
    for rule in raw["rules"]:
        if rule["id"] == "credit.lumenworks.fixed":
            rule["replaces"] = ["credit.sop_v4.does_not_exist"]

    with pytest.raises(RulebookError, match="replaces unknown rule"):
        validate(parse(raw))


def test_a_rule_sourced_from_a_deprecated_document_is_rejected(raw):
    """Policy v2 has a complete, plausible SLA table. It must not be able to
    back a current rule by accident."""
    raw = copy.deepcopy(raw)
    raw["rules"][0]["source"]["doc"] = "02_Support_Policy_v2_DEPRECATED"

    with pytest.raises(RulebookError, match="DEPRECATED"):
        validate(parse(raw))


def test_a_missing_tier_two_sla_default_is_rejected(raw):
    """Agreements are partial by nature -- Northstar says nothing about other
    plans. A missing default would silently produce `indeterminate`."""
    raw = copy.deepcopy(raw)
    raw["rules"] = [r for r in raw["rules"] if r["id"] != "sla.v3.standard.p2"]

    with pytest.raises(RulebookError, match="missing tier-2 defaults"):
        validate(parse(raw))


def test_every_entry_carries_provenance():
    book = load()
    sourced = (
        list(book.rules)
        + list(book.constraints)
        + list(book.severity_rules)
        + list(book.known_issues)
        + list(book.product_facts)
    )
    for entry in sourced:
        assert entry.source.doc, entry.id
        assert entry.source.section, entry.id
        assert entry.source.quote, entry.id


def test_replaces_is_only_used_where_a_source_authorises_it():
    """The phase 02a review dropped two uses that were inferred from contract
    prose rather than stated in it. Every survivor is backed by explicit
    language -- either the rule's own clause says "replace", or the rule it
    displaces carves out the exception itself."""
    book = load()
    replacing = [r for r in book.rules if r.replaces]
    assert {r.id for r in replacing} == {
        "cancel.northstar.waiver",
        "credit.lumenworks.fixed",
        "sla.northstar.p1",
    }

    for rule in replacing:
        own_clause_says_so = "replace" in rule.source.quote.lower()
        displaced_carves_it_out = any(
            "unless a customer agreement" in book.rule(target).source.quote.lower()
            for target in rule.replaces
        )
        assert own_clause_says_so or displaced_carves_it_out, rule.id


def test_a_fictional_fifth_account_needs_no_code_change(raw, now):
    """The claim in D-01, tested rather than asserted.

    A reviewer adds a new customer whose agreement waives the cancellation fee
    only inside two hours of booking -- a threshold that appears nowhere in the
    supplied pack -- by editing YAML. No Python changes.
    """
    raw = copy.deepcopy(raw)
    raw["rules"].append(
        {
            "id": "cancel.orionfreight.short_waiver",
            "domain": "cancellation",
            "authority_tier": 1,
            "status": "ACTIVE",
            "effective_from": "2026-02-01",
            "effective_to": "2027-01-31",
            "applies_to": {"accounts": ["ACCT-999"]},
            "when": {"order_status": {"eq": "BOOKED"}, "minutes_since_booking": {"lte": 120}},
            "then": {"outcome": "allowed", "fee_inr": 0},
            "replaces": ["cancel.sop_v4.booked_after_grace"],
            "source": {
                "doc": "07_OrionFreight_Agreement",
                "section": "2. Cancellation",
                "quote": "OrionFreight may cancel a BOOKED shipment within two hours of "
                "booking with no fee. This clause replaces the standard fee.",
            },
        }
    )

    manifest = dict(load_manifest())
    manifest["07_OrionFreight_Agreement"] = DocumentMeta(
        doc_id="07_OrionFreight_Agreement",
        title="ParcelPilot - OrionFreight Agreement",
        authority_tier=1,
        status="ACTIVE",
        effective_from="2026-02-01",
        effective_to="2027-01-31",
        scope="account:ACCT-999",
    )

    book = parse(raw)
    validate(book, manifest=manifest)

    inside = engine.resolve(
        "cancellation",
        {"order_status": "BOOKED", "minutes_since_booking": 90},
        account_id="ACCT-999",
        as_of=now,
        book=book,
    )
    assert inside.winner.id == "cancel.orionfreight.short_waiver"
    assert inside.winner.then["fee_inr"] == 0

    # Past their negotiated window, and the SOP rule they replaced is gone -- so
    # only the grace-window rule remains, and it does not match either.
    outside = engine.resolve(
        "cancellation",
        {"order_status": "BOOKED", "minutes_since_booking": 300},
        account_id="ACCT-999",
        as_of=now,
        book=book,
    )
    assert outside.winner is None
    assert [r.id for r in outside.replaced] == ["cancel.sop_v4.booked_after_grace"]

    # And nothing about the existing accounts moved.
    unchanged = engine.resolve(
        "cancellation",
        {"order_status": "BOOKED", "minutes_since_booking": 300},
        account_id="ACCT-002",
        as_of=now,
        book=book,
    )
    assert unchanged.winner.id == "cancel.sop_v4.booked_after_grace"
    assert unchanged.winner.then["fee_inr"] == 250
