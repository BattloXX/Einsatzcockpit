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
    handler: Callable[..., Awaitable[dict[str, object]]]
    hauptloop: bool = False


TOOLS: dict[str, ToolDefinition] = {}


def register_tool(
    *,
    name: str,
    description: str,
    required_roles: tuple[str, ...] = (),
    module_check: Callable[[int, Any], bool] | None = None,
    hauptloop: bool = False,
):
    # Standard: eigener Event-Loop im Worker fuer synchrone DB-Arbeit.
    # hauptloop=True bei direkten ODER indirekten Awaits auf WebSocket-Broadcasts,
    # SMS/Gateway-Versand, geteilte Async-Clients oder globale Lock/Event/Queue.
    # Pro Aufruf erzeugte AsyncClients sind im Worker erlaubt; Services mitpruefen.
    # Im Zweifel hauptloop=True setzen. Kontext-Laden bleibt immer im Worker.
    def decorator(
        handler: Callable[..., Awaitable[dict[str, object]]],
    ) -> Callable[..., Awaitable[dict[str, object]]]:
        TOOLS[name] = ToolDefinition(name, description, required_roles, module_check, handler, hauptloop)
        return handler

    return decorator
