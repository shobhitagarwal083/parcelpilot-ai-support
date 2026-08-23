# Product note

## The problem chosen: proactive issue detection

I built both additional problems, weighted toward **proactive detection**.
Problem 2 (conflicting sources) was forced by the minimum requirements anyway —
the headline example question *is* a precedence conflict. So treating it as a
separate deliverable would have been double-counting.

Proactive detection is where the visible differentiation is, and it costs one
extra view because it reuses the same rule engine the reactive path already
needs.

### Why it matters more than it first appears

Support systems are structurally reactive: they answer questions that get asked.
The expensive failures are the ones nobody asks about.

The clearest case in this dataset is **ORD-2002**. The pickup is 4 hours 30
minutes past its window. Carrier fault is accepted. The customer is not at
fault. LumenWorks' agreement entitles them to a flat ₹300 credit.

**No ticket exists.** The customer has not noticed, so nobody asked, so no
reactive system on earth surfaces it — however good its retrieval, however
capable its model. It is invisible by construction.

It is also the case a support organisation most wants found. A customer who
later discovers they were silently owed money under a signed agreement is a
harder conversation than the ₹300.

### What the triage board finds

Nine signals across the fixed snapshot, ranked by urgency, each carrying the
evidence and the rule behind it:

| Rank | Detector | Finds |
|---|---|---|
| critical | `security_incident` | TKT-505 — suspected credential exposure, P1 regardless of how it was reported |
| critical | `sla_breached` | TKT-501 and TKT-505 past their first-response targets |
| high | `known_issue_cluster` | 2 tickets matching KI-208, a known defect rather than a plan limit |
| high | `silent_credit_eligible` | **ORD-2002 — ₹300 owed, nobody asked** |
| medium | `stalled_cancellation` | 3 cancellations requested but still BOOKED |
| medium | `carrier_concentration` | SwiftShip in 3 of 6 orders |
| low | `contradicted_guidance` | TKT-450 and TKT-451 closed with advice the current rules contradict |

Two design points worth naming:

**Every signal shows its reasoning.** Each card expands into the same `Decision`
object the chat path produces — outcome, facts used, citations, overrides. A
detection a manager cannot audit is a detection they will learn to ignore.

**`contradicted_guidance` is the self-critical one.** It scans closed tickets for
advice that current rules contradict, and finds two — including TKT-450, which
told Northstar a ₹250 fee applied when their agreement waives it. That is the
system reporting its own organisation's past mistakes, which is exactly the kind
of thing that stays invisible until a customer escalates.

---

## What I would build next, in order

**1. Write-back to the ticket system.** Today an approved action writes an audit
row. In production it must create the credit note, update the ticket, and notify
the customer — with idempotency, because the failure mode of a retried credit is
paying twice. Highest value and highest risk, which is why it is first and why
it needs an integration test suite rather than a demo.

**2. Rule authoring for non-engineers.** The rulebook is already versioned YAML
with citations, which was chosen partly for this. The missing piece is a review
UI: propose a change, see which past decisions would flip, approve. Support
policy changes monthly; an engineer in that loop makes the system decay.

**3. Decision replay.** Every `Decision` is a pure function of facts, rulebook
version and snapshot time. Persisting those inputs makes "why did we tell them
₹0 in August?" answerable exactly, at the version that applied then. Cheap to
add now, near-impossible to retrofit once decisions are only in logs.

**4. Confidence-aware routing.** The system knows when a required fact is
unknown. It does not yet know when a question is outside its rulebook entirely —
it will retrieve *something* and answer. Detecting "no rule covers this" and
escalating is a different signal from "a fact is missing".

**5. Coverage telemetry on the rulebook.** Which rules fire, which never do,
which questions reach no rule. That tells ParcelPilot where policy is ambiguous
in practice, not in theory — and it is the input that makes item 2 worth having.

---

## What I deliberately left out

**Real authentication.** The brief permits mocking it, so the persona switcher
names a principal and the server resolves it. What is *not* mocked is everything
downstream — scope enforcement, capability checks, the disclosure boundary. I
would rather ship a real access-control layer behind a fake login than a real
login in front of a fake one.

**Multi-turn clarification flows.** The agent asks when a fact is missing, but it
does not manage a structured slot-filling conversation. At this dataset's size
the extra machinery would have been scaffolding around a problem that does not
exist yet.

**Embeddings and a vector store.** 24 chunks. BM25 is deterministic, needs no
model call, and the queries are keyword-shaped. Adding a vector index would have
been a dependency, a build step and a source of non-determinism, bought nothing
measurable, and looked more impressive than it performed.

**A general policy DSL.** It would express rules this pack does not contain. The
YAML covers what exists and stays readable by the people who own the policy.

**Streaming the triage board.** It is a scan over a fixed snapshot; it returns in
milliseconds. Real-time detection matters when the data is live, and that is a
different system with different failure modes.

**Aggregate credit caps.** LumenWorks' agreement mentions a monthly cap. The pack
provides no ledger of credits already issued, so the system discloses the cap as
a caveat rather than pretending to enforce it. Inventing a number would have been
worse than admitting the gap.

---

## The one metric

> **Proportion of answers that cite the rule a human expert would have cited.**

Not deflection rate, not resolution time, not CSAT.

Those three are the usual choices and each rewards the wrong behaviour here. A
system can deflect brilliantly while telling Northstar they owe ₹250. Speed and
satisfaction both improve when an agent confidently says yes — which is exactly
the failure this dataset is built to punish. TKT-450 was a fast, satisfying,
deflected, *wrong* answer, and it is now generating a second contact.

The proposed metric is measurable without a survey: sample answers, have a
support lead mark the rule they would have applied, compare to
`Decision.winning_rule_id`. It is auditable after the fact because every
decision records its inputs. And it fails loudly in the case that matters — an
answer citing the SOP where the agreement governs is wrong even when the
customer is delighted.

**The leading indicator to pair with it:** the rate of `indeterminate` outcomes.
Rising means the rulebook has gaps or facts are not being captured upstream.
Falling to zero means the system has started guessing. Neither extreme is good,
which is what makes it informative.
