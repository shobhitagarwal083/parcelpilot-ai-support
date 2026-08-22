"""Retrieval -- trap T5, and the citation half of requirement 2.

Two of these are enforcement rather than ranking. An agreement clause leaking
through a citation is the same breach as a leaked row, and Policy v2 is excluded
at index level rather than by asking the model nicely: it contains a complete,
plausible SLA table, and its "DO NOT USE FOR CURRENT REQUESTS" line is one
sentence that a chunk-level retriever will happily separate from it.
"""

from __future__ import annotations

import pytest

from app.auth import principals
from app.auth.scope import AccessDenied
from app.retrieval.search import search
from app.retrieval.store import load_chunks


@pytest.fixture
def northstar():
    return principals.get("cust-northstar")


@pytest.fixture
def lumenworks():
    return principals.get("cust-lumenworks")


@pytest.fixture
def priya():
    return principals.get("staff-priya")


@pytest.fixture
def rohit():
    return principals.get("staff-rohit")


def test_r1_a_default_search_never_returns_a_superseded_document(rohit):
    for query in ["enterprise P1 response target", "support policy", "severity", "1 hour"]:
        hits = search(rohit, query, limit=10)
        assert all(h.chunk["status"] != "DEPRECATED" for h in hits), query


def test_r2_a_manager_can_ask_for_the_old_policy_and_gets_a_warning(priya):
    hits = search(priya, "support policy severity targets", limit=10, include_deprecated=True)
    deprecated = [h for h in hits if h.chunk["status"] == "DEPRECATED"]

    assert deprecated
    assert all("must not be used to answer a current request" in h.warning for h in deprecated)


def test_r3_a_customer_cannot_reach_the_deprecated_index_at_all(northstar):
    with pytest.raises(AccessDenied, match="read:deprecated"):
        search(northstar, "support policy", include_deprecated=True)


def test_r3_an_agent_without_the_capability_cannot_either(rohit):
    with pytest.raises(AccessDenied, match="read:deprecated"):
        search(rohit, "support policy", include_deprecated=True)


def test_a4_one_customer_cannot_surface_anothers_agreement(lumenworks):
    """The citation leak path. LumenWorks searching for a fee waiver must not
    find Northstar's clause, however well it matches."""
    hits = search(lumenworks, "cancellation fee waiver no fee regardless", limit=10)

    assert hits
    assert all(h.chunk["doc_id"] != "05_Northstar_Logistics_Enterprise_Agreement" for h in hits)
    assert any(h.chunk["doc_id"] == "06_LumenWorks_Service_Agreement" for h in hits)


def test_a5_and_the_same_holds_in_the_other_direction(northstar):
    hits = search(northstar, "service credit fixed INR 300 four hours", limit=10)

    assert all(h.chunk["doc_id"] != "06_LumenWorks_Service_Agreement" for h in hits)


def test_internal_users_see_every_agreement(rohit):
    hits = search(rohit, "cancellation fee waiver service credit", limit=20)
    scopes = {h.chunk["scope"] for h in hits}
    assert "account:ACCT-001" in scopes
    assert "account:ACCT-002" in scopes


def test_r4_authority_breaks_a_tie_in_favour_of_the_agreement(northstar):
    """Presentation only. The signed agreement surfaces above the general SOP so
    a human reading the citations sees the governing document first -- but
    nothing here decides what the customer is owed. That is resolved in the
    policy engine, from rule tiers, deterministically."""
    hits = search(northstar, "cancellation fee waiver", limit=5)

    top = hits[0]
    sop = next(h for h in hits if h.chunk["doc_id"].startswith("03_"))

    assert top.chunk["doc_id"] == "05_Northstar_Logistics_Enterprise_Agreement"
    assert top.chunk["authority_tier"] == 1
    # The SOP actually scores higher lexically; authority is what reorders them.
    assert sop.lexical_score > top.lexical_score
    assert top.score > sop.score


def test_r5_every_chunk_carries_its_authority_metadata():
    for chunk in load_chunks():
        assert chunk["authority_tier"] in (1, 2, 3)
        assert chunk["status"] in ("CURRENT", "ACTIVE", "DEPRECATED")
        assert chunk["effective_from"]
        assert chunk["scope"]
        assert chunk["doc_title"]
        assert chunk["section"]


def test_an_empty_or_stopword_only_query_returns_nothing(rohit):
    assert search(rohit, "") == []
    assert search(rohit, "the and of") == []
