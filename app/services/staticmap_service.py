"""Statisches OSM-Kartenbild für die Teams-Alarmkarte (und die öffentliche
Alarmübersicht) — rendert direkt aus OSM-Tiles, kein API-Key/externer Dienst nötig.

`staticmap` lädt Tiles synchron per `requests` — Aufrufer in async Kontext sollten
`render_incident_map_png` über `asyncio.to_thread` laufen lassen, damit der Event-Loop
nicht blockiert.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict

from app.core.map_config import OSM_TILE_URL, OSM_TILE_USER_AGENT

logger = logging.getLogger("einsatzleiter.staticmap")

_MARKER_COLOR = "#d42225"  # Marken-Rot
_RENDER_CACHE: OrderedDict[tuple[float, float, int, tuple[int, int]], bytes] = OrderedDict()
_RENDER_CACHE_MAXSIZE = 128
_ROAD_CLOSURE_RENDER_CACHE: OrderedDict[tuple[str, tuple[int, int]], bytes] = OrderedDict()


def render_incident_map_png(
    lat: float, lng: float, *, zoom: int = 16, size: tuple[int, int] = (600, 360),
) -> bytes:
    """Rendert ein PNG-Kartenausschnitt um (lat, lng) mit einem roten Marker.

    Wiederholte Koordinaten werden gecacht, weil die OSM-Tile-Nutzungsrichtlinie
    wiederholtes Nachladen derselben Tiles untersagt und Ausfaelle abgefedert werden.
    Wirft bei Netzwerk-/Tile-Fehlern die zugrunde liegende Exception weiter — Aufrufer
    sollen das Kartenbild als optionalen Baustein behandeln (Karte ohne Bild versenden,
    statt den ganzen Alarm-Versand scheitern zu lassen).
    """
    schluessel = (round(lat, 5), round(lng, 5), zoom, size)
    if schluessel in _RENDER_CACHE:
        _RENDER_CACHE.move_to_end(schluessel)
        return _RENDER_CACHE[schluessel]

    import io

    from staticmap import CircleMarker, StaticMap

    width, height = size
    m = StaticMap(
        width, height,
        url_template=OSM_TILE_URL,
        headers={"User-Agent": OSM_TILE_USER_AGENT},
    )
    m.add_marker(CircleMarker((lng, lat), _MARKER_COLOR, 14))
    image = m.render(zoom=zoom, center=(lng, lat))

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    png = buf.getvalue()
    _RENDER_CACHE[schluessel] = png
    _RENDER_CACHE.move_to_end(schluessel)
    if len(_RENDER_CACHE) > _RENDER_CACHE_MAXSIZE:
        _RENDER_CACHE.popitem(last=False)
    return png


def render_route_map_png(
    route: list[tuple[float, float]] | None,
    marker: list[dict] | None,
    *, size: tuple[int, int] = (620, 380),
) -> bytes:
    """Rendert ein PNG mit einer Förderstrecke: Wegstrecken-Linie + Pumpen-/Zielmarker.

    `route`: Stützpunkte der Strecke als [(lat, lng), …] (Wegstrecken-Linie).
    `marker`: Punkte als [{"lat", "lng", "color"?, "radius"?}, …] (Pumpen, Ziel).
    Zoom/Zentrum werden von `staticmap` automatisch so gewählt, dass alle Elemente
    ins Bild passen — dadurch erscheinen alle Pumpenstandorte (nicht nur der erste)
    und der Streckenverlauf. Wirft bei Tile-/Netzfehlern weiter (optionaler Baustein).
    """
    import io

    from staticmap import CircleMarker, Line, StaticMap

    width, height = size
    m = StaticMap(
        width, height,
        url_template=OSM_TILE_URL,
        headers={"User-Agent": OSM_TILE_USER_AGENT},
        padding_x=30, padding_y=30,
    )
    if route and len(route) >= 2:
        # staticmap erwartet (lng, lat)
        m.add_line(Line([(lng, lat) for lat, lng in route], "#2563eb", 4))
    for p in (marker or []):
        if p.get("lat") is None or p.get("lng") is None:
            continue
        m.add_marker(CircleMarker(
            (float(p["lng"]), float(p["lat"])),
            p.get("color") or _MARKER_COLOR, int(p.get("radius") or 13)))
    image = m.render()

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def render_road_closure_map_png(geometry: dict, *, size: tuple[int, int] = (800, 450)) -> bytes:
    """Render a road-closure GeoJSON geometry, cached independently from incidents."""
    key = (hashlib.sha1(json.dumps(geometry, sort_keys=True).encode()).hexdigest(), size)
    if key in _ROAD_CLOSURE_RENDER_CACHE:
        _ROAD_CLOSURE_RENDER_CACHE.move_to_end(key)
        return _ROAD_CLOSURE_RENDER_CACHE[key]
    import io

    from staticmap import CircleMarker, Line, Polygon, StaticMap
    width, height = size
    m = StaticMap(width, height, url_template=OSM_TILE_URL, headers={"User-Agent": OSM_TILE_USER_AGENT})
    typ = geometry.get("type")
    coordinates: list = geometry.get("coordinates") or []
    lines: list = []
    if typ == "MultiLineString":
        lines = coordinates
    elif typ == "LineString":
        lines = [coordinates]
    for line in lines:
        points = [(float(x), float(y)) for x, y in line]
        m.add_line(Line(points, "#ffffff", 10))
        m.add_line(Line(points, "#d32f2f", 6))
    polygons: list = []
    if typ == "Polygon":
        polygons = coordinates
    elif typ == "MultiPolygon":
        polygons = [ring for polygon in coordinates for ring in polygon]
    for ring in polygons:
        m.add_polygon(Polygon([(float(x), float(y)) for x, y in ring], "#d32f2f55", "#d32f2f", 3))
    if typ == "Point" and coordinates:
        m.add_marker(CircleMarker((float(coordinates[0]), float(coordinates[1])), "#d32f2f", 14))
        image = m.render(zoom=16, center=(float(coordinates[0]), float(coordinates[1])))
    else:
        image = m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = buf.getvalue()
    _ROAD_CLOSURE_RENDER_CACHE[key] = result
    if len(_ROAD_CLOSURE_RENDER_CACHE) > 64:
        _ROAD_CLOSURE_RENDER_CACHE.popitem(last=False)
    return result
