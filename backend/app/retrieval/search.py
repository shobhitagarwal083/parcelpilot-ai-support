"""Authority-weighted lexical search over the document chunks.

BM25 rather than embeddings, deliberately. Six one-page documents, roughly 8,000
words: an embedding index adds a dependency, a build step and a deployment cost
to solve a recall problem that does not exist at this scale. The interesting
part of retrieval here is ranking by authority and filtering by scope, and
neither is something embeddings help with. At ParcelPilot's real scale --
thousands of agreements -- hybrid retrieval becomes correct, and the chunk
metadata is already shaped for it.

**Ranking is a hint, not the precedence mechanism.** The authority weight below
nudges a signed agreement above a general policy in a result list, so a human
reading the citations sees the governing document first. It decides nothing.
Precedence is resolved in `policy/engine.py`, deterministically, from rule tiers
-- because a retrieval score that happened to rank the SOP first must not be
able to change what a customer is owed.

Two filters are enforcement rather than ranking, and both run before scoring:

  scope       an agreement chunk carries `scope: account:ACCT-001`, so a
              LumenWorks user searching "fee waiver" cannot surface Northstar's
              clause. A leaked citation is the same breach as a leaked row.
  deprecated  Policy v2 is excluded at index level, not by prompt. It contains a
              complete, plausible SLA table, and its "DO NOT USE" line is one
              sentence a chunk-level retriever will happily separate from it.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.auth.principals import READ_DEPRECATED, Principal
from app.auth.scope import require_capability
from app.retrieval.store import load_chunks

_TOKEN = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be but by can do does for from has have how i if in is it its
    my of on or our that the their they this to was we what when where which who will
    with would you your""".split()
)

#: Applied to the lexical score so a signed agreement surfaces above a general
#: policy that scores the same. Presentation only -- see the module docstring.
AUTHORITY_WEIGHT = {1: 1.5, 2: 1.25, 3: 1.0, 4: 0.6}
DEPRECATED_WEIGHT = 0.4

DEPRECATED_WARNING = (
    "This document is DEPRECATED and superseded. It is shown because it was asked "
    "for explicitly, and must not be used to answer a current request."
)

_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass(frozen=True)
class Hit:
    chunk: dict[str, Any]
    score: float
    lexical_score: float
    authority_weight: float
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.chunk["doc_id"],
            "doc_title": self.chunk["doc_title"],
            "section": self.chunk["section"],
            "text": self.chunk["text"],
            "authority_tier": self.chunk["authority_tier"],
            "status": self.chunk["status"],
            "effective_from": self.chunk["effective_from"],
            "scope": self.chunk["scope"],
            "score": round(self.score, 4),
            "warning": self.warning,
        }


def _visible_to(chunk: dict[str, Any], principal: Principal) -> bool:
    scope = chunk["scope"]
    if scope == "global":
        return True
    if scope.startswith("account:"):
        return principal.can_access(scope.split(":", 1)[1])
    return False


def search(
    principal: Principal,
    query: str,
    *,
    limit: int = 5,
    include_deprecated: bool = False,
) -> list[Hit]:
    if include_deprecated:
        require_capability(principal, READ_DEPRECATED, action="search superseded documents")

    corpus = load_chunks()
    terms = tokenize(query)
    if not terms:
        return []

    tokenised = [tokenize(chunk["text"] + " " + chunk["section"]) for chunk in corpus]
    lengths = [len(t) for t in tokenised]
    average_length = sum(lengths) / len(lengths) if lengths else 0.0
    total = len(corpus)

    document_frequency = Counter()
    for tokens in tokenised:
        document_frequency.update(set(tokens))

    hits: list[Hit] = []
    for chunk, tokens, length in zip(corpus, tokenised, lengths, strict=True):
        if chunk["status"] == "DEPRECATED" and not include_deprecated:
            continue
        if not _visible_to(chunk, principal):
            continue

        counts = Counter(tokens)
        lexical = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            appearances = document_frequency[term]
            idf = math.log(1 + (total - appearances + 0.5) / (appearances + 0.5))
            denominator = frequency + _K1 * (1 - _B + _B * length / (average_length or 1))
            lexical += idf * (frequency * (_K1 + 1)) / denominator

        if lexical <= 0:
            continue

        deprecated = chunk["status"] == "DEPRECATED"
        weight = DEPRECATED_WEIGHT if deprecated else AUTHORITY_WEIGHT[chunk["authority_tier"]]
        hits.append(
            Hit(
                chunk=chunk,
                score=lexical * weight,
                lexical_score=lexical,
                authority_weight=weight,
                warning=DEPRECATED_WARNING if deprecated else None,
            )
        )

    hits.sort(key=lambda h: (-h.score, h.chunk["authority_tier"], h.chunk["chunk_id"]))
    return hits[:limit]
