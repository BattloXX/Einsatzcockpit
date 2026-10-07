"""Gemeinsame Typen und Auswahlregeln für Einsatz-Anfahrtsrouting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services import road_closure_geo_service


class RoutingError(Exception):
    """Ein erwarteter, für die Aufrufer sicher behandelbarer Routingfehler."""

    def __init__(self, kind: str, message: str | None = None) -> None:
        self.kind = kind
        messages = {
            "timeout": "Der Routingdienst hat nicht rechtzeitig geantwortet.",
            "unreachable": "Der Routingdienst ist nicht erreichbar.",
            "http": "Der Routingdienst hat einen HTTP-Fehler gemeldet.",
            "invalid": "Der Routingdienst hat eine ungültige Antwort geliefert.",
            "disabled": "Einsatz-Routing ist deaktiviert.",
            "no_route": "Es wurde keine befahrbare Route gefunden.",
        }
        super().__init__(message or messages.get(kind, "Routing fehlgeschlagen."))


@dataclass
class RouteResult:
    geometry: dict
    distance_m: float
    duration_s: float
    street_names: list[str]


@dataclass
class RouteResponse:
    primary: RouteResult
    alternatives: list[RouteResult]
    provider: str
    avoid_supported: bool


class RoutingProvider(Protocol):
    name: str
    supports_avoid_polygons: bool

    async def calculate_route(
        self,
        start: tuple[float, float],
        dest: tuple[float, float],
        *,
        avoid_polygons: list[dict] | None = None,
        alternatives: bool = False,
    ) -> RouteResponse: ...


def compact_street_names(names: list[object]) -> list[str]:
    """Entfernt leere Namen und unmittelbar wiederholte Straßennamen."""
    result: list[str] = []
    for raw in names:
        name = raw.strip() if isinstance(raw, str) else ""
        if name and (not result or result[-1] != name):
            result.append(name)
    return result


def choose_alternative(response: RouteResponse, closure_geometries: list[dict]) -> RouteResult | None:
    """Liefert die erste Route, die nicht durch eine Sperre führt."""
    routes = [response.primary, *response.alternatives]
    closures = list(enumerate(closure_geometries))
    for route in routes:
        coordinates = route.geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            continue
        last = coordinates[-1]
        if not isinstance(last, (list, tuple)) or len(last) < 2:
            continue
        destination = (float(last[1]), float(last[0]))
        relevance = road_closure_geo_service.classify(route.geometry, destination, closures)
        if not any(item.relevance == "route" for item in relevance):
            return route
    return None
