"""The cost ceiling for a public URL in front of a model endpoint.

There was a `MAX_TURNS_PER_SESSION` constant here in spirit, and it was dead
code: defined, never referenced, and unenforceable as written. The chat API is
stateless -- the client sends its own history on every request -- so there is no
session to count turns against. A cap that cannot be enforced is worse than no
cap, because it reads like protection.

The control that does work is per-IP, and it is what a public demo actually
needs: one visitor should not be able to spend the shared free-tier allowance
that the next visitor needs.

Two windows, because they defend against different things:

  * a short window stops a burst -- a refresh-happy reviewer or a loop
  * a long window stops sustained draining over an afternoon

Deliberately in-process. A single container serves this app (see api/main.py),
so a dict is the correct data structure and Redis would be operational weight
for nothing. If this were ever scaled to more than one instance the limit would
become per-instance, which is the sort of thing that should be noticed here
rather than discovered in production -- hence this paragraph.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

#: (window seconds, max requests in that window)
BURST = (60, 12)
SUSTAINED = (3600, 120)

#: Paths that cost a model call. Everything else -- personas, records, the
#: triage board -- is served from SQLite and is not worth rationing.
METERED_PREFIXES = ("/api/chat",)

_hits: dict[str, deque[float]] = defaultdict(deque)


def client_key(request: Request) -> str:
    """Identify the caller.

    Behind Fly.io the peer address is the proxy, so the forwarded header is the
    real client. It is spoofable by a determined caller, which is acceptable
    here: this is a courtesy limit protecting a free-tier allowance, not an
    authentication boundary. Treating it as the latter would be the mistake.
    """
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _over_limit(key: str, now: float) -> tuple[bool, int]:
    """Record this hit and report whether it breaches either window."""
    seen = _hits[key]
    longest = max(BURST[0], SUSTAINED[0])
    while seen and now - seen[0] > longest:
        seen.popleft()

    for window, allowed in (BURST, SUSTAINED):
        recent = sum(1 for stamp in seen if now - stamp <= window)
        if recent >= allowed:
            oldest = next(stamp for stamp in seen if now - stamp <= window)
            return True, max(1, int(window - (now - oldest)) + 1)

    seen.append(now)
    return False, 0


async def rate_limit(request: Request, call_next):
    """Reject metered requests from a caller who has had their share."""
    if not request.url.path.startswith(METERED_PREFIXES):
        return await call_next(request)

    blocked, retry_after = _over_limit(client_key(request), time.monotonic())
    if blocked:
        # 429 with the same shape the provider's own rate limit produces, so
        # the client's existing error path renders it without a special case.
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "detail": (
                    "Rate limit: this demo shares one free-tier model allowance across "
                    f"everyone using it. Try again in about {retry_after}s."
                )
            },
        )

    return await call_next(request)


def reset() -> None:
    """Clear all counters. For tests."""
    _hits.clear()
