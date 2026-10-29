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

import asyncio
import hashlib
import logging
import math
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from weakref import WeakKeyDictionary

import httpx

from app.config import settings
from app.services import shared_cache

logger = logging.getLogger(__name__)

# Lokaler TTL-Cache und Sperren je Event-Loop.
_cache: dict[str, tuple[dict, float]] = {}
_flights: WeakKeyDictionary = WeakKeyDictionary()

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


def _overpass_query(lat: float, lng: float, radius_m: int, max_results: int) -> str:
    r = int(radius_m)
    return (
        "[out:json][timeout:25];("
        f'node["emergency"="fire_hydrant"](around:{r},{lat},{lng});'
        f'node["emergency"="suction_point"](around:{r},{lat},{lng});'
        f'node["emergency"="fire_water_pond"](around:{r},{lat},{lng});'
        f'node["emergency"="water_tank"](around:{r},{lat},{lng});'
        f'node["fire_hydrant:type"](around:{r},{lat},{lng});'
        ");out body {};".format(int(max_results) * 2)
    )


@dataclass(frozen=True)
class HydrantErgebnis:
    """Hydranten samt Herkunft und Zeitpunkt der letzten erfolgreichen Abfrage."""

    items: list[dict]
    veraltet: bool
    stand: datetime | None
    quelle: str


def _lokal_lesen(schluessel: str):
    eintrag = _cache.get(schluessel)
    if eintrag and eintrag[1] > time.time():
        return eintrag[0]
    _cache.pop(schluessel, None)
    return None


def _lokal_setzen(schluessel: str, wert: dict, ttl: float):
    # Abgelaufene Eintraege entfernen und auch bei vielen Koordinaten begrenzen.
    if len(_cache) >= 2048:
        jetzt = time.time()
        for key in list(_cache):
            if _cache[key][1] <= jetzt:
                _cache.pop(key, None)
        while len(_cache) >= 2048:
            _cache.pop(next(iter(_cache)))
    _cache[schluessel] = (wert, time.time() + ttl)


async def _redis_lesen(schluessel: str):
    if settings.REDIS_URL:
        try:
            return await shared_cache.get_entry(schluessel)
        except Exception:
            shared_cache.redis_error()
    return None


async def _lesen(schluessel: str, *, redis_zuerst: bool = False):
    lokal = _lokal_lesen(schluessel)
    if lokal is not None and not redis_zuerst:
        return lokal
    eintrag = await _redis_lesen(schluessel)
    if eintrag is not None:
        wert, rest = eintrag
        _lokal_setzen(schluessel, wert, rest)
        return wert
    return lokal


async def _setzen(schluessel: str, wert: dict, ttl: int):
    _lokal_setzen(schluessel, wert, ttl)
    if settings.REDIS_URL:
        try:
            await shared_cache.set(schluessel, wert, ttl)
        except Exception:
            shared_cache.redis_error()


@asynccontextmanager
async def _worker_sperre(schluessel: str):
    if not settings.REDIS_URL:
        yield
        return
    kontext = shared_cache.lock(schluessel)
    try:
        await kontext.__aenter__()
    except shared_cache.LockTimeout:
        raise
    except Exception:
        shared_cache.redis_error()
        yield
        return
    try:
        yield
    finally:
        try:
            await kontext.__aexit__(None, None, None)
        except Exception:
            shared_cache.redis_error()


@asynccontextmanager
async def _sperre(schluessel: str):
    sperren = _flights.setdefault(asyncio.get_running_loop(), {})
    # Benutzer zaehlen, damit eine Sperre mit Wartenden nie entfernt wird.
    if schluessel not in sperren:
        for key, (_, nutzer) in list(sperren.items()):
            if nutzer == 0:
                del sperren[key]
                if len(sperren) < 1024:
                    break
        sperren[schluessel] = (asyncio.Lock(), 0)
    sperre, nutzer = sperren[schluessel]
    sperren[schluessel] = (sperre, nutzer + 1)
    try:
        async with sperre:
            async with _worker_sperre(schluessel):
                yield
    finally:
        sperren[schluessel] = (sperre, sperren[schluessel][1] - 1)
        if sperren[schluessel][1] == 0:
            del sperren[schluessel]


def _als_ergebnis(eintrag: dict | None, quelle: str) -> HydrantErgebnis:
    if eintrag is None:
        return HydrantErgebnis([], True, None, "leer")
    return HydrantErgebnis(eintrag["items"], quelle == "veraltet", eintrag["stand"], quelle)


async def _frisch(schluessel: str) -> HydrantErgebnis | None:
    lokal = _lokal_lesen(schluessel)
    if lokal is not None:
        return _als_ergebnis(lokal, "cache")
    eintrag = await _redis_lesen(schluessel)
    if eintrag is not None:
        wert, rest = eintrag
        _lokal_setzen(schluessel, wert, rest)
        alt_key = schluessel.replace("hydranten:", "hydranten-stale:", 1)
        rest_alt = settings.HYDRANT_STALE_TTL_SECONDS - (time.time() - wert["stand"].timestamp())
        if rest_alt > 0:
            _lokal_setzen(alt_key, wert, rest_alt)
        return _als_ergebnis(wert, "redis")
    return None


def _wartezeit(exc: Exception, anzahl: int) -> int:
    from app.services.overpass_endpunkte import OverpassNichtVerfuegbar

    if isinstance(exc, OverpassNichtVerfuegbar) and exc.letzter_fehler is not None:
        exc = exc.letzter_fehler
    maximum = settings.HYDRANT_BACKOFF_MAX_SECONDS
    basis = min(maximum, 60 * 2 ** min(anzahl - 1, 10))
    wartezeit = basis * random.uniform(0.8, 1.2)
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry = exc.response.headers.get("Retry-After", "")
        try:
            sekunden = float(retry)
        except ValueError:
            try:
                sekunden = parsedate_to_datetime(retry).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                sekunden = 0
        # Retry-After ist eine Mindestwartezeit, auch bei negativem Jitter.
        wartezeit = max(wartezeit, sekunden)
    return max(1, math.ceil(min(maximum, wartezeit)))


async def _fehler(schluessel: str, breaker_key: str, zustand: dict, exc: Exception, start: float):
    from app.services.overpass_endpunkte import OverpassNichtVerfuegbar

    ursache = exc.letzter_fehler if isinstance(exc, OverpassNichtVerfuegbar) else exc
    logger.warning("Overpass-Hydrantenabfrage fehlgeschlagen: klasse=%s dauer=%.3fs",
                   type(ursache).__name__, time.monotonic() - start)
    jetzt = time.time()
    vorher = await _lesen(schluessel + ":negativ") or {}
    anzahl = vorher.get("anzahl", 0) + 1
    pause = _wartezeit(exc, anzahl)
    await _setzen(schluessel + ":negativ", {"anzahl": anzahl, "bis": jetzt + pause},
                  max(settings.HYDRANT_BACKOFF_MAX_SECONDS * 2, pause))
    fehler = [zeit for zeit in zustand.get("fehler", [])
              if jetzt - zeit <= settings.HYDRANT_BREAKER_WINDOW_SECONDS]
    fehler.append(jetzt)
    offen = zustand.get("offen_bis", 0)
    if len(fehler) >= settings.HYDRANT_BREAKER_FAILS or offen:
        if not offen or offen <= jetzt:
            logger.warning("Hydranten-Breaker offen")
        offen = jetzt + settings.HYDRANT_BREAKER_OPEN_SECONDS
    await _setzen(breaker_key, {"fehler": fehler, "offen_bis": offen},
                  max(settings.HYDRANT_BREAKER_WINDOW_SECONDS, settings.HYDRANT_BREAKER_OPEN_SECONDS) * 2)


async def fetch_osm_hydranten_ergebnis(
    lat: float, lng: float, radius_m: int | None = None, max_results: int | None = None,
) -> HydrantErgebnis:
    """Begrenzt Wartezeit, buendelt Abfragen und liefert gekennzeichnete Altdaten."""
    radius = int(radius_m or settings.HYDRANT_RADIUS_M)
    limit = int(max_results or settings.HYDRANT_MAX)
    schluessel = f"hydranten:{lat:.4f}:{lng:.4f}:{radius}:{limit}"
    alt_key = schluessel.replace("hydranten:", "hydranten-stale:", 1)
    # URL nur gehasht ablegen, keine moeglichen Zugangsdaten im Schluessel.
    breaker_key = "hydranten-breaker:" + hashlib.sha256(settings.HYDRANT_OVERPASS_URL.encode()).hexdigest()
    alt = _lokal_lesen(alt_key)
    start = time.monotonic()
    try:
        async with asyncio.timeout(settings.HYDRANT_TOTAL_TIMEOUT_SECONDS):
            frisch = await _frisch(schluessel)
            if frisch is not None:
                return frisch
            alt = await _lesen(alt_key, redis_zuerst=True)
            negativ = await _lesen(schluessel + ":negativ")
            if negativ and negativ["bis"] > time.time():
                return _als_ergebnis(alt, "veraltet")
            async with _sperre(schluessel):
                frisch = await _frisch(schluessel)
                if frisch is not None:
                    return frisch
                # Globale Sperre schuetzt Fehlerzaehler und genau eine Half-Open-Probe.
                async with _sperre(breaker_key):
                    negativ = await _lesen(schluessel + ":negativ", redis_zuerst=True)
                    zustand = await _lesen(breaker_key, redis_zuerst=True) or {}
                    if ((negativ and negativ["bis"] > time.time())
                            or zustand.get("offen_bis", 0) > time.time()):
                        return _als_ergebnis(alt, "veraltet")
                    try:
                        # Reserve fuer Fehlercache und Redis innerhalb des Gesamtbudgets.
                        rest = settings.HYDRANT_TOTAL_TIMEOUT_SECONDS - (time.monotonic() - start)
                        async with asyncio.timeout(max(0, rest * 0.8)):
                            from app.services.overpass_endpunkte import abfragen

                            daten = await abfragen(
                                _overpass_query(lat, lng, radius, limit),
                                primaer=settings.HYDRANT_OVERPASS_URL,
                                timeout_je_versuch=settings.HYDRANT_TIMEOUT_SECONDS,
                                budget_s=rest * 0.8, zweck="hydranten",
                            )
                        items = parse_overpass_elements(daten.get("elements", []), lat, lng, max_results=limit)
                    except Exception as exc:
                        await _fehler(schluessel, breaker_key, zustand, exc, start)
                        return _als_ergebnis(alt, "veraltet")
                    if zustand.get("offen_bis"):
                        logger.info("Hydranten-Breaker geschlossen")
                    fehler = ([] if zustand.get("offen_bis") else [
                        zeit for zeit in zustand.get("fehler", [])
                        if time.time() - zeit <= settings.HYDRANT_BREAKER_WINDOW_SECONDS
                    ])
                    await _setzen(breaker_key, {"fehler": fehler, "offen_bis": 0},
                                  settings.HYDRANT_BREAKER_WINDOW_SECONDS)
                    await _setzen(schluessel + ":negativ", {"anzahl": 0, "bis": 0}, 1)
                    eintrag = {"items": items, "stand": datetime.now(UTC)}
                    await _setzen(alt_key, eintrag, settings.HYDRANT_STALE_TTL_SECONDS)
                    await _setzen(schluessel, eintrag, settings.HYDRANT_CACHE_TTL_SECONDS)
                    return _als_ergebnis(eintrag, "frisch")
    except (TimeoutError, shared_cache.LockTimeout):
        # Keine weitere Redis-Wartezeit nach Ablauf des harten Gesamtbudgets.
        return _als_ergebnis(alt or _lokal_lesen(alt_key), "veraltet")


async def fetch_osm_hydranten(
    lat: float, lng: float, radius_m: int | None = None, max_results: int | None = None,
) -> list[dict]:
    """Kompatible Listen-Schnittstelle fuer bestehende Aufrufer."""
    return (await fetch_osm_hydranten_ergebnis(lat, lng, radius_m, max_results)).items


def parse_overpass_elements(
    elements: list[dict], lat: float, lng: float, max_results: int | None = None,
) -> list[dict]:
    """Overpass-Elemente → normalisierte Hydranten-Liste (sortiert, gedeckelt).

    Dedupliziert nach OSM-ID (Union-Queries können ein Element mehrfach liefern).
    """
    limit = int(max_results or settings.HYDRANT_MAX)
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
    return ergebnisse[:limit]


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
