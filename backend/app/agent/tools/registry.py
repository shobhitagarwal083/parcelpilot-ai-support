"""The tool registry -- the security and correctness boundary.

Two decisions here are load-bearing.

**The principal is argument zero, injected server-side.** It is not in any tool's
JSON schema, so there is no shape of tool call the model can emit that names a
different one. Requirement 2 asks for enforcement in the tool layer rather than
in model instructions, and this is what that means concretely.

**Every persona sees the identical tool list.** Hiding `detect_issues` from
customers would be a prompt-level guard -- the model simply would not be told
about it -- and prompt-level guards are what the brief warns against. Instead
every tool is always offered and the capability is checked inside the handler,
so a customer that asks for proactive detection gets a structured refusal from
the data layer rather than a model that was never tempted.

It also keeps the serialised tool list byte-stable across personas, which is
what makes a cached prompt prefix possible if the provider ever supports one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.auth.principals import Principal
from app.auth.scope import AccessDenied, NotFound

ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    #: Human-readable label for the tool trace in the UI (requirement 6).
    trace_label: str = ""

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self.tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """Serialised deterministically -- sorted by name, never per-principal."""
        return [self.tools[name].schema() for name in sorted(self.tools)]

    def invoke(
        self,
        name: str,
        principal: Principal,
        arguments: dict[str, Any],
        *,
        as_of: datetime,
    ) -> dict[str, Any]:
        """Run a tool. Never raises: every failure comes back as a tool result.

        A malformed call or a denied one has to reach the model as data so it can
        correct course, and has to reach the trace so a human can see what was
        attempted. Raising would end the turn and hide both.
        """
        tool = self.get(name)
        if tool is None:
            return _error(
                f"No tool named {name!r}. Available tools: {', '.join(sorted(self.tools))}."
            )
        try:
            return tool.handler(principal, as_of=as_of, **arguments)
        except AccessDenied as exc:
            return _error(str(exc), kind="access_denied")
        except NotFound as exc:
            return _error(str(exc), kind="not_found")
        except TypeError as exc:
            return _error(f"Invalid arguments for {name}: {exc}", kind="bad_arguments")
        except ValueError as exc:
            return _error(str(exc), kind="bad_arguments")


def _error(message: str, *, kind: str = "error") -> dict[str, Any]:
    return {"is_error": True, "error_kind": kind, "error": message}


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """Tolerate the ways a model can mangle a JSON argument blob."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__unparsed__": raw}
    return parsed if isinstance(parsed, dict) else {"__unparsed__": raw}


REGISTRY = Registry()


def tool(
    name: str, description: str, parameters: dict[str, Any], trace_label: str = ""
) -> Callable[[ToolHandler], ToolHandler]:
    def decorate(handler: ToolHandler) -> ToolHandler:
        REGISTRY.register(
            Tool(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
                trace_label=trace_label or name.replace("_", " "),
            )
        )
        return handler

    return decorate


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
