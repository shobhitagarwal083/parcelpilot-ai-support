"""Single source of configuration — and the only module permitted to touch a clock.

Every timing answer in this system (SLA breach, cancellation grace window,
pickup lateness) is derived from one pinned instant. If any other module read
the wall clock, every one of those answers would drift silently and only become
visible when someone checked a number by hand.

`tests/test_no_wall_clock.py` greps the tree and fails if `datetime.now(`,
`date.today(` or `time.time(` appears outside this file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ---------------------------------------------------------------- paths

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

# .env lives at the repository root and is gitignored. Loaded without override,
# so a real environment variable always wins over a stale file -- which is what
# the hosted deployment needs, where the key comes from a secret store.
load_dotenv(ROOT_DIR / ".env", override=False)

DATA_DIR = ROOT_DIR / "data"
SOURCE_DIR = DATA_DIR / "source"
INDEX_DIR = DATA_DIR / "index"

CHUNKS_PATH = INDEX_DIR / "chunks.json"
SNAPSHOT_PATH = INDEX_DIR / "snapshot.json"
DB_PATH = DATA_DIR / "parcelpilot.db"

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
DOCUMENTS_MANIFEST = KNOWLEDGE_DIR / "documents.yaml"
RULEBOOK_PATH = KNOWLEDGE_DIR / "rulebook.yaml"

# ---------------------------------------------------------------- time

TZ = ZoneInfo("Asia/Kolkata")

# Transcribed from the `README` sheet of ParcelPilot_Assessment_Data.xlsx.
# Ingest re-parses that cell and asserts it agrees with this constant, so the
# two cannot drift apart unnoticed. The fallback exists so that a fresh clone
# can run the test suite before `python -m app.ingest` has been run.
_PINNED_SNAPSHOT = datetime(2026, 8, 16, 11, 0, tzinfo=TZ)


def _load_snapshot() -> datetime:
    if SNAPSHOT_PATH.exists():
        raw = json.loads(SNAPSHOT_PATH.read_text())["snapshot_at"]
        return datetime.fromisoformat(raw).astimezone(TZ)
    return _PINNED_SNAPSHOT


#: "Now", for the entire system. A Sunday, which is load-bearing: three of the
#: five open tickets are under business-hours coverage and their clocks have
#: therefore not started.
SNAPSHOT_AT = _load_snapshot()

# ---------------------------------------------------------------- business calendar

# Assumptions A1-A3; stated in the README because none of this is in the pack.
BUSINESS_DAYS = frozenset({0, 1, 2, 3, 4})  # Mon-Fri, matching date.weekday()
BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 18
BUSINESS_MINUTES_PER_DAY = (BUSINESS_END_HOUR - BUSINESS_START_HOUR) * 60  # 540

# ---------------------------------------------------------------- model provider

# D-19: Google AI Studio (Gemini), via its OpenAI-compatible endpoint.
# Nothing outside app/agent/provider.py may import a vendor client.
#
# Two providers are described here rather than one, and that is deliberate.
# D-14 argued the provider is swappable because the model never arbitrates
# between sources and never computes a number -- the engine, calendar, rulebook
# and access control contain no model call at all. Keeping both configured turns
# that from a claim into something demonstrable: run the same question through
# each and watch ₹0 and ₹300 come out byte-identical, because neither model
# produced them.
#
# Both speak the OpenAI chat-completions shape, so one adapter reaches both.

PROVIDERS: dict[str, dict[str, object]] = {
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        # Free-tier flash models only, and every one verified to actually emit a
        # tool call before being listed. Two exclusions worth recording:
        #
        #   gemini-2.5-flash      answered in prose 0/4 times when handed a tool
        #                         and told to use it. It fails D-14's hard gate,
        #                         so it cannot drive this agent at any quality.
        #   gemini-3-flash-preview  called the tool 2/4 -- too loose for a chain
        #                         that has to hold across five or six calls.
        #
        # Pro models are excluded by policy, not measurement: they are not free
        # tier. `*-latest` aliases are excluded too -- they drift, and a reviewer
        # running this repo weeks from now should get the model we tested.
        #
        # Ordered. Unlike OpenRouter, Google has no server-side fallback array,
        # so provider.py walks this list itself.
        "models": ["gemini-3.5-flash", "gemini-3.1-flash-lite"],
        "console": "https://aistudio.google.com/apikey",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        # Order set by the phase 05a bake-off, not by published throughput
        # figures: nemotron completed the tool chain 10/10 while GLM was never
        # served at all (`scripts/bakeoff.py`, 2026-08-23).
        "models": ["nvidia/nemotron-3-nano-30b-a3b:free", "z-ai/glm-5.2:free"],
        "console": "https://openrouter.ai/keys",
    },
}

PROVIDER = os.environ.get("MODEL_PROVIDER", "google").strip().lower()

MAX_TOKENS = 4096

# Phase 10 cost ceiling — a public URL in front of an LLM endpoint needs one.
MAX_TURNS_PER_SESSION = 30
MAX_TOOL_CALLS_PER_TURN = 12


def provider_settings(name: str | None = None) -> dict[str, object]:
    """Resolve the active provider's settings, failing loudly on a typo.

    A misspelled MODEL_PROVIDER should not silently fall back to a default --
    that turns a five-second fix into a confusing debugging session about why
    the wrong model is answering.
    """
    key = (name or PROVIDER).strip().lower()
    if key not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown MODEL_PROVIDER {key!r}; expected one of: {known}")
    return PROVIDERS[key]


def provider_base_url(name: str | None = None) -> str:
    return str(provider_settings(name)["base_url"])


def provider_models(name: str | None = None) -> list[str]:
    return list(provider_settings(name)["models"])  # type: ignore[arg-type]


def primary_model(name: str | None = None) -> str:
    return provider_models(name)[0]


def api_key(name: str | None = None) -> str | None:
    """The active provider's key, read from the environment at call time.

    Read on each call rather than captured at import, so a key added to .env
    after the process starts is picked up by a reload rather than requiring a
    restart -- and so tests can remove it.
    """
    return os.environ.get(str(provider_settings(name)["key_env"])) or None
