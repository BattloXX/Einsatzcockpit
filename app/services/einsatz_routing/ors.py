"""openrouteservice-Adapter für Einsatz-Anfahrtsrouten."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.services.einsatz_routing.base import (
    RouteResponse,
    RouteResult,
    RoutingError,
    compact_street_names,
)

logger = logging.getLogger("einsatzleiter.einsatz_routing")
_DEFAULT_URL = "https://api.openrouteservice.org"


class OrsProvider:
    name = "ors"
    supports_avoid_polygons = True

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self.api_url = settings.EINSATZ_ROUTING_API_URL.rstrip("/") or _DEFAULT_URL
        self.api_key = settings.EINSATZ_ROUTING_API_KEY
        self.profile = settings.EINSATZ_ROUTING_PROFILE or "driving-car"

    async def calculate_route(
        self,
        start: tuple[float, float],
        dest: tuple[float, float],
        *,
        avoid_polygons: list[dict] | None = None,
        alternatives: bool = False,
    ) -> RouteResponse:
        if self.api_url == _DEFAULT_URL and not self.api_key:
            error = RoutingError("disabled")
            logger.warning("Einsatz-Routing fehlgeschlagen: %s", error.kind)
            raise error
        payload: dict[str, Any] = {
            "coordinates": [[start[1], start[0]], [dest[1], dest[0]]],
            "instructions": True,
            "language": "de",
            "units": "m",
        }
        if avoid_polygons:
            try:
                payload["options"] = {"avoid_polygons": _multi_polygon(avoid_polygons)}
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("Einsatz-Routing fehlgeschlagen: invalid")
                raise RoutingError("invalid", "Ungültige Sperrfläche für das Routing.") from exc
        if alternatives:
            payload["alternative_routes"] = {
                "target_count": 2,
                "share_factor": 0.6,
                "weight_factor": 1.6,
            }
        url = f"{self.api_url}/v2/directions/{self.profile}/geojson"
        try:
            async with httpx.AsyncClient(
                timeout=settings.EINSATZ_ROUTING_TIMEOUT_SECONDS,
                headers={"User-Agent": settings.ROUTING_USER_AGENT, "Authorization": self.api_key},
                transport=self.transport,
            ) as client:
                response = await client.post(url, json=payload)
            if response.status_code == 404:
                raise RoutingError("no_route")
            if response.status_code != 200:
                # ORS meldet "keine Route" mit Fehlercode 2009 im JSON-Body.
                try:
                    fehler = response.json()
                except ValueError:
                    fehler = None
                fehler_info = fehler.get("error") if isinstance(fehler, dict) else None
                if isinstance(fehler_info, dict) and fehler_info.get("code") == 2009:
                    raise RoutingError("no_route")
                raise RoutingError("http", f"Der Routingdienst hat HTTP-Status {response.status_code} gemeldet.")
            data = _response_json(response)
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


def _multi_polygon(geometries: list[dict]) -> dict:
    polygons: list[list] = []
    for geometry in geometries:
        if geometry.get("type") == "Polygon":
            polygons.append(geometry["coordinates"])
        elif geometry.get("type") == "MultiPolygon":
            polygons.extend(geometry["coordinates"])
        else:
            raise ValueError("Sperrfläche ist kein Polygon.")
    return {"type": "MultiPolygon", "coordinates": polygons}


def _response_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise RoutingError("invalid") from exc
    if not isinstance(data, dict):
        raise RoutingError("invalid")
    return data


def _parse_response(data: dict) -> RouteResponse:
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise RoutingError("no_route")
    routes = [_parse_feature(feature) for feature in features]
    return RouteResponse(routes[0], routes[1:], "ors", True)


def _parse_feature(feature: object) -> RouteResult:
    if not isinstance(feature, dict):
        raise ValueError("Ungültiges ORS-Feature.")
    geometry = feature["geometry"]
    properties = feature["properties"]
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise ValueError("Ungültige ORS-Geometrie.")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("Ungültige ORS-Geometrie.")
    summary = properties["summary"]
    names = [step.get("name") for segment in properties.get("segments", []) for step in segment.get("steps", [])]
    return RouteResult(geometry, float(summary["distance"]), float(summary["duration"]), compact_street_names(names))
