# ParcelPilot AI Support

An AI support system for a logistics company, built around one idea:

> **The model routes and narrates. It never decides.**

Every rupee amount, eligibility verdict, deadline and breach in this system is
computed by a deterministic rule engine and handed to the model already
finished. The model chooses which tool to call and writes the sentence. It never
arbitrates between a contract and a policy, never computes a fee, and cannot
execute anything.

That constraint is what makes the rest of the design possible — the answers are
reproducible, the reasoning is auditable, and swapping the model provider is a
change to one file.

---

## Why that matters here

The source pack is deliberately contradictory. A customer asks:

> *"Can I cancel ORD-1001 without a cancellation fee?"*

- The **SOP** says ₹250 after a 30-minute grace window.
- **Northstar's agreement** waives the fee entirely.
- A **past ticket** (TKT-450) told this same customer the ₹250 applied — and was wrong.

The correct answer is **₹0**, citing the agreement, while disclosing that the SOP
would have charged ₹250 and that the earlier ticket was mistaken. A
retrieval-and-summarise system averages those three sources and produces a
confident, wrong number.

Ask the *same* question about a service credit and the answer changes **per
account**: LumenWorks' agreement sets a 4-hour threshold where the SOP sets 2.
Three hours late is a ₹300 credit for one account and nothing for another.

Twelve such conflicts are catalogued and each is pinned by a test.

---

## Quick start

**Requirements:** Python 3.11+, Node 18+, and a free
[Google AI Studio key](https://aistudio.google.com/apikey).

```bash
git clone <repo-url> && cd calquity
```

```bash
cp .env.example .env    # then add GEMINI_API_KEY
```

```bash
cd backend && python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
```

Build the search index and database from the source workbook and PDFs:

```bash
cd backend && ./.venv/bin/python -m app.ingest
```

Run the API:

```bash
cd backend && ./.venv/bin/python -m uvicorn app.api.main:app --port 8000
```

Run the interface, in a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>.

### Tests

216 tests, no API key required — the rule engine, retrieval and access control
contain no model call at all.

```bash
cd backend && ./.venv/bin/python -m pytest
```

---

## What to try

The persona switcher in the left rail is the demo control. The same question
gets a different answer depending on who is asking, and the boundary is enforced
in the data layer rather than by asking the model nicely.

| As | Ask | What it shows |
|---|---|---|
| Northstar | *Can I cancel ORD-1001 without a cancellation fee?* | ₹0. Agreement outranks SOP; TKT-450 flagged as wrong |
| LumenWorks | *A pickup is three hours late due to carrier fault. Service credit?* | Not eligible — their agreement's 4-hour threshold replaces the SOP's 2 |
| LumenWorks | *Can I cancel ORD-1001?* | Denied. Different account, and the refusal is visible in the trace |
| Rohit (agent) | *Prepare a goodwill credit for TKT-501* | Proposes ₹1,500 — Confirm **disabled**, above the SOP's ₹1,000 threshold |
| Priya (manager) | Triage board | Approves what Rohit could not, plus 9 detected issues |

The board's best card is **ORD-2002**: 4h30m past its pickup window, carrier
fault accepted, ₹300 owed under LumenWorks' agreement — and no ticket exists.
Nobody asked. A purely reactive support system never finds it.

---

## Documentation

| | |
|---|---|
| [Architecture note](docs/architecture.md) | Agent and tool design, document and structured-data handling, conflict resolution, trade-offs |
| [Product note](docs/product.md) | Problem chosen, prioritised roadmap, deliberate omissions, the metric |
| [AI tool usage](docs/ai-usage.md) | Which tools, how they were used, what they got wrong |

---

## How it fits together

```
              ┌──────────────────────────────────────────┐
  question →  │  agent loop      model routes + narrates │
              └────────────┬─────────────────────────────┘
                           │ tool calls
              ┌────────────▼─────────────────────────────┐
              │  tool layer      principal injected here │
              │                  access control enforced │
              └────────────┬─────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
   rule engine        repositories        retrieval
   (decides)          (scoped rows)       (BM25, tiered)
        │
        ▼
   Decision  ──  outcome · amount · citations · overrides · caveats
```

A `Decision` is the unit that crosses the boundary. The model receives one
already computed and describes it; the interface renders citations and overrides
out of the same object rather than out of the model's prose.

### Layout

```
backend/app/
  knowledge/     rulebook.yaml — every rule, threshold and amount, versioned
  policy/        the rule engine, calendar, and one adapter per domain
  repo/          repositories; every entry point takes a principal first
  auth/          principals, scope enforcement, disclosure redaction
  agent/         provider boundary, streaming loop, 11 tools
  ops/           proactive detectors
  api/           FastAPI routes, SSE chat endpoint
frontend/src/    React interface — persona rail, tool trace, decision cards
```

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `MODEL_PROVIDER` | `google` | `google`, `groq` or `openrouter` |
| `GEMINI_API_KEY` | — | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GROQ_API_KEY` | — | Optional. Used as failover if set |
| `OPENROUTER_API_KEY` | — | Optional. Used as failover if set |

All three speak the OpenAI chat-completions shape, so one adapter reaches any of
them. Any key you set also joins the failover chain: free quota is metered per
project, so a second *vendor* is the only fallback that survives the first being
exhausted.

**"Now" is pinned to Sunday 16 August 2026, 11:00 IST** — the snapshot the source
data describes. It is injected, never read from the system clock, so every SLA
and deadline answer is identical today and in 2030. A test greps the tree and
fails if any module outside `config.py` calls `datetime.now()`.

The Sunday is load-bearing: three of the five open tickets are under
business-hours coverage, so their response clocks have not started. That is a
different answer from "within target", and the tools distinguish them.

---

## Deployment

One container: the API serves the built frontend, so there is one origin, no
CORS, and one deploy that cannot drift out of step with itself.

The deciding reason is streaming. The interface shows tool calls *as they
happen*, and every hop between browser and process is somewhere an SSE stream
can be buffered — which does not error, it just quietly turns a live trace into
one delayed dump.

```bash
docker build -t parcelpilot . && docker run -p 8080:8080 -e GEMINI_API_KEY=... parcelpilot
```

Ingest runs at image build time, so a cold start serves immediately rather than
parsing six PDFs first, and a malformed source pack fails the build instead of
the first request. `PORT` is read at runtime for hosts that inject one.

[`render.yaml`](render.yaml) and [`fly.toml`](fly.toml) are both committed. Fly
was the original choice for its warm machines, and its free trial turned out to
be two machine-hours or seven days — a link that expires before it is reviewed
is worse than a slow one. Render's free tier needs no card and renews monthly,
at the cost of spinning down after 15 minutes idle and taking about a minute to
wake.

## Notes for reviewers

**The hosted link may take up to a minute to wake.** It runs on a free tier that
spins down when idle. The demo video opens on a loaded app for that reason.

**The demo runs on a free-tier model and may rate-limit.** If it does, the
interface says so plainly rather than failing silently. The decisions themselves
are computed by the rule engine, so nothing on screen is wrong when the model is
unavailable — there is simply no narrator.

**Auth is mocked, as the brief permits.** The client names a persona and the
server resolves it. What is *not* mocked is everything after that: the principal
is injected server-side, never accepted as a parameter, and every repository
entry point takes it as its first argument. A test asserts that last property
across the whole repository layer, so there is no code path that reaches data
without one.
