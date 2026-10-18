"""Wächter für async-Routen, die eine synchrone SQLAlchemy-Session verwenden."""

from __future__ import annotations

import inspect
from pathlib import Path

from fastapi.routing import APIRoute

from app.db import get_db
from app.main import app

BASELINE_PATH = Path(__file__).parent / "data" / "async_routes_sync_db_baseline.txt"


def _uses_get_db(endpoint: object) -> bool:
    """Prüft, ob die Route selbst eine Session per ``Depends(get_db)`` bekommt.

    Globale Dependencies (Auth/Tenant), die intern ``get_db`` nutzen, laufen als
    sync-Dependency im Threadpool und zählen nicht – entscheidend ist, ob der
    Routen-Körper mit der synchronen Session arbeitet.
    """
    for param in inspect.signature(endpoint).parameters.values():
        if getattr(param.default, "dependency", None) is get_db:
            return True
    return False


def _api_route_contexts() -> list[object]:
    """Liefert auch bei verschachtelt inkludierten Routern deren effektive APIRoutes."""
    contexts: list[object] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            contexts.append(route)
            continue
        effective_candidates = getattr(route, "effective_candidates", None)
        if effective_candidates is None:
            continue
        contexts.extend(
            candidate
            for candidate in effective_candidates()
            if isinstance(candidate.original_route, APIRoute)
        )
    return contexts


def _async_routes_with_sync_db() -> set[str]:
    return {
        f"{route.endpoint.__module__}:{route.endpoint.__qualname__}"
        for route in _api_route_contexts()
        if inspect.iscoroutinefunction(route.endpoint)
        and _uses_get_db(route.endpoint)
    }


def _baseline_entries() -> set[str]:
    return {
        line
        for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def test_no_new_async_routes_with_sync_db() -> None:
    found = _async_routes_with_sync_db()
    new_routes = found - _baseline_entries()

    assert not new_routes, (
        f"{len(found)} async+DB-Routen gefunden. Neue Einträge: {sorted(new_routes)}. "
        "Route als def schreiben, Broadcasts via app.core.async_bridge.run_async."
    )


def test_baseline_contains_only_existing_async_routes_with_sync_db() -> None:
    found = _async_routes_with_sync_db()
    obsolete_routes = _baseline_entries() - found

    assert not obsolete_routes, (
        f"{len(found)} async+DB-Routen gefunden. Nicht mehr vorhandene Baseline-Einträge: "
        f"{sorted(obsolete_routes)}. Aus Baseline entfernen."
    )
