"""The provider boundary. Nothing else in the codebase imports an LLM client.

D-14: the system runs on OpenRouter's free tier, primary and fallback sent as a
model array in one request so a capacity failure on one fails over rather than
failing the demo.

The boundary earns its place beyond portability. Every correctness-bearing
component -- the rule engine, the calendar, retrieval, access control -- contains
no model call at all, which is precisely why the provider is swappable. Most
systems cannot do this; ours can because the model never arbitrates or computes.

Swapping to a paid provider is a change to this file and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app import config


class ProviderError(RuntimeError):
    pass


class MissingCredentials(ProviderError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str = ""
    arguments: str = ""


@dataclass
class TextDelta:
    text: str


@dataclass
class Completed:
    finish_reason: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    model: str | None = None

    @property
    def was_declined(self) -> bool:
        """Some providers end a turn by refusing rather than by answering.

        Worth handling explicitly here: TKT-505 in this dataset is a suspected
        credential exposure, and asking an assistant to help investigate one is
        exactly the benign security-adjacent request a safety classifier can
        catch. Losing the turn silently would break the demo on the most urgent
        ticket in the pack.
        """
        return self.finish_reason in ("content_filter", "refusal")


def _headers() -> dict[str, str]:
    key = config.openrouter_api_key()
    if not key:
        raise MissingCredentials(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add a key -- "
            "the policy engine, retrieval and access control all run and test without one."
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # OpenRouter attributes free-tier usage with these.
        "HTTP-Referer": "https://github.com/parcelpilot-ai-support",
        "X-Title": "ParcelPilot AI Support",
    }


def build_payload(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    primary = model or config.PRIMARY_MODEL
    payload: dict[str, Any] = {
        "model": primary,
        "messages": messages,
        "max_tokens": config.MAX_TOKENS,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if model is None and config.FALLBACK_MODELS:
        # OpenRouter accepts an ordered fallback array in one request, so a
        # free-tier capacity failure is invisible rather than fatal.
        payload["models"] = [primary, *config.FALLBACK_MODELS]
    return payload


async def stream_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 120.0,
) -> AsyncIterator[TextDelta | Completed]:
    """Yield text deltas as they arrive, then one Completed at the end.

    Streaming is not decoration. Tool-call events reach the client the moment
    the model emits them, so the trace renders while the model is still working
    -- which is requirement 6, and which also hides most of the latency of a
    free-tier model.
    """
    payload = build_payload(messages, tools, model=model, stream=True)

    calls: dict[int, ToolCall] = {}
    text_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    served_by: str | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=_headers(),
            json=payload,
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise ProviderError(f"provider returned {response.status_code}: {body[:500]}")

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                if error := chunk.get("error"):
                    raise ProviderError(str(error.get("message") or error))

                served_by = chunk.get("model") or served_by
                if chunk.get("usage"):
                    usage = chunk["usage"]

                for choice in chunk.get("choices", []):
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}

                    if content := delta.get("content"):
                        text_parts.append(content)
                        yield TextDelta(content)

                    for fragment in delta.get("tool_calls") or []:
                        _accumulate(calls, fragment)

    yield Completed(
        finish_reason=finish_reason,
        tool_calls=[calls[i] for i in sorted(calls)],
        text="".join(text_parts),
        usage=usage,
        model=served_by,
    )


def _accumulate(calls: dict[int, ToolCall], fragment: dict[str, Any]) -> None:
    """Reassemble a tool call from its streamed pieces.

    Name and ID arrive once; the JSON arguments arrive as a series of string
    fragments that mean nothing until concatenated.
    """
    index = fragment.get("index", 0)
    call = calls.setdefault(index, ToolCall(id=fragment.get("id") or f"call_{index}"))
    if fragment.get("id"):
        call.id = fragment["id"]
    function = fragment.get("function") or {}
    if function.get("name"):
        call.name = function["name"]
    if function.get("arguments"):
        call.arguments += function["arguments"]
