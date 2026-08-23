"""The cost ceiling.

A public URL in front of a model endpoint needs one, and the one that was here
before was dead code: `MAX_TURNS_PER_SESSION` was defined, never referenced, and
unenforceable anyway because the chat API is stateless. These tests exist so the
replacement cannot rot the same way -- a limit nothing exercises is a limit
nobody notices has stopped working.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent import provider
from app.api import limits
from app.api.main import create_app


@pytest.fixture(autouse=True)
def clean_counters():
    limits.reset()
    yield
    limits.reset()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Never call a real provider from a test.

    Worth being explicit about, because unsetting the environment does not
    achieve it: config loads .env at import, so the key is back in os.environ
    before any test runs. Without this stub these five tests made real model
    calls and took 77 seconds -- slow, non-deterministic, and quietly spending
    the same free-tier allowance the limiter exists to protect.
    """

    async def stubbed(messages, tools, *, model=None, timeout=120.0):
        yield provider.TextDelta("ok")
        yield provider.Completed(finish_reason="stop", text="ok")

    monkeypatch.setattr(provider, "stream_completion", stubbed)


@pytest.fixture
def client():
    return TestClient(create_app())


def test_reading_records_is_never_rationed(client):
    """Only model calls cost anything. Records come from SQLite.

    Rationing them would punish the persona switching that makes the access
    boundary visible, for no saving at all.
    """
    for _ in range(limits.BURST[1] * 2):
        response = client.get("/api/session/personas")
        assert response.status_code == 200


def test_the_burst_window_stops_a_runaway_caller(client):
    """One visitor should not spend the allowance the next visitor needs."""
    allowed = limits.BURST[1]
    codes = [
        client.post("/api/chat", json={"message": "hello", "history": []}).status_code
        for _ in range(allowed + 2)
    ]

    assert codes[-1] == 429
    assert codes.count(429) == 2, "should block only what exceeds the window"


def test_a_blocked_caller_is_told_when_to_come_back(client):
    for _ in range(limits.BURST[1]):
        client.post("/api/chat", json={"message": "hi", "history": []})

    response = client.post("/api/chat", json={"message": "hi", "history": []})

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    # Phrased for a person who is not at fault -- the allowance is shared.
    assert "shares one free-tier" in response.json()["detail"]


def test_callers_are_counted_separately(client):
    """Otherwise the first busy visitor locks out everyone behind the proxy."""
    for _ in range(limits.BURST[1]):
        client.post(
            "/api/chat",
            json={"message": "hi", "history": []},
            headers={"x-forwarded-for": "10.0.0.1"},
        )

    blocked = client.post(
        "/api/chat", json={"message": "hi", "history": []}, headers={"x-forwarded-for": "10.0.0.1"}
    )
    other = client.post(
        "/api/chat", json={"message": "hi", "history": []}, headers={"x-forwarded-for": "10.0.0.2"}
    )

    assert blocked.status_code == 429
    assert other.status_code != 429


def test_the_forwarded_client_wins_over_the_peer_address():
    """Behind a proxy the peer is the proxy, so every visitor would share a bucket."""

    class Request:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "127.0.0.1"})()

    assert limits.client_key(Request({"fly-client-ip": "203.0.113.7"})) == "203.0.113.7"
    assert limits.client_key(Request({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})) == "203.0.113.9"
    assert limits.client_key(Request({})) == "127.0.0.1"
