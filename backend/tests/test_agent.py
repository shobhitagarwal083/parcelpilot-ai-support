"""The agent layer: registry, prompts, provider payload and the streaming loop.

None of this needs an API key. The provider is stubbed, which is possible only
because it sits behind one module -- and that boundary is also what makes the
whole system portable to a paid provider later.
"""

from __future__ import annotations

import json

import pytest

from app import config
from app.agent import loop, prompts, provider
from app.agent import provider as provider_module
from app.agent.tools import REGISTRY, parse_arguments
from app.auth import principals

EXPECTED_TOOLS = {
    "search_policy_documents",
    "get_account",
    "get_order",
    "list_orders",
    "get_ticket",
    "search_tickets",
    "evaluate_cancellation",
    "evaluate_service_credit",
    "evaluate_sla",
    "detect_issues",
    "propose_action",
}


# ---------------------------------------------------------------- registry


def test_the_registry_covers_all_three_required_tool_categories():
    """Requirement 3: document retrieval, structured lookup, and a state-changing
    action."""
    assert set(REGISTRY.tools) == EXPECTED_TOOLS


def test_no_tool_schema_exposes_the_principal():
    """The principal is injected server-side. If it appeared in a schema, the
    model could name a different one -- which is the whole failure mode
    requirement 2 exists to prevent."""
    for tool in REGISTRY.tools.values():
        properties = tool.parameters["properties"]
        assert "principal" not in properties, tool.name
        assert "principal_id" not in properties, tool.name
        assert "as_of" not in properties, tool.name


def test_every_persona_is_offered_the_identical_tool_list():
    """Hiding detect_issues from customers would be a prompt-level guard, which
    is what the brief warns against. It is offered to everyone and refused by
    the capability check, so the refusal is visible in the trace."""
    schemas = REGISTRY.schemas()
    assert schemas == REGISTRY.schemas()
    assert [s["function"]["name"] for s in schemas] == sorted(EXPECTED_TOOLS)


def test_tool_schemas_are_well_formed():
    for tool in REGISTRY.tools.values():
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["description"].strip()
        assert schema["function"]["parameters"]["type"] == "object"
        assert tool.trace_label


def test_an_unknown_tool_comes_back_as_data_not_an_exception(now):
    result = REGISTRY.invoke("summon_manager", principals.get("staff-rohit"), {}, as_of=now)

    assert result["is_error"]
    assert "summon_manager" in result["error"]


def test_a_denied_tool_call_comes_back_as_data(now):
    """It has to reach the model so it can correct course, and the trace so a
    human can see what was attempted. Raising would hide both."""
    result = REGISTRY.invoke(
        "evaluate_cancellation",
        principals.get("cust-lumenworks"),
        {"order_id": "ORD-1001"},
        as_of=now,
    )

    assert result["is_error"]
    assert result["error_kind"] == "access_denied"


def test_malformed_arguments_come_back_as_data(now):
    result = REGISTRY.invoke("get_order", principals.get("staff-rohit"), {}, as_of=now)

    assert result["is_error"]
    assert result["error_kind"] == "bad_arguments"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"order_id": "ORD-1001"}', {"order_id": "ORD-1001"}),
        ({"order_id": "ORD-1001"}, {"order_id": "ORD-1001"}),
        ("", {}),
        (None, {}),
        ("not json at all", {"__unparsed__": "not json at all"}),
        ("[1, 2]", {"__unparsed__": "[1, 2]"}),
    ],
)
def test_argument_parsing_tolerates_what_models_emit(raw, expected):
    assert parse_arguments(raw) == expected


# ---------------------------------------------------------------- prompts


def test_the_system_prefix_is_byte_stable_across_personas():
    """What makes a cached prompt prefix possible if a provider supports one.
    Costs nothing now; retrofitting means unpicking the prompt builder."""
    prefixes = {prompts.system_messages(p)[0]["content"] for p in principals.PERSONAS.values()}
    assert len(prefixes) == 1


def test_the_prefix_carries_the_pinned_time_not_a_live_clock():
    """The usual thing that silently invalidates a cached prefix is a
    datetime.now() in the system prompt. Ours is a constant."""
    prefix = prompts.SYSTEM_PREFIX
    assert "Sunday 16 August 2026, 11:00 IST" in prefix
    assert prefix == prompts.SYSTEM_PREFIX


def test_the_prefix_forbids_the_model_doing_arithmetic_or_arbitration():
    prefix = prompts.SYSTEM_PREFIX.lower()
    assert "never calculate" in prefix
    assert "never choose between conflicting sources" in prefix
    assert "does not execute anything" in prompts.SYSTEM_PREFIX


def test_persona_guidance_comes_after_the_prefix_not_inside_it():
    messages = prompts.system_messages(principals.get("cust-northstar"))

    assert len(messages) == 2
    assert "ACCT-001" not in messages[0]["content"]
    assert "ACCT-001" in messages[1]["content"]


def test_customers_are_told_to_describe_behaviour_not_internal_ids():
    """Q7: a customer has no use for an issue ID and its investigation state."""
    assert "issue ID" in prompts.CUSTOMER_BRIEF
    assert "20 minutes" in prompts.CUSTOMER_BRIEF
    assert "rule identifiers" in prompts.INTERNAL_BRIEF


# ---------------------------------------------------------------- provider


def test_the_payload_is_vendor_neutral():
    """One adapter reaches both providers, so the payload carries no vendor field.

    Failover used to ride on OpenRouter's `models` array, which Google does not
    accept -- the behaviour would have silently vanished on switching provider.
    It lives in stream_completion now, so the payload stays plain.
    """
    payload = provider.build_payload([{"role": "user", "content": "hi"}], REGISTRY.schemas())

    assert payload["model"] == config.primary_model()
    assert "models" not in payload
    assert payload["max_tokens"] == config.MAX_TOKENS
    assert len(payload["tools"]) == len(EXPECTED_TOOLS)


def test_pinning_a_model_overrides_the_configured_primary():
    """The bake-off needs each model measured on its own."""
    payload = provider.build_payload([], [], model="some-other-model")

    assert payload["model"] == "some-other-model"


def test_every_configured_provider_resolves():
    for name in config.PROVIDERS:
        assert config.provider_base_url(name).startswith("https://")
        assert config.provider_models(name), f"{name} lists no models"


def test_an_unknown_provider_fails_loudly():
    """A typo in MODEL_PROVIDER must not quietly answer from the wrong vendor."""
    with pytest.raises(ValueError, match="unknown MODEL_PROVIDER"):
        config.provider_settings("gemeni")


def test_a_missing_key_names_the_variable_and_what_still_works(monkeypatch):
    monkeypatch.delenv(str(config.provider_settings()["key_env"]), raising=False)
    with pytest.raises(provider.MissingCredentials, match="policy engine"):
        provider._headers()


@pytest.mark.asyncio
async def test_failover_tries_the_next_candidate_when_the_first_will_not_serve(monkeypatch):
    """Capacity failures are what this exists for; every free tier hits them."""
    attempted: list[tuple[str, str]] = []
    candidates = config.candidates()
    if len(candidates) < 2:
        pytest.skip("needs at least two configured candidates")

    async def flaky(messages, tools, *, provider, model, timeout):
        attempted.append((provider, model))
        if (provider, model) == (candidates[0].provider, candidates[0].model):
            raise provider_module.ProviderError("provider returned 429: rate limited")
        yield provider_module.TextDelta("ok")
        yield provider_module.Completed(finish_reason="stop", text="ok")

    monkeypatch.setattr(provider, "_stream_once", flaky)

    chunks = [c async for c in provider.stream_completion([], [])]

    assert attempted[0] == (candidates[0].provider, candidates[0].model)
    assert len(attempted) == 2
    assert any(isinstance(c, provider.Completed) for c in chunks)


@pytest.mark.asyncio
async def test_failover_crosses_vendors_not_just_models(monkeypatch):
    """D-20: the case failover exists for is the one a single vendor cannot cover.

    Free quota is metered per project, so when a provider answers
    RESOURCE_EXHAUSTED every one of its models is exhausted at the same instant.
    A chain that never leaves that vendor fails for the reason the first attempt
    failed.
    """
    monkeypatch.setattr(config, "PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "test-google")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")

    providers_tried = [c.provider for c in config.candidates()]

    assert providers_tried[0] == "google"
    assert "groq" in providers_tried, "a second vendor must be reachable"


@pytest.mark.asyncio
async def test_a_provider_without_a_key_is_skipped_not_attempted(monkeypatch):
    """An unused entry in the table should cost nothing."""
    monkeypatch.setattr(config, "PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "test-google")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert {c.provider for c in config.candidates()} == {"google"}


@pytest.mark.asyncio
async def test_failover_does_not_splice_two_answers_together(monkeypatch):
    """Once the client has seen output, retrying elsewhere would join half of
    one answer to half of another -- worse than surfacing the error."""

    async def dies_mid_stream(messages, tools, *, provider, model, timeout):
        yield provider_module.TextDelta("the fee is ")
        raise provider_module.ProviderError("provider returned 500: upstream died")

    monkeypatch.setattr(provider, "_stream_once", dies_mid_stream)

    seen = []
    with pytest.raises(provider_module.ProviderError):
        async for chunk in provider.stream_completion([], []):
            seen.append(chunk)

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_pinned_model_never_fails_over(monkeypatch):
    """Substituting a different model would invalidate the bake-off silently."""
    attempted: list[str] = []

    async def always_fails(messages, tools, *, provider, model, timeout):
        attempted.append(model)
        raise provider_module.ProviderError("provider returned 429: rate limited")
        yield  # pragma: no cover - unreachable, makes this a generator

    monkeypatch.setattr(provider, "_stream_once", always_fails)

    with pytest.raises(provider_module.ProviderError):
        async for _ in provider.stream_completion([], [], model="pinned-model"):
            pass

    assert attempted == ["pinned-model"]


def test_streamed_tool_call_fragments_reassemble():
    """Name and ID arrive once; the JSON arguments arrive as fragments that mean
    nothing until concatenated."""
    calls: dict[int, provider.ToolCall] = {}
    for fragment in [
        {"index": 0, "id": "call_1", "function": {"name": "get_order", "arguments": '{"order'}},
        {"index": 0, "function": {"arguments": '_id": "ORD-'}},
        {"index": 0, "function": {"arguments": '1001"}'}},
    ]:
        provider._accumulate(calls, fragment)

    assert calls[0].name == "get_order"
    assert json.loads(calls[0].arguments) == {"order_id": "ORD-1001"}


@pytest.mark.parametrize("reason", ["content_filter", "refusal"])
def test_a_declined_turn_is_recognised(reason):
    """TKT-505 is a suspected credential exposure -- exactly the benign
    security-adjacent request a classifier can catch."""
    assert provider.Completed(finish_reason=reason).was_declined


# ---------------------------------------------------------------- the loop


def fake_provider(monkeypatch, script):
    """Replay a scripted sequence of provider responses."""
    turns = iter(script)

    async def stream_completion(messages, tools, *, model=None, timeout=120.0):
        text, calls = next(turns)
        for word in text.split():
            yield provider.TextDelta(word + " ")
        yield provider.Completed(
            finish_reason="tool_calls" if calls else "stop",
            tool_calls=calls,
            text=text,
            usage={"total_tokens": 42},
        )

    monkeypatch.setattr(provider, "stream_completion", stream_completion)


async def collect(principal, message, **kwargs):
    return [
        event
        async for event in loop.run(principal, [{"role": "user", "content": message}], **kwargs)
    ]


@pytest.mark.asyncio
async def test_a_tool_call_is_traced_before_its_result(monkeypatch, now):
    fake_provider(
        monkeypatch,
        [
            (
                "Let me check.",
                [
                    provider.ToolCall(
                        id="c1", name="evaluate_cancellation", arguments='{"order_id": "ORD-1001"}'
                    )
                ],
            ),
            ("No fee applies.", []),
        ],
    )
    events = await collect(principals.get("cust-northstar"), "can I cancel ORD-1001?", as_of=now)
    kinds = [e.kind for e in events]

    assert kinds.index("tool_call") < kinds.index("tool_result")
    call = next(e for e in events if e.kind == "tool_call")
    assert call.data["name"] == "evaluate_cancellation"
    assert call.data["label"] == "evaluating cancellation terms"
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_citations_are_derived_from_the_decision_not_the_prose(monkeypatch, now):
    """The UI renders what the engine produced, not what the model said about
    it. A model that forgot to mention its source cannot lose the citation."""
    fake_provider(
        monkeypatch,
        [
            (
                "",
                [
                    provider.ToolCall(
                        id="c1", name="evaluate_cancellation", arguments='{"order_id": "ORD-1001"}'
                    )
                ],
            ),
            ("Cancellation is free.", []),
        ],
    )
    events = await collect(principals.get("cust-northstar"), "cancel ORD-1001", as_of=now)
    citations = [e.data for e in events if e.kind == "citation"]

    docs = {c["doc_id"] for c in citations}
    assert "05_Northstar_Logistics_Enterprise_Agreement" in docs
    assert "03_Cancellation_and_Service_Credit_SOP_v4" in docs
    assert all(c["quote"] for c in citations)


@pytest.mark.asyncio
async def test_the_decision_reaches_the_client_whole(monkeypatch, now):
    fake_provider(
        monkeypatch,
        [
            (
                "",
                [
                    provider.ToolCall(
                        id="c1", name="evaluate_cancellation", arguments='{"order_id": "ORD-1001"}'
                    )
                ],
            ),
            ("Done.", []),
        ],
    )
    events = await collect(principals.get("cust-northstar"), "cancel ORD-1001", as_of=now)
    result = next(e for e in events if e.kind == "tool_result")

    assert result.data["decision"]["amount_inr"] == 0
    assert result.data["decision"]["winning_rule_id"] == "cancel.northstar.waiver"
    assert result.data["decision"]["contradicts"][0]["ticket_id"] == "TKT-450"


@pytest.mark.asyncio
async def test_a_denied_tool_call_is_traced_and_the_turn_continues(monkeypatch, now):
    fake_provider(
        monkeypatch,
        [
            ("", [provider.ToolCall(id="c1", name="detect_issues", arguments="{}")]),
            ("I cannot run that for you.", []),
        ],
    )
    events = await collect(principals.get("cust-northstar"), "what is broken?", as_of=now)
    result = next(e for e in events if e.kind == "tool_result")

    assert result.data["is_error"]
    assert "signals" in result.data["summary"]
    assert events[-1].kind == "done"


@pytest.mark.asyncio
async def test_a_proposed_action_is_announced_as_unexecuted(monkeypatch, now):
    fake_provider(
        monkeypatch,
        [
            (
                "",
                [
                    provider.ToolCall(
                        id="c1",
                        name="propose_action",
                        arguments='{"action_type": "create_escalation", "reason": "P1 breach", '
                        '"account_id": "ACCT-001", "target_id": "TKT-501"}',
                    )
                ],
            ),
            ("I have prepared an escalation for your confirmation.", []),
        ],
    )
    events = await collect(principals.get("staff-rohit"), "escalate TKT-501", as_of=now)
    proposed = next(e for e in events if e.kind == "action_proposed")

    assert proposed.data["status"] == "PENDING"
    assert proposed.data["action_type"] == "create_escalation"
    result = next(e for e in events if e.kind == "tool_result")
    assert "awaiting confirmation" in result.data["summary"]


@pytest.mark.asyncio
async def test_a_breach_emits_an_escalation_event(monkeypatch, now):
    fake_provider(
        monkeypatch,
        [
            (
                "",
                [
                    provider.ToolCall(
                        id="c1", name="evaluate_sla", arguments='{"ticket_id": "TKT-501"}'
                    )
                ],
            ),
            ("This is overdue.", []),
        ],
    )
    events = await collect(principals.get("staff-rohit"), "is TKT-501 late?", as_of=now)
    escalation = next(e for e in events if e.kind == "escalation")

    assert "breached by 15 minutes" in escalation.data["reason"]


@pytest.mark.asyncio
async def test_a_declined_turn_routes_to_a_human(monkeypatch, now):
    async def declining(messages, tools, *, model=None, timeout=120.0):
        yield provider.Completed(finish_reason="content_filter", tool_calls=[], text="")

    monkeypatch.setattr(provider, "stream_completion", declining)
    events = await collect(principals.get("staff-rohit"), "investigate TKT-505", as_of=now)

    assert [e.kind for e in events] == ["escalation", "done"]
    assert "human colleague" in events[0].data["reason"]


@pytest.mark.asyncio
async def test_a_missing_key_is_reported_not_raised(monkeypatch, now):
    async def unconfigured(messages, tools, *, model=None, timeout=120.0):
        raise provider.MissingCredentials("GEMINI_API_KEY is not set")
        yield  # pragma: no cover

    monkeypatch.setattr(provider, "stream_completion", unconfigured)
    events = await collect(principals.get("staff-rohit"), "hello", as_of=now)

    assert events[0].kind == "error"
    assert events[0].data["kind"] == "missing_credentials"


@pytest.mark.asyncio
async def test_a_runaway_loop_is_capped(monkeypatch, now):
    """A public URL in front of a model needs a ceiling that does not depend on
    the model behaving."""

    async def never_finishes(messages, tools, *, model=None, timeout=120.0):
        yield provider.Completed(
            finish_reason="tool_calls",
            tool_calls=[provider.ToolCall(id="c", name="list_orders", arguments="{}")],
        )

    monkeypatch.setattr(provider, "stream_completion", never_finishes)
    events = await collect(principals.get("staff-rohit"), "loop forever", as_of=now)

    assert events[-2].kind == "error"
    assert events[-2].data["kind"] in ("loop_cap", "turn_cap")
    assert events[-1].kind == "done"


def test_events_serialise_as_sse():
    event = loop.Event("tool_call", {"name": "get_order"})
    rendered = event.sse()

    assert rendered.startswith("event: tool_call\ndata: ")
    assert rendered.endswith("\n\n")
    assert json.loads(rendered.split("data: ", 1)[1].strip())["name"] == "get_order"
