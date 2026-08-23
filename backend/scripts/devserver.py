"""The real app, with a scripted model. Development only -- never shipped.

The hosted demo runs on a free tier capped at 50 model requests per day (D-18).
Building a streaming interface against that is untenable: a single afternoon of
UI iteration would spend several days of quota, and every reload would return a
slightly different answer, so no layout bug could be reproduced twice.

So this serves the *real* FastAPI app -- real tools, real rule engine, real
access control, real SSE protocol -- and replaces only `provider.stream_completion`
with a scripted reply chosen by keyword. The frontend cannot tell the difference,
which is the point: everything it talks to is genuine except the narrator.

    python -m scripts.devserver

⚠️ It patches a module attribute at import time. That is acceptable here and
nowhere else, which is why it lives in scripts/ rather than app/.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import uvicorn

from app.agent import provider

# ------------------------------------------------------------------ scripts

#: Each entry: a matcher, then the rounds the "model" plays. A round is
#: (narration, [(tool_name, arguments)]). An empty tool list ends the turn.
SCRIPTS: list[tuple[re.Pattern[str], list[tuple[str, list[tuple[str, dict[str, Any]]]]]]] = [
    (
        re.compile(r"cancel", re.I),
        [
            ("", [("get_order", {"order_id": "ORD-1001"})]),
            ("", [("evaluate_cancellation", {"order_id": "ORD-1001"})]),
            (
                "No cancellation fee applies. Northstar's enterprise agreement lets you "
                "cancel any BOOKED shipment before pickup at no charge, regardless of how "
                "long ago it was booked. The standard SOP would have charged ₹250 after "
                "a 30-minute grace window, but the agreement outranks it. A previous ticket "
                "told this account otherwise, and that answer was wrong.",
                [],
            ),
        ],
    ),
    (
        # The requirement-4 beat: a credit above the SOP's ₹1,000 threshold, so
        # the card lands as NEEDS_APPROVAL and only a manager can clear it.
        re.compile(r"escalate|refund|compensat|goodwill", re.I),
        [
            ("", [("get_ticket", {"ticket_id": "TKT-501"})]),
            (
                "",
                [
                    (
                        "propose_action",
                        {
                            "action_type": "issue_service_credit",
                            "account_id": "ACCT-001",
                            "target_id": "TKT-501",
                            "amount_inr": 1500,
                            "reason": "Goodwill credit for the breached P1 first-response target.",
                        },
                    )
                ],
            ),
            (
                "I've prepared a ₹1,500 goodwill credit against TKT-501. Nothing has been "
                "issued yet — it is above the ₹1,000 threshold in SOP v4 §3, so it needs a "
                "support manager to approve it. Review the details below and confirm.",
                [],
            ),
        ],
    ),
    (
        re.compile(r"credit", re.I),
        [
            (
                "",
                [
                    (
                        "evaluate_service_credit",
                        {
                            "account_id": "ACCT-002",
                            "hours_past_window_end": 3,
                            "carrier_fault": True,
                            "customer_fault": False,
                        },
                    )
                ],
            ),
            (
                "Not eligible. Your service agreement sets the threshold at more than four "
                "hours past the end of the pickup window, and this delay is three hours. "
                "The agreement replaces both the standard two-hour threshold and the "
                "default credit amount, so the general SOP does not apply to your account.",
                [],
            ),
        ],
    ),
    (
        re.compile(r"sla|breach|respond", re.I),
        [
            ("", [("detect_issues", {})]),
            (
                "Two tickets have breached their first-response targets. TKT-501 is a P1 "
                "production outage on Northstar, whose agreement sets a 15-minute 24x7 "
                "target -- it is 15 minutes overdue. TKT-505 reports a suspected API key "
                "exposure on Axis Labs, 120 minutes past the 30-minute Enterprise target.",
                [],
            ),
        ],
    ),
    (
        re.compile(r"csv|upload|bulk", re.I),
        [
            ("", [("get_ticket", {"ticket_id": "TKT-502"})]),
            ("", [("search_policy_documents", {"query": "bulk upload row limit"})]),
            (
                "This is a known defect, not a plan limit. The Growth plan supports up to "
                "5,000 rows per CSV; KI-208 causes intermittent failures above roughly "
                "3,000 rows and is under investigation. Splitting the file below 3,000 rows "
                "is the current workaround. An earlier ticket told this customer the plan "
                "was capped at 3,000 rows, which was incorrect.",
                [],
            ),
        ],
    ),
    (
        re.compile(r"owed|unclaimed|nobody", re.I),
        [
            ("", [("detect_issues", {})]),
            (
                "Yes -- ORD-2002 on LumenWorks. The pickup is 4 hours 30 minutes past the "
                "end of its window with carrier fault accepted and no customer fault, which "
                "meets their agreement's four-hour threshold and entitles them to a flat "
                "₹300 credit. No ticket has been raised, so nobody has asked for it.",
                [],
            ),
        ],
    ),
]

FALLBACK: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    (
        "This is the development server with a scripted narrator, so it only answers a "
        "fixed set of questions. Try one of the suggestions, or run the real backend with "
        "an OpenRouter key to ask anything.",
        [],
    ),
]


def _script_for(messages: list[dict[str, Any]]) -> list[tuple[str, list[tuple[str, dict]]]]:
    latest = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            latest = str(message.get("content") or "")
            break
    for matcher, script in SCRIPTS:
        if matcher.search(latest):
            return script
    return FALLBACK


def install(delay: float) -> None:
    """Replace the provider with a replay of the scripted chain."""
    import asyncio

    async def stream_completion(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> AsyncIterator[provider.TextDelta | provider.Completed]:
        script = _script_for(messages)
        # How many assistant turns have already been played this request.
        round_index = sum(1 for m in messages if m.get("role") == "assistant")
        text, calls = script[min(round_index, len(script) - 1)]

        if text:
            for word in text.split(" "):
                if delay:
                    await asyncio.sleep(delay)
                yield provider.TextDelta(word + " ")

        yield provider.Completed(
            finish_reason="tool_calls" if calls else "stop",
            # Ids must be unique across rounds, not just within one. A real
            # provider guarantees that; a naive `call_{index}` does not, and the
            # collision would make the client attach round two's result to round
            # one's trace row -- a wrong trace being worse than no trace.
            tool_calls=[
                provider.ToolCall(
                    id=f"call_{round_index}_{index}",
                    name=name,
                    arguments=json.dumps(arguments),
                )
                for index, (name, arguments) in enumerate(calls)
            ],
            text=text,
            usage={"total_tokens": 0, "scripted": True},
            model="scripted-devserver",
        )

    provider.stream_completion = stream_completion  # type: ignore[assignment]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.02,
        help="per-word delay, so streaming looks like streaming",
    )
    args = parser.parse_args()

    install(args.delay)

    from app.api.main import create_app

    print(f"scripted dev server on http://127.0.0.1:{args.port} -- no model requests are made")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
