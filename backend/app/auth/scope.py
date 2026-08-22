"""The enforcement points. Every one of them raises rather than filtering quietly.

Requirement 2 says access control must live in the data/tool layer, not in model
instructions. The distinguishing test is A11: the model passes
`account_id="ACCT-001"` while the session belongs to a customer of ACCT-002, and
still gets `AccessDenied`. The requested account is *intersected* with the
principal's, never trusted.

A12 closes the hole A11 misses. Evaluate tools take hypothetical parameters, so
they carry an `account_id` that is attached to no row any repository guards.
Without scoping them, a Northstar customer can ask "what would ACCT-002 get for
a 3-hour delay?" and read LumenWorks' contractual 4-hour threshold back out of
the answer -- a contract-terms leak that trips no repository check.
"""

from __future__ import annotations

from app.auth.principals import Principal
from app.repo import audit


class AccessDenied(PermissionError):
    def __init__(self, principal: Principal, resource: str, reason: str) -> None:
        self.principal_id = principal.id
        self.resource = resource
        self.reason = reason
        super().__init__(f"{principal.id} may not access {resource}: {reason}")


class NotFound(LookupError):
    pass


def assert_account_access(principal: Principal, account_id: str, *, resource: str) -> None:
    if principal.can_access(account_id):
        return
    audit.record(
        principal_id=principal.id,
        action="account_access",
        resource=resource,
        outcome="denied",
        detail=f"requested {account_id}, scoped to {sorted(principal.account_ids) or 'none'}",
    )
    raise AccessDenied(
        principal,
        resource,
        f"it belongs to {account_id}, which is outside this session's scope",
    )


def require_capability(principal: Principal, capability: str, *, action: str) -> None:
    if principal.can(capability):
        return
    audit.record(
        principal_id=principal.id,
        action=action,
        resource=capability,
        outcome="denied",
        detail=f"role {principal.role} lacks {capability}",
    )
    raise AccessDenied(principal, action, f"the {principal.role} role lacks {capability!r}")


def resolve_account_scope(principal: Principal, requested: str | None) -> frozenset[str] | None:
    """Intersect a requested account with the session's own scope.

    Returns None when the principal may read every account, which the
    repositories treat as "no WHERE clause".
    """
    if requested is None:
        return None if principal.reads_any_account else principal.account_ids
    assert_account_access(principal, requested, resource=f"account {requested}")
    return frozenset({requested})


def resolve_subject_account(principal: Principal, requested: str | None) -> str:
    """Pick the single account a hypothetical question is about.

    A customer asking "should I get a credit?" names no account, and the answer
    differs per account -- 2 hours under the SOP, 4 under the LumenWorks
    agreement -- so it is resolved from the session rather than guessed. An
    internal user must name one, because they have no single account of their own.
    """
    if requested is not None:
        assert_account_access(principal, requested, resource=f"account {requested}")
        return requested
    if len(principal.account_ids) == 1:
        return next(iter(principal.account_ids))
    raise ValueError(
        "which account is this about? The answer depends on the customer agreement, "
        "so it cannot be answered generically."
    )
