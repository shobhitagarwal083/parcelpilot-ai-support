# ParcelPilot AI Support

An AI support system for ParcelPilot, a B2B logistics platform, built against a
deliberately imperfect source pack: policies that have been superseded, customer
agreements that override general policy, and historical tickets whose recorded
resolutions are wrong.

## The idea in one paragraph

Authority is a property of every piece of knowledge in the system, carried from
ingestion through retrieval into a deterministic decision. **The model
orchestrates and explains; it never arbitrates between conflicting sources and
never does arithmetic.** Cancellation fees, service-credit amounts and SLA
targets are resolved by a rule engine over versioned YAML, and handed to the
model as a finished `Decision` object to narrate.

Status: **in progress.** See `docs/` for the architecture and product notes.

## Setup

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and add an OpenRouter key. Not needed for the
policy engine or its tests.

## Layout

```
backend/app/
  config.py        pinned snapshot time, paths, provider config
  ingest/          PDFs to tagged chunks; xlsx to SQLite
  knowledge/       documents.yaml (authority manifest) + rulebook.yaml
  policy/          calendar, generic precedence engine, domain adapters
  retrieval/       authority-weighted lexical search
  auth/            Principal and mock personas
  repo/            scoped data access
  agent/           provider boundary, streaming tool loop
  ops/             proactive issue detection
  api/             SSE chat, actions, signals
data/source/       the seven supplied files, unmodified
```
