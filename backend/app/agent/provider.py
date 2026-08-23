"""The provider boundary. Nothing else in the codebase imports an LLM client.

D-19: the system runs on Google AI Studio (Gemini) through its OpenAI-compatible
endpoint, with OpenRouter kept configured as an alternative. Both speak the same
chat-completions shape, so one adapter reaches either; `MODEL_PROVIDER` chooses.
Failover walks the configured model list here rather than relying on a vendor
field, so the behaviour survives changing provider.

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
    #: Opaque provider state attached to this call, echoed back verbatim on the
    #: next request. Gemini 3.x returns a `thought_signature` here and rejects
    #: the follow-up turn with a 400 if it is missing, so dropping it breaks
    #: every multi-step chain at the second call -- the first tool runs, then
    #: the turn dies. Nothing here interprets it; it is carried, not read.
    extra: dict[str, Any] = field(default_factory=dict)

    def as_message_part(self) -> dict[str, Any]:
        """Render this call for the assistant message that replays it.

        Built here rather than in the loop so that vendor-specific fields stay
        behind the provider boundary. The loop knows it is sending back a tool
        call; it does not know what any provider needs attached to one.
        """
        part: dict[str, Any] = {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }
        if self.extra:
            part["extra_content"] = self.extra
        return part


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


def _headers(provider: str | None = None) -> dict[str, str]:
    name = provider or config.PROVIDER
    settings = config.provider_settings(name)
    key = config.api_key(name)
    if not key:
        raise MissingCredentials(
            f"{settings['key_env']} is not set. Add it to .env (get one at "
            f"{settings['console']}) -- the policy engine, retrieval and access control "
            "all run and test without one."
        )
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if name == "openrouter":
        # OpenRouter attributes free-tier usage with these; others ignore them.
        headers["HTTP-Referer"] = "https://github.com/parcelpilot-ai-support"
        headers["X-Title"] = "ParcelPilot AI Support"
    return headers


def build_payload(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model or config.primary_model(),
        "messages": messages,
        "max_tokens": config.MAX_TOKENS,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


async def stream_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 120.0,
) -> AsyncIterator[TextDelta | Completed]:
    """Try each configured model in order until one answers, across vendors.

    Failover lives here rather than in a provider-specific request field.
    OpenRouter accepts an ordered `models` array and fails over server-side;
    Google does not, so relying on that would have made the behaviour vanish
    the moment the provider changed.

    D-20: it crosses vendors, and that is the point. Free quota is metered per
    project, not per model -- when Google answers RESOURCE_EXHAUSTED every
    Gemini model is exhausted at the same instant, so falling back from one to
    another fails for exactly the reason the first one did. A second vendor has
    independent quota, which is the only kind of fallback that survives the
    case it exists for.

    ⚠️ Failover only applies *before* the first chunk is yielded. Once the
    client has seen output, retrying elsewhere would splice two different
    answers into one turn, which is worse than the error.
    """
    if model is not None:
        # A pinned model means the caller is measuring that model. Failing over
        # would silently substitute a different one -- exactly what the bake-off
        # must not do.
        async for chunk in _stream_once(
            messages, tools, provider=config.PROVIDER, model=model, timeout=timeout
        ):
            yield chunk
        return

    candidates = config.candidates()
    if not candidates:
        # No provider has a key. Report the active one, since that is the
        # variable the operator most likely meant to set.
        _headers()
        raise ProviderError("no model was configured to try")

    last_error: ProviderError | None = None

    for candidate in candidates:
        produced = False
        try:
            async for chunk in _stream_once(
                messages,
                tools,
                provider=candidate.provider,
                model=candidate.model,
                timeout=timeout,
            ):
                produced = True
                yield chunk
            return
        except ProviderError as exc:
            if produced:
                raise
            last_error = exc

    raise last_error or ProviderError("no model was configured to try")


async def _stream_once(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    provider: str,
    model: str | None,
    timeout: float,
) -> AsyncIterator[TextDelta | Completed]:
    """One request to one model at one provider.

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
            f"{config.provider_base_url(provider)}/chat/completions",
            headers=_headers(provider),
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
    if extra := fragment.get("extra_content"):
        call.extra = extra
    function = fragment.get("function") or {}
    if function.get("name"):
        call.name = function["name"]
    if function.get("arguments"):
        call.arguments += function["arguments"]
