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

# D-14: OpenRouter free tier, sent as a model-fallback array in one request so
# that free-tier capacity failures fail over rather than failing the demo.
# Nothing outside app/agent/provider.py may import a vendor client.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRIMARY_MODEL = "z-ai/glm-5.2:free"
FALLBACK_MODELS = ["nvidia/nemotron-3-nano-30b-a3b:free"]
MAX_TOKENS = 4096

# Phase 10 cost ceiling — a public URL in front of an LLM endpoint needs one.
MAX_TURNS_PER_SESSION = 30
MAX_TOOL_CALLS_PER_TURN = 12


def openrouter_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or None
