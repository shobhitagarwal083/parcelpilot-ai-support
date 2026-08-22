"""Streaming chat endpoint.

Server-sent events rather than a single JSON response, so the tool trace renders
while the model is still working. That is requirement 6, and it also covers for
the latency of a free-tier model: a reviewer sees the agent looking things up
within a second or two rather than watching a blank box.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import config
from app.agent import loop
from app.api.deps import CurrentPrincipal
from app.auth.principals import Principal

router = APIRouter(prefix="/api/chat", tags=["chat"])


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    #: Recent turns only. No summarisation, no compaction -- at this scale the
    #: complexity would buy nothing, and a follow-up like "can I cancel it?"
    #: after "what is the status of ORD-1001?" is what makes it feel like a
    #: product rather than a demo.
    history: list[Turn] = Field(default_factory=list)


MAX_HISTORY_TURNS = 12


async def _events(principal: Principal, request: ChatRequest) -> AsyncIterator[str]:
    history = [
        {"role": turn.role, "content": turn.content}
        for turn in request.history[-MAX_HISTORY_TURNS:]
        if turn.role in ("user", "assistant") and turn.content
    ]
    history.append({"role": "user", "content": request.message})

    async for event in loop.run(principal, history, as_of=config.SNAPSHOT_AT):
        yield event.sse()


@router.post("")
async def chat(principal: CurrentPrincipal, request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _events(principal, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
