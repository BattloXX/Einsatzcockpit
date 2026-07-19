"""Statisches OSM-Kartenbild für die Teams-Alarmkarte (und die öffentliche
Alarmübersicht) — rendert direkt aus OSM-Tiles, kein API-Key/externer Dienst nötig.

`staticmap` lädt Tiles synchron per `requests` — Aufrufer in async Kontext sollten
`render_incident_map_png` über `asyncio.to_thread` laufen lassen, damit der Event-Loop
nicht blockiert.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("einsatzleiter.staticmap")

# OSM-Tile-Nutzungsrichtlinie verlangt einen aussagekräftigen User-Agent (kein Default-Client).
_USER_AGENT = "Einsatzcockpit/1.0 (+https://einsatzcockpit.com)"
_MARKER_COLOR = "#d42225"  # Marken-Rot


def render_incident_map_png(
    lat: float, lng: float, *, zoom: int = 16, size: tuple[int, int] = (600, 360),
) -> bytes:
    """Rendert ein PNG-Kartenausschnitt um (lat, lng) mit einem roten Marker.

    Wirft bei Netzwerk-/Tile-Fehlern die zugrunde liegende Exception weiter — Aufrufer
    sollen das Kartenbild als optionalen Baustein behandeln (Karte ohne Bild versenden,
    statt den ganzen Alarm-Versand scheitern zu lassen).
    """
    import io

    from staticmap import CircleMarker, StaticMap

    width, height = size
    m = StaticMap(
        width, height,
        url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        headers={"User-Agent": _USER_AGENT},
    )
    m.add_marker(CircleMarker((lng, lat), _MARKER_COLOR, 14))
    image = m.render(zoom=zoom, center=(lng, lat))

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


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
        url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        headers={"User-Agent": _USER_AGENT},
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
