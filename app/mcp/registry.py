"""Registrierung von MCP-Tools mit deklarativen Live-Berechtigungen."""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    required_roles: tuple[str, ...]
    module_check: Callable[[int, Any], bool] | None
    handler: Callable[[Any], Awaitable[dict[str, object]]]


TOOLS: dict[str, ToolDefinition] = {}


def register_tool(
    *,
    name: str,
    description: str,
    required_roles: tuple[str, ...] = (),
    module_check: Callable[[int, Any], bool] | None = None,
):
    def decorator(
        handler: Callable[[Any], Awaitable[dict[str, object]]],
    ) -> Callable[[Any], Awaitable[dict[str, object]]]:
        TOOLS[name] = ToolDefinition(name, description, required_roles, module_check, handler)
        return handler

    return decorator
