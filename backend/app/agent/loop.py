"""The streaming tool loop.

Hand-written rather than an SDK tool runner, because the client has to see each
tool call *as it happens*. Requirement 6 asks the interface to show which tool
is being used, and that is only true if `get_order(ORD-1001)` renders while the
model is still thinking about the next step -- not after the turn completes.

The loop also derives events the model never sends. Citations, proposed actions
and escalations are read out of the Decision objects the tools returned, so the
UI renders them from data the engine produced rather than from anything the
model chose to say about them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app import config
from app.agent import prompts, provider
from app.agent.tools import REGISTRY, parse_arguments
from app.auth.principals import Principal


@dataclass
class Event:
    kind: str
    data: dict[str, Any]

    def sse(self) -> str:
        return f"event: {self.kind}\ndata: {json.dumps(self.data, default=str)}\n\n"


MAX_TOOL_ROUNDS = 6


async def run(
    principal: Principal,
    history: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    model: str | None = None,
) -> AsyncIterator[Event]:
    as_of = as_of or config.SNAPSHOT_AT
    messages = prompts.system_messages(principal) + list(history)
    schemas = REGISTRY.schemas()

    seen_citations: set[str] = set()
    tool_calls_made = 0
    usage: dict[str, Any] = {}

    for _ in range(MAX_TOOL_ROUNDS):
        completed: provider.Completed | None = None
        try:
            async for chunk in provider.stream_completion(messages, schemas, model=model):
                if isinstance(chunk, provider.TextDelta):
                    yield Event("token", {"text": chunk.text})
                else:
                    completed = chunk
        except provider.MissingCredentials as exc:
            yield Event("error", {"message": str(exc), "kind": "missing_credentials"})
            return
        except provider.ProviderError as exc:
            yield Event("error", {"message": str(exc), "kind": "provider"})
            return

        if completed is None:
            yield Event("error", {"message": "the provider returned no response"})
            return

        usage = completed.usage or usage

        if completed.was_declined:
            # Do not try to salvage a declined turn. TKT-505 is a suspected
            # credential exposure, and an assistant that half-answers a security
            # question is worse than one that hands it to a person.
            yield Event(
                "escalation",
                {
                    "reason": "The assistant declined to answer this one. Routing it to a "
                              "human colleague rather than guessing.",
                },
            )
            yield Event("done", {"usage": usage, "finish_reason": completed.finish_reason})
            return

        if not completed.tool_calls:
            yield Event("done", {"usage": usage, "model": completed.model})
            return

        messages.append(
            {
                "role": "assistant",
                "content": completed.text or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments or "{}"},
                    }
                    for call in completed.tool_calls
                ],
            }
        )

        for call in completed.tool_calls:
            if tool_calls_made >= config.MAX_TOOL_CALLS_PER_TURN:
                yield Event(
                    "error",
                    {"message": "too many tool calls in one turn", "kind": "turn_cap"},
                )
                yield Event("done", {"usage": usage})
                return
            tool_calls_made += 1

            arguments = parse_arguments(call.arguments)
            tool = REGISTRY.get(call.name)
            yield Event(
                "tool_call",
                {
                    "id": call.id,
                    "name": call.name,
                    "label": tool.trace_label if tool else call.name,
                    "input": arguments,
                },
            )

            result = REGISTRY.invoke(call.name, principal, arguments, as_of=as_of)

            yield Event(
                "tool_result",
                {
                    "id": call.id,
                    "name": call.name,
                    "is_error": bool(result.get("is_error")),
                    "summary": _summarise(call.name, result),
                    "decision": result if _is_decision(result) else None,
                },
            )

            for event in _derived_events(result, seen_citations):
                yield event

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )

    yield Event(
        "error",
        {"message": "gave up after too many tool rounds without an answer", "kind": "loop_cap"},
    )
    yield Event("done", {"usage": usage})


def _is_decision(result: dict[str, Any]) -> bool:
    return "outcome" in result and "citations" in result


def _derived_events(result: dict[str, Any], seen: set[str]) -> list[Event]:
    """Events read out of the engine's output, not out of the model's prose."""
    events: list[Event] = []

    for citation in result.get("citations", []) or []:
        key = f"{citation['doc_id']}#{citation['section']}"
        if key in seen:
            continue
        seen.add(key)
        events.append(Event("citation", citation))

    if result.get("requires_human") and result.get("human_reason"):
        events.append(Event("escalation", {"reason": result["human_reason"]}))

    if result.get("action_id") and result.get("status") in ("PENDING", "NEEDS_APPROVAL"):
        events.append(
            Event(
                "action_proposed",
                {
                    "action_id": result["action_id"],
                    "action_type": result["action_type"],
                    "target_id": result.get("target_id"),
                    "account_id": result.get("account_id"),
                    "payload": result.get("payload", {}),
                    "status": result["status"],
                    "needs_approval": result.get("needs_approval", False),
                    "decision": result.get("decision"),
                },
            )
        )

    return events


def _summarise(name: str, result: dict[str, Any]) -> str:
    if result.get("is_error"):
        return result["error"]
    if _is_decision(result):
        return result.get("summary") or result["outcome"]
    if "results" in result:
        return f"{len(result['results'])} passage(s) found"
    if "count" in result:
        return f"{result['count']} record(s)"
    if "action_id" in result:
        return f"prepared {result['action_type']}, awaiting confirmation"
    for key in ("order_id", "ticket_id", "account_id"):
        if key in result:
            return f"{result[key]}"
    return "ok"
