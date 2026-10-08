"""Reine OSM-Abschnittsauflösung ohne Datenbank oder Netzwerk."""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import Any

from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import linemerge, nearest_points, substring, transform, unary_union

from app.services.osm_street_service import OsmNetwork, OsmWay, normalize_street_name
from app.services.road_closure_geo_service import _projector


@dataclass(frozen=True)
class Endpoint:
    kind: str
    value: str
    raw: str


@dataclass
class SectionResult:
    geometry: dict | None = None
    quality: str = "niedrig"
    geometry_status: str = "missing"
    osm_name: str | None = None
    length_m: float | None = None
    endpoints: list[dict] = field(default_factory=list)
    mehrdeutigkeiten: list[dict] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)
    osm_erreichbar: bool = True

    def to_dict(self) -> dict:
        return {
            "geometry": self.geometry,
            "quality": self.quality,
            "geometry_status": self.geometry_status,
            "osm_name": self.osm_name,
            "length_m": self.length_m,
            "endpoints": self.endpoints,
            "mehrdeutigkeiten": self.mehrdeutigkeiten,
            "hinweise": self.hinweise,
        }


_HOUSE = re.compile(r"^(?:(?:nr\.?|hausnummer|haus)\s*)?(\d+[a-z]?)$", re.I)
_PREFIX = re.compile(r"^(?:kreuzung(?:\s+mit)?|einmündung|einmuendung|ab|bis|höhe|hoehe|bei|ecke)\s+", re.I)
_RANKS = {"niedrig": 0, "mittel": 1, "hoch": 2}
WHOLE_STREET_MAX_M = 1500


def parse_endpoint(text: str | None, main_street: str = "") -> Endpoint:
    raw = (text or "").strip()
    if not raw:
        return Endpoint("none", "", raw)
    match = _HOUSE.match(raw)
    if match:
        return Endpoint("house", match.group(1).casefold(), raw)
    clean = _PREFIX.sub("", raw).strip()
    named = re.match(r"^(.+?)[, ]+(\d+[a-z]?)\s*$", clean, re.I)
    if named and not re.fullmatch(r"[A-Za-z]{1,3}", named.group(1).strip()):
        name, number = named.group(1).strip(), named.group(2).casefold()
        if normalize_street_name(name) == normalize_street_name(main_street):
            return Endpoint("house", number, raw)
        clean = name
    return Endpoint("cross", clean, raw)


def _cap(current: str, maximum: str) -> str:
    return current if _RANKS[current] <= _RANKS[maximum] else maximum


def _nodes(ways: list[OsmWay]) -> dict[int, tuple[float, float]]:
    return {node: coord for way in ways for node, coord in zip(way.node_ids, way.coords)}


def _connected(graph: dict[int, list[tuple[int, float]]]) -> bool:
    """True, wenn alle Knoten des Straßengraphen zusammenhängen."""
    if not graph:
        return False
    start = next(iter(graph))
    seen = {start}
    stack = [start]
    while stack:
        for neighbour, _cost in graph[stack.pop()]:
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return len(seen) == len(graph)


def _dijkstra(graph: dict[int, list[tuple[int, float]]], start: int, end: int) -> list[int] | None:
    queue = [(0.0, start)]
    distances = {start: 0.0}
    previous: dict[int, int] = {}
    while queue:
        distance, node = heapq.heappop(queue)
        if node == end:
            path = [node]
            while node in previous:
                node = previous[node]
                path.append(node)
            return list(reversed(path))
        if distance != distances[node]:
            continue
        for neighbour, cost in graph.get(node, []):
            candidate = distance + cost
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                previous[neighbour] = node
                heapq.heappush(queue, (candidate, neighbour))
    return None


def resolve(
    network: OsmNetwork,
    street_name: str,
    from_ep: Endpoint,
    to_ep: Endpoint,
    house_points: dict[str, tuple[float, float]],
) -> SectionResult:
    target = normalize_street_name(street_name)
    exact = [way for way in network.ways if normalize_street_name(way.name) == target]
    main = exact or [way for way in network.ways if target in normalize_street_name(way.name)]
    result = SectionResult()
    if not main:
        result.hinweise.append("Straße nicht in OSM gefunden")
        return result
    result.osm_name = max(main, key=lambda way: len(way.coords)).name
    quality = "hoch"
    if not exact and len({way.name for way in main}) > 1:
        result.mehrdeutigkeiten.append({"art": "strassenname", "text": street_name, "kandidaten": []})
        quality = "niedrig"
    coords = [coord for way in main for coord in way.coords]
    transformer = _projector(
        sum(coord[1] for coord in coords) / len(coords),
        sum(coord[0] for coord in coords) / len(coords),
    )
    inverse = Transformer.from_crs(transformer.target_crs, transformer.source_crs, always_xy=True)
    nodes = _nodes(main)
    metric_nodes = {
        node: transform(transformer.transform, Point(lng, lat)) for node, (lat, lng) in nodes.items()
    }
    graph: dict[int, list[tuple[int, float]]] = {node: [] for node in nodes}
    for way in main:
        for left, right in zip(way.node_ids, way.node_ids[1:]):
            if left in metric_nodes and right in metric_nodes:
                cost = metric_nodes[left].distance(metric_nodes[right])
                graph[left].append((right, cost))
                graph[right].append((left, cost))
    lines = [
        LineString([metric_nodes[node].coords[0] for node in way.node_ids if node in metric_nodes])
        for way in main
    ]
    unioned = unary_union(lines)
    merged = linemerge(unioned) if unioned.geom_type == "MultiLineString" else unioned
    components = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
    longest = max(components, key=lambda item: item.length)
    if len(components) > 1 and any(
        first.distance(second) > 200
        for index, first in enumerate(components)
        for second in components[index + 1:]
    ):
        candidates = []
        for index, line in enumerate(components):
            center = transform(inverse.transform, line.interpolate(.5, normalized=True))
            candidates.append(
                {"index": index, "laenge_m": round(line.length), "mittelpunkt": {"lat": center.y, "lng": center.x}}
            )
        result.mehrdeutigkeiten.append({"art": "mehrere_strassenzuege", "text": street_name, "kandidaten": candidates})
        quality = _cap(quality, "mittel")

    def cross_candidates(endpoint: Endpoint) -> list[int]:
        if endpoint.kind != "cross":
            return []
        cross = normalize_street_name(endpoint.value)
        others = [way for way in network.ways if way not in main and cross in normalize_street_name(way.name)]
        main_nodes = {node for way in main for node in way.node_ids}
        other_nodes = {node for way in others for node in way.node_ids}
        shared = list(main_nodes & other_nodes)
        if shared:
            return shared
        other_lines = [
            transform(transformer.transform, LineString([(lng, lat) for lat, lng in way.coords]))
            for way in others
        ]
        nearest = min(
            ((line.distance(other_line), line, other_line) for line in lines for other_line in other_lines),
            default=None,
            key=lambda item: item[0],
        )
        if nearest and nearest[0] <= 25:
            point = nearest_points(nearest[1], nearest[2])[0]
            result.hinweise.append(f"Kreuzung mit {endpoint.value} nur geometrisch ermittelt")
            return [min(metric_nodes, key=lambda node: point.distance(metric_nodes[node]))]
        return []

    from_cross, to_cross = cross_candidates(from_ep), cross_candidates(to_ep)

    def cross_choice(candidates: list[int], other: list[int], endpoint: Endpoint) -> int | None:
        nonlocal quality
        if not candidates:
            return None
        if len(candidates) > 1 and any(
            metric_nodes[candidates[0]].distance(metric_nodes[node]) > 50 for node in candidates[1:]
        ):
            result.mehrdeutigkeiten.append(
                {
                    "art": "kreuzung",
                    "text": endpoint.value,
                    "kandidaten": [{"lat": nodes[node][0], "lng": nodes[node][1]} for node in candidates],
                }
            )
            quality = _cap(quality, "mittel")
        if other:
            reachable = [
                (len(path), candidate)
                for candidate in candidates
                for other_node in other
                if (path := _dijkstra(graph, candidate, other_node)) is not None
            ]
            if reachable:
                return min(reachable)[1]
        return candidates[0]

    from_node = cross_choice(from_cross, to_cross, from_ep)
    to_node = cross_choice(to_cross, from_cross, to_ep)

    def endpoint(endpoint: Endpoint, role: str, node: int | None) -> tuple[int | None, Point | None, dict[str, Any]]:
        nonlocal quality
        data: dict[str, Any] = {
            "rolle": role,
            "methode": "nicht_gefunden",
            "text": endpoint.raw,
            "lat": None,
            "lng": None,
        }
        if endpoint.kind == "none":
            data["methode"] = "strassenende"
            return None, None, data
        if endpoint.kind == "cross":
            if node is None:
                result.hinweise.append(f"Querstraße {endpoint.value} nicht gefunden")
                return None, None, data
            if f"Kreuzung mit {endpoint.value} nur geometrisch ermittelt" in result.hinweise:
                quality = _cap(quality, "mittel")
            lat, lng = nodes[node]
            data.update({"methode": "kreuzung", "lat": lat, "lng": lng})
            return node, metric_nodes[node], data
        point = house_points.get(endpoint.value)
        if point is None:
            return None, None, data
        metric = transform(transformer.transform, Point(point[1], point[0]))
        street_distance = min(line.distance(metric) for line in lines)
        nearest = min(metric_nodes, key=lambda item: metric.distance(metric_nodes[item]))
        distance = street_distance
        if distance > 60:
            return None, None, data
        if distance > 30:
            quality = _cap(quality, "mittel")
        data.update({"methode": "hausnummer", "lat": point[0], "lng": point[1]})
        return nearest, metric, data

    first_node, first_point, first_data = endpoint(from_ep, "von", from_node)
    second_node, second_point, second_data = endpoint(to_ep, "bis", to_node)
    result.endpoints = [first_data, second_data]
    if first_node is not None and second_node is not None:
        path = _dijkstra(graph, first_node, second_node)
        if path is None:
            result.mehrdeutigkeiten.append({"art": "kein_zusammenhang", "text": street_name, "kandidaten": []})
            quality = _cap(quality, "niedrig")
            cut = longest
        else:
            if len(path) < 2:
                cut = longest
                quality = _cap(quality, "mittel")
            else:
                path_line = LineString([metric_nodes[node].coords[0] for node in path])
                start = path_line.project(first_point) if from_ep.kind == "house" and first_point else 0
                end = path_line.project(second_point) if to_ep.kind == "house" and second_point else path_line.length
                cut = substring(path_line, min(start, end), max(start, end))
    else:
        cut = longest
        hint = "Ganze Straße übernommen – bitte Abschnitt prüfen"
        if from_ep.kind == to_ep.kind == "none":
            connected = _connected(graph)
            if connected:
                # Ganze Straße inklusive aller Äste, nicht nur der längste Zug.
                cut = merged
            whole_high = (
                bool(exact)
                and connected
                and not result.mehrdeutigkeiten
                and cut.length <= WHOLE_STREET_MAX_M
            )
            if whole_high:
                quality = "hoch"
                result.hinweise.append("Ganze Straße übernommen")
            else:
                quality = _cap(quality, "mittel")
                result.hinweise.append(hint)
        else:
            found = sum(item["methode"] != "nicht_gefunden" for item in result.endpoints)
            if found == 0:
                result.hinweise.append("Abschnittsenden nicht gefunden – ganze Straße übernommen")
            else:
                result.hinweise.append("Nur ein Abschnittsende gefunden – ganze Straße übernommen")
            quality = _cap(quality, "mittel")
    if cut.is_empty or cut.length < 1:
        result.hinweise.append("Abschnitt konnte nicht aus der Straße ausgeschnitten werden")
        return result
    if any(item["methode"] == "nicht_gefunden" and item["text"] for item in result.endpoints):
        quality = _cap(quality, "mittel")
    output = transform(inverse.transform, cut)
    if output.geom_type == "MultiLineString":
        result.geometry = {
            "type": "MultiLineString",
            "coordinates": [[[round(x, 6), round(y, 6)] for x, y in part.coords] for part in output.geoms],
        }
    else:
        result.geometry = {
            "type": "LineString",
            "coordinates": [[round(x, 6), round(y, 6)] for x, y in output.coords],
        }
    result.length_m = round(cut.length, 1)
    both = first_node is not None and second_node is not None and not result.mehrdeutigkeiten
    whole_high = from_ep.kind == to_ep.kind == "none" and quality == "hoch"
    result.quality = "hoch" if (both or whole_high) and quality == "hoch" and 10 <= cut.length <= 5000 else quality
    result.geometry_status = "ok" if result.quality == "hoch" else "needs_review"
    return result
