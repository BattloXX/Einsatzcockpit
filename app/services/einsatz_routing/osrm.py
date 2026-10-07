"""OSRM-Adapter für Einsatz-Anfahrtsrouten."""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.services.einsatz_routing.base import (
    RouteResponse,
    RouteResult,
    RoutingError,
    compact_street_names,
)

logger = logging.getLogger("einsatzleiter.einsatz_routing")


class OsrmProvider:
    name = "osrm"
    supports_avoid_polygons = False

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self.api_url = settings.EINSATZ_ROUTING_API_URL.rstrip("/") or settings.ROUTING_OSRM_URL.rstrip("/")
        self.profile = settings.EINSATZ_ROUTING_PROFILE or "driving"

    async def calculate_route(
        self,
        start: tuple[float, float],
        dest: tuple[float, float],
        *,
        avoid_polygons: list[dict] | None = None,
        alternatives: bool = False,
    ) -> RouteResponse:
        del avoid_polygons
        coordinates = f"{start[1]},{start[0]};{dest[1]},{dest[0]}"
        url = f"{self.api_url}/route/v1/{self.profile}/{coordinates}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.EINSATZ_ROUTING_TIMEOUT_SECONDS,
                headers={"User-Agent": settings.ROUTING_USER_AGENT},
                transport=self.transport,
            ) as client:
                response = await client.get(
                    url,
                    params={
                        "overview": "full",
                        "geometries": "geojson",
                        "steps": "true",
                        "alternatives": str(alternatives).lower(),
                    },
                )
            if response.status_code != 200:
                # OSRM meldet "keine Route" als 400 mit JSON-Body; alles andere ist ein HTTP-Fehler.
                try:
                    fehler = response.json()
                except ValueError:
                    fehler = None
                if isinstance(fehler, dict) and fehler.get("code") == "NoRoute":
                    raise RoutingError("no_route")
                raise RoutingError("http", f"Der Routingdienst hat HTTP-Status {response.status_code} gemeldet.")
            data = _response_json(response)
            if data.get("code") == "NoRoute":
                raise RoutingError("no_route")
            result = _parse_response(data)
        except RoutingError as exc:
            logger.warning("Einsatz-Routing fehlgeschlagen: %s", exc.kind)
            raise
        except httpx.TimeoutException as exc:
            error = RoutingError("timeout")
            logger.warning("Einsatz-Routing fehlgeschlagen: %s", error.kind)
            raise error from exc
        except httpx.TransportError as exc:
            error = RoutingError("unreachable")
            logger.warning("Einsatz-Routing fehlgeschlagen: %s", error.kind)
            raise error from exc
        except (KeyError, TypeError, ValueError) as exc:
            error = RoutingError("invalid")
            logger.warning("Einsatz-Routing fehlgeschlagen: %s", error.kind)
            raise error from exc
        logger.info(
            "Einsatz-Routing erfolgreich: provider=%s distance_m=%s duration_s=%s",
            self.name,
            result.primary.distance_m,
            result.primary.duration_s,
        )
        return result


def _response_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise RoutingError("invalid") from exc
    if not isinstance(data, dict):
        raise RoutingError("invalid")
    return data


def _parse_response(data: dict) -> RouteResponse:
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        raise RoutingError("no_route")
    parsed = [_parse_route(route) for route in routes]
    return RouteResponse(parsed[0], parsed[1:], "osrm", False)


def _parse_route(route: object) -> RouteResult:
    if not isinstance(route, dict):
        raise ValueError("Ungültige OSRM-Route.")
    geometry = route["geometry"]
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise ValueError("Ungültige OSRM-Geometrie.")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("Ungültige OSRM-Geometrie.")
    names = [step.get("name") for leg in route.get("legs", []) for step in leg.get("steps", [])]
    return RouteResult(geometry, float(route["distance"]), float(route["duration"]), compact_street_names(names))
