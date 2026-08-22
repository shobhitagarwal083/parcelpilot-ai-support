"""Request-scoped dependencies.

The principal is resolved from the request and injected server-side. It is never
a body field, never a query parameter, and never something the model can set --
which is the whole of requirement 2's "enforced in the data/tool layer".

Auth is mocked, as the brief permits: the client names a persona and the server
looks it up. What is *not* mocked is what happens after that, which is where
every real enforcement decision is made.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.auth import principals
from app.auth.principals import Principal
from app.auth.scope import AccessDenied, NotFound


def current_principal(
    x_principal_id: Annotated[str | None, Header()] = None,
) -> Principal:
    try:
        return principals.get(x_principal_id or principals.DEFAULT_PERSONA)
    except LookupError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def to_http(exc: Exception) -> HTTPException:
    """Map domain errors onto status codes without leaking what was hidden."""
    if isinstance(exc, AccessDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
