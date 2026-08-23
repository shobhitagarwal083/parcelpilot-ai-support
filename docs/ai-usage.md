# AI tool usage

## What I used

**Claude Code (Opus 5)** — effectively all of it: planning, implementation,
tests, this documentation. Run from the terminal with the repository as context.

**Google AI Studio, Groq and OpenRouter** — as the runtime providers being
evaluated, not as authoring tools.

No other assistants, no code generation services, no autocomplete beyond what
Claude Code produced.

## How I used it

**Analysis before code.** The first several hours produced no application code
at all — instead a set of working notes: an audit of every row and document in
the pack, a catalogue of the twelve conflicts the data is built around, an
architecture sketch, a phased plan, and a decisions log recording each choice
with its reasoning and the alternatives rejected.

That ordering mattered more than any individual implementation step. The central
architectural decision — that the model routes and narrates but never decides —
came directly from auditing the data and noticing that three sources give three
different answers about ORD-1001, so the correct answer requires a precedence
rule rather than better reading comprehension. Writing code first would have
produced a retrieval-and-summarise system, and the traps would have surfaced
much later.

**Tests as the specification.** Each of the twelve traps was written as a failing
test before the code that defeats it. 216 tests now, and the ones that matter
assert on rule precedence rather than on strings.

**A decisions log with reasons.** Twenty entries, each recording what was chosen,
what was rejected, and why. Several were later overturned by measurement, and the
log records the reversal rather than quietly editing history.

## Where it was wrong

This is the part worth reading, and the reason I kept a log at all.

**It wrote model lists from memory, twice, and both were wrong.** The Gemini
configuration named a model that returns 404 and another that never emits a tool
call. Replacing it, the Groq configuration named two Llama models that are not in
that catalogue at all. Both lists read as entirely plausible. Neither survived
contact with the live `/models` endpoint. The fix was procedural and is now
written into the config as a comment: read the catalogue, probe each candidate,
then measure — never write a model list from memory.

**It shipped a measurement harness with a bug that inverted its own conclusion.**
The bake-off reported `chain 6/6 (100%)` and recommended a primary model — while
every one of those six runs had failed. It counted the "tool was called" event
and never checked whether the turn survived. That is the precise failure the
bake-off exists to catch, sitting inside the bake-off. It surfaced only because
the correctness column read `right 0/6`, which was obviously wrong, and because
the harness could dump full answers instead of just counts.

**It introduced a disclosure leak while building the interface.** A known-issue
caveat ships in two registers — one naming an internal tracker ID, one safe for
customers. Both were being serialised into a customer's browser, with only the
client choosing which to render. That puts the secret in the response body and
the enforcement in the UI, which is the same mistake as enforcing access control
in the prompt. Now redacted at the tool boundary, verified on the wire.

**It failed to apply its own measurement.** The bake-off measured
`gemini-3.5-flash` at 1/4 chain completion against flash-lite's 5/5. The config
kept `gemini-3.5-flash` as primary anyway, on nothing but its higher version
number, until a later check caught it.

## What that implies

The pattern across all four is the same: **the model is reliable at producing
plausible structure and unreliable at knowing whether the structure is true.**
Every one of those errors looked correct. None survived being checked against
something real — a live API, a full dump, a byte count on the wire.

So the practices that actually did the work were the unglamorous ones:

- Query the live system rather than trusting recall about it
- Make the harness dump raw evidence, not just aggregates — every serious bug
  here was caught by reading answers rather than counts
- Treat a suspicious number as a bug in the measurement until proven otherwise
- Verify security properties by observing the wire, not by reading the code
- Keep a decision log, so a conclusion that was later overturned stays visible

The architecture and the tooling ended up making the same argument. A model is
good at routing and narrating and bad at being authoritative — so the system
gives it exactly that job and hands it finished decisions, and I checked its
work the same way.
