"""Reine Geometrie-Hilfen fuer Strassensperren (GeoJSON nutzt [Laenge, Breite])."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from math import floor
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

ROUTE_BUFFER_M = 25
MIN_LINE_OVERLAP_M = 30
DESTINATION_RADIUS_M = 150
NEARBY_RADIUS_M = 500
AVOID_BUFFER_M = 10
_TYPES = {"Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def _coordinates(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            yield value
        else:
            for item in value:
                yield from _coordinates(item)


def validate_geometry(data: dict | str) -> dict:
    """Validiert und liefert eine eigenstaendige unterstuetzte GeoJSON-Geometrie."""
    value: Any = data
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Ungültiges GeoJSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Ungültiges GeoJSON.")
    if value.get("type") == "Feature":
        value = value.get("geometry")
    elif value.get("type") == "FeatureCollection":
        features = value.get("features")
        if not isinstance(features, list) or len(features) != 1:
            raise ValueError("Die FeatureCollection muss genau eine Geometrie enthalten.")
        value = features[0].get("geometry") if isinstance(features[0], dict) else None
    if not isinstance(value, dict) or value.get("type") not in _TYPES:
        raise ValueError("Nicht unterstützter GeoJSON-Geometrietyp.")
    coords = value.get("coordinates")
    if not isinstance(coords, list):
        raise ValueError("Ungültige GeoJSON-Koordinaten.")
    points = list(_coordinates(coords))
    if not points:
        raise ValueError("Die Geometrie enthält keine Koordinaten.")
    for point in points:
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point[:2]):
            raise ValueError("Ungültige GeoJSON-Koordinaten.")
        if not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
            raise ValueError("Koordinaten liegen außerhalb des gültigen Bereichs.")
    typ = value["type"]
    if typ == "LineString" and len(coords) < 2:
        raise ValueError("Eine Linie benötigt mindestens zwei Punkte.")
    if typ == "MultiLineString" and (not coords or any(not isinstance(line, list) or len(line) < 2 for line in coords)):
        raise ValueError("Jede Linie benötigt mindestens zwei Punkte.")
    if typ in {"Polygon", "MultiPolygon"}:
        rings = coords if typ == "Polygon" else [ring for polygon in coords for ring in polygon]
        if not rings or any(not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1] for ring in rings):
            raise ValueError("Polygon-Ringe benötigen mindestens vier geschlossene Punkte.")
    result = {"type": typ, "coordinates": coords}
    try:
        geometry = shape(result)
    except Exception as exc:
        raise ValueError("Ungültige GeoJSON-Geometrie.") from exc
    if geometry.is_empty or (typ in {"Polygon", "MultiPolygon"} and not geometry.is_valid):
        raise ValueError("Die Polygon-Geometrie ist ungültig.")
    return result


def bbox(geom: dict) -> tuple[float, float, float, float]:
    min_lng, min_lat, max_lng, max_lat = shape(geom).bounds
    return min_lat, min_lng, max_lat, max_lng


@lru_cache(maxsize=120)
def _transformer_for_zone(zone: int, south: bool) -> Transformer:
    return Transformer.from_crs("EPSG:4326", f"EPSG:{327 if south else 326}{zone:02d}", always_xy=True)


def _projector(lng: float, lat: float) -> Transformer:
    return _transformer_for_zone(max(1, min(60, floor((lng + 180) / 6) + 1)), lat < 0)


def to_metric(geom_dict: dict, transformer: Transformer):
    return transform(transformer.transform, shape(geom_dict))


def _metric_geometry(geom: dict):
    min_lat, min_lng, max_lat, max_lng = bbox(geom)
    transformer = _projector((min_lng + max_lng) / 2, (min_lat + max_lat) / 2)
    return to_metric(geom, transformer), transformer


def _route_overlap_m(route, clipped) -> float:
    """Ermittelt die laengs der Route ueberdeckte Strecke eines Linienabschnitts."""
    overlap = 0.0
    for line in getattr(clipped, "geoms", [clipped]):
        coordinates = list(getattr(line, "coords", []))
        if coordinates:
            positions = [route.project(Point(coordinate)) for coordinate in coordinates]
            overlap += max(positions) - min(positions)
    return overlap


@dataclass(frozen=True)
class ClosureRelevance:
    closure_id: int
    relevance: str
    distance_to_route_m: float | None
    distance_to_destination_m: float
    overlap_m: float | None


def classify(
    route: dict | None,
    destination: tuple[float, float],
    closures: list[tuple[int, dict]],
) -> list[ClosureRelevance]:
    dest_lat, dest_lng = destination
    transformer = _projector(dest_lng, dest_lat)
    destination_point = transform(transformer.transform, Point(dest_lng, dest_lat))
    route_metric = to_metric(validate_geometry(route), transformer) if route else None
    corridor = route_metric.buffer(ROUTE_BUFFER_M) if route_metric else None
    result: list[ClosureRelevance] = []
    for closure_id, raw in closures:
        geom = to_metric(validate_geometry(raw), transformer)
        d_route = geom.distance(route_metric) if route_metric else None
        d_dest = geom.distance(destination_point)
        overlap = None
        on_route = bool(corridor and geom.intersects(corridor))
        if on_route and geom.geom_type in {"LineString", "MultiLineString"}:
            overlap = _route_overlap_m(route_metric, geom.intersection(corridor))
            on_route = overlap >= MIN_LINE_OVERLAP_M or (
                geom.length < MIN_LINE_OVERLAP_M and overlap >= geom.length * 0.8
            )
        if on_route:
            relevance = "route"
        elif d_dest <= DESTINATION_RADIUS_M:
            relevance = "destination"
        elif min(d_dest, d_route if d_route is not None else d_dest) <= NEARBY_RADIUS_M:
            relevance = "nearby"
        else:
            relevance = None
        if relevance:
            result.append(
                ClosureRelevance(
                    closure_id,
                    relevance,
                    round(d_route) if d_route is not None else None,
                    round(d_dest),
                    round(overlap) if overlap is not None else None,
                )
            )
    return result


def avoid_polygon(geom: dict) -> dict:
    metric, transformer = _metric_geometry(validate_geometry(geom))
    inverse = Transformer.from_crs(transformer.target_crs, transformer.source_crs, always_xy=True)
    result = transform(inverse.transform, metric.buffer(AVOID_BUFFER_M))
    return dict(mapping(result))


def fingerprint(items: list[tuple[int, datetime | None]]) -> str:
    raw = "|".join(f"{item_id}:{value.isoformat() if value else ''}" for item_id, value in sorted(items))
    return hashlib.sha256(raw.encode()).hexdigest()


def distance_point_to_geometry_m(lat: float, lng: float, geom: dict) -> float:
    metric, transformer = _metric_geometry(validate_geometry(geom))
    return transform(transformer.transform, Point(lng, lat)).distance(metric)
