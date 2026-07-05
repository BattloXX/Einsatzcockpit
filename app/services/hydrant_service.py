"""Hydranten- / Löschwasser-Layer (OpenStreetMap / OSMHydrant).

Fragt Löschwasser-Entnahmestellen (`emergency=fire_hydrant`, dazu Saugstellen,
Löschteiche, Löschwasserbehälter) server-seitig über die Overpass-API ab. Der
Server-Proxy vermeidet CSP-/Offline-Probleme im Browser und respektiert die
Overpass-Fair-Use-Policy (fester User-Agent, Timeout, kurzer Cache).

`typ` in der Ausgabe ist normalisiert:
  - ueberflur   (pillar / Überflurhydrant)
  - unterflur   (underground / Unterflurhydrant)
  - loeschwasser (Saugstelle / Löschteich / Behälter / sonstige Wasserquelle)

Distanz/Richtung werden relativ zum Bezugspunkt (Einsatz-/Objektkoordinate) berechnet.
"""
from __future__ import annotations

import logging
import math
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# In-Memory-Cache: key = (lat_gerundet, lng_gerundet, radius) → (timestamp, liste)
_cache: dict[tuple[float, float, int], tuple[float, list[dict]]] = {}

_HIMMELSRICHTUNGEN = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _richtung(lat1: float, lng1: float, lat2: float, lng2: float) -> str:
    """Grobe Himmelsrichtung vom Bezugspunkt (1) zum Ziel (2)."""
    dlng = math.radians(lng2 - lng1)
    y = math.sin(dlng) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlng))
    grad = (math.degrees(math.atan2(y, x)) + 360) % 360
    return _HIMMELSRICHTUNGEN[int((grad + 22.5) % 360 // 45)]


def _typ_aus_tags(tags: dict) -> str:
    emergency = tags.get("emergency", "")
    if emergency == "fire_hydrant":
        ht = (tags.get("fire_hydrant:type") or "").lower()
        if ht in ("underground", "wall"):
            return "unterflur"
        return "ueberflur"  # pillar / pipe / unbekannt → Überflur (häufigster Fall)
    # Saugstelle, Löschteich, Löschwasserbehälter, Wasserquelle
    return "loeschwasser"


def _ref_aus_tags(tags: dict) -> str | None:
    for key in ("ref", "fire_hydrant:diameter", "name"):
        if tags.get(key):
            val = str(tags[key])
            if key == "fire_hydrant:diameter":
                return "DN " + val
            return val
    return None


def _overpass_query(lat: float, lng: float, radius_m: int) -> str:
    r = int(radius_m)
    return (
        "[out:json][timeout:25];("
        f'node["emergency"="fire_hydrant"](around:{r},{lat},{lng});'
        f'node["emergency"="suction_point"](around:{r},{lat},{lng});'
        f'node["emergency"="fire_water_pond"](around:{r},{lat},{lng});'
        f'node["emergency"="water_tank"](around:{r},{lat},{lng});'
        f'node["fire_hydrant:type"](around:{r},{lat},{lng});'
        ");out body {};".format(settings.HYDRANT_MAX * 2)
    )


async def fetch_osm_hydranten(lat: float, lng: float, radius_m: int | None = None) -> list[dict]:
    """Holt OSM-Hydranten im Umkreis (mit TTL-Cache). Gibt [] bei Fehler zurück.

    Rückgabe je Eintrag: {id, lat, lng, typ, ref, entfernung_m, richtung, quelle}.
    """
    radius = int(radius_m or settings.HYDRANT_RADIUS_M)
    ckey = (round(lat, 4), round(lng, 4), radius)
    now = time.time()
    cached = _cache.get(ckey)
    if cached and (now - cached[0]) < settings.HYDRANT_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.HYDRANT_USER_AGENT},
            timeout=settings.HYDRANT_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.post(
                settings.HYDRANT_OVERPASS_URL,
                data={"data": _overpass_query(lat, lng, radius)},
            )
            resp.raise_for_status()
            daten = resp.json()
    except Exception as exc:  # Overpass down / Timeout / Netzfehler → leer, Aufrufer nutzt Fallback
        logger.warning("Overpass-Hydrantenabfrage fehlgeschlagen: %s", exc)
        return []

    ergebnisse = parse_overpass_elements(daten.get("elements", []), lat, lng)
    _cache[ckey] = (now, ergebnisse)
    return ergebnisse


def parse_overpass_elements(elements: list[dict], lat: float, lng: float) -> list[dict]:
    """Overpass-Elemente → normalisierte Hydranten-Liste (sortiert, gedeckelt).

    Dedupliziert nach OSM-ID (Union-Queries können ein Element mehrfach liefern).
    """
    ergebnisse: list[dict] = []
    gesehen: set = set()
    for el in elements:
        e_lat, e_lng = el.get("lat"), el.get("lon")
        oid = el.get("id")
        if e_lat is None or e_lng is None or oid in gesehen:
            continue
        gesehen.add(oid)
        tags = el.get("tags", {})
        ergebnisse.append({
            "id": "osm-" + str(oid),
            "lat": e_lat,
            "lng": e_lng,
            "typ": _typ_aus_tags(tags),
            "ref": _ref_aus_tags(tags),
            "entfernung_m": int(round(_haversine_m(lat, lng, e_lat, e_lng))),
            "richtung": _richtung(lat, lng, e_lat, e_lng),
            "quelle": "osm",
        })
    ergebnisse.sort(key=lambda h: h["entfernung_m"])
    return ergebnisse[: settings.HYDRANT_MAX]


def manuelle_objekt_hydranten(karten_objekte, ref_lat: float | None, ref_lng: float | None) -> list[dict]:
    """Manuell in Objekten gesetzte Hydrant-Symbole (ObjektKartenObjekt) als Hydranten-Einträge.

    `karten_objekte`: Iterable von ObjektKartenObjekt mit typ hydrant_ueberflur/hydrant_unterflur.
    """
    _MAP = {"hydrant_ueberflur": "ueberflur", "hydrant_unterflur": "unterflur"}
    out: list[dict] = []
    for k in karten_objekte:
        typ = _MAP.get(k.typ)
        if typ is None or k.lat is None or k.lng is None:
            continue
        eintrag = {
            "id": "objekt-" + str(k.id),
            "lat": k.lat,
            "lng": k.lng,
            "typ": typ,
            "ref": k.label,
            "entfernung_m": None,
            "richtung": None,
            "quelle": "objekt",
        }
        if ref_lat is not None and ref_lng is not None:
            eintrag["entfernung_m"] = int(round(_haversine_m(ref_lat, ref_lng, k.lat, k.lng)))
            eintrag["richtung"] = _richtung(ref_lat, ref_lng, k.lat, k.lng)
        out.append(eintrag)
    return out


def merge_hydranten(osm: list[dict], manuell: list[dict]) -> list[dict]:
    """Vereinigt OSM- und manuelle Hydranten, sortiert nach Entfernung (None ans Ende)."""
    alle = list(osm) + list(manuell)
    alle.sort(key=lambda h: (h["entfernung_m"] is None, h["entfernung_m"] or 0))
    return alle
