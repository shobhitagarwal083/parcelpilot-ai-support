"""Mock authentication: six switchable personas.

The brief permits mocking auth, account context and roles. What it does not
permit is enforcing them in the prompt -- "access controls should be enforced in
the data/tool layer rather than relying only on model instructions". So the
Principal is resolved from the session, passed as argument zero to every tool,
and never accepted as a model-supplied parameter.

Switching persona mid-demo is also the clearest possible evidence that the
boundary is real: the same question gets a different answer, and the difference
comes from the repository raising, not from the model choosing to decline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["customer", "internal"]
Role = Literal["customer_user", "support_agent", "support_manager"]

#: Read any account, not just your own.
READ_ANY = "read:any"
#: Retrieve documents the manifest marks DEPRECATED, with a warning attached.
READ_DEPRECATED = "read:deprecated"
#: Prepare a state-changing action for a human to confirm. Never execute one.
PROPOSE = "propose"
#: Run proactive issue detection across accounts.
SIGNALS = "signals"
#: Confirm a credit that exceeded the SOP's manager-approval threshold.
APPROVE_CREDIT = "approve:credit"


@dataclass(frozen=True)
class Principal:
    id: str
    display_name: str
    kind: Kind
    role: Role
    account_ids: frozenset[str]
    capabilities: frozenset[str]

    @property
    def reads_any_account(self) -> bool:
        return READ_ANY in self.capabilities

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def can_access(self, account_id: str) -> bool:
        return self.reads_any_account or account_id in self.account_ids

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind,
            "role": self.role,
            "account_ids": sorted(self.account_ids),
            "capabilities": sorted(self.capabilities),
        }


PERSONAS: dict[str, Principal] = {
    p.id: p
    for p in (
        Principal(
            id="cust-northstar",
            display_name="Northstar Logistics (customer)",
            kind="customer",
            role="customer_user",
            account_ids=frozenset({"ACCT-001"}),
            capabilities=frozenset({PROPOSE}),
        ),
        Principal(
            id="cust-lumenworks",
            display_name="LumenWorks (customer)",
            kind="customer",
            role="customer_user",
            account_ids=frozenset({"ACCT-002"}),
            capabilities=frozenset({PROPOSE}),
        ),
        Principal(
            id="cust-beacon",
            display_name="Beacon Retail (customer)",
            kind="customer",
            role="customer_user",
            account_ids=frozenset({"ACCT-003"}),
            capabilities=frozenset({PROPOSE}),
        ),
        Principal(
            id="cust-axis",
            display_name="Axis Labs (customer)",
            kind="customer",
            role="customer_user",
            account_ids=frozenset({"ACCT-004"}),
            capabilities=frozenset({PROPOSE}),
        ),
        Principal(
            id="staff-rohit",
            display_name="Rohit - support agent (internal)",
            kind="internal",
            role="support_agent",
            account_ids=frozenset(),
            capabilities=frozenset({PROPOSE, READ_ANY, SIGNALS}),
        ),
        Principal(
            id="staff-priya",
            display_name="Priya - support manager (internal)",
            kind="internal",
            role="support_manager",
            account_ids=frozenset(),
            capabilities=frozenset({PROPOSE, READ_ANY, SIGNALS, APPROVE_CREDIT, READ_DEPRECATED}),
        ),
    )
}

DEFAULT_PERSONA = "cust-northstar"


def get(principal_id: str) -> Principal:
    try:
        return PERSONAS[principal_id]
    except KeyError:
        raise LookupError(f"unknown persona: {principal_id!r}") from None
