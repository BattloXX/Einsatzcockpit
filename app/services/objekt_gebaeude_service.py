"""OSM-Gebaeudeecken als Referenzquelle, mit eigenem Overpass-Zeitbudget."""

from __future__ import annotations

import asyncio
import time

import httpx

from app.config import settings
from app.mcp.fehler import ExternerDienstFehler
from app.services import hydrant_service as overpass
from app.services import shared_cache
from app.services.objekt_karte_service import ObjektKartenFehler
from app.services.overpass_endpunkte import OverpassNichtVerfuegbar, abfragen


async def gebaeude_umrisse(lat: float, lng: float, radius_m: int = 150) -> dict:
    if type(radius_m) is not int or not 10 <= radius_m <= 500:
        raise ObjektKartenFehler("radius_m muss zwischen 10 und 500 liegen")
    key = f"objekt-gebaeude:{lat:.7f}:{lng:.7f}:{radius_m}"
    start = asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(settings.OBJEKT_GEBAEUDE_TOTAL_TIMEOUT_SECONDS):
            async with overpass._sperre(key):
                cached = await overpass._lesen(key)
                if cached is not None:
                    return cached | {"cache_status": "hit"}
                negativ = await overpass._lesen(key + ":negativ") or {}
                if negativ.get("bis", 0) > time.time():
                    raise ExternerDienstFehler(
                        code="OVERPASS_ALL_ENDPOINTS_FAILED", retryable=True,
                        meldung="Overpass derzeit nicht erreichbar (alle Endpunkte ausgelastet oder gesperrt)",
                        details={"attempted_endpoints_count": negativ.get("anzahl", 0),
                                 "endpunkte": negativ.get("endpunkte", []), "cache_status": "negativ_cache",
                                 "retry_after_s": max(0, int(negativ["bis"] - time.time()) + 1)},
                    )
                try:
                    # Zwei Sekunden Reserve fuer Cache und Aufbereitung innerhalb des Gesamtbudgets.
                    rest = settings.OBJEKT_GEBAEUDE_TOTAL_TIMEOUT_SECONDS - (
                        asyncio.get_running_loop().time() - start
                    ) - 2.0
                    daten = await abfragen(
                        '[out:json][timeout:25];way["building"]'
                        f"(around:{radius_m},{lat},{lng});out geom 100;",
                        primaer=settings.HYDRANT_OVERPASS_URL,
                        timeout_je_versuch=settings.OBJEKT_GEBAEUDE_TIMEOUT_SECONDS,
                        budget_s=rest, zweck="gebaeude",
                    )
                    if daten.get("remark") or not isinstance(daten.get("elements"), list):
                        raise ValueError("Unvollstaendige Overpass-Antwort")
                    items = []
                    for el in daten["elements"]:
                        ring = el.get("geometry", [])
                        if len(ring) < 4 or ring[0] != ring[-1]:
                            continue
                        ecken = [
                            {"nummer": i + 1, "lat": p["lat"], "lng": p["lon"]} for i, p in enumerate(ring[:-1])
                        ]
                        items.append({"osm_id": el["id"], "quelle": "osm_gebaeude", "ecken": ecken})
                except OverpassNichtVerfuegbar as exc:
                    await overpass._setzen(
                        key + ":negativ",
                        {"bis": time.time() + 60, "anzahl": exc.versuchte, "endpunkte": exc.versuche}, 60,
                    )
                    raise ExternerDienstFehler(
                        code=exc.code, retryable=True,
                        details={"attempted_endpoints_count": exc.versuchte, "endpunkte": exc.versuche,
                                 "cache_status": "miss"},
                        meldung="Overpass derzeit nicht erreichbar "
                        f"(alle Endpunkte ausgelastet oder gesperrt, {exc.versuchte} versucht); "
                        "bitte spaeter erneut versuchen"
                    ) from exc
                except httpx.HTTPStatusError as exc:
                    raise ExternerDienstFehler(
                        code="OVERPASS_HTTP_4XX", retryable=False,
                        meldung="Overpass-Gebaeudeabfrage fehlgeschlagen", details={"cache_status": "miss"},
                    ) from exc
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    raise ExternerDienstFehler(
                        code="OVERPASS_INVALID_RESPONSE", retryable=True,
                        details={"cache_status": "miss"},
                        meldung="Overpass-Gebaeudeabfrage fehlgeschlagen; keine Referenzdaten verfuegbar"
                    ) from exc
                ergebnis = {"gebaeude": items[:100], "quelle": "OpenStreetMap / Overpass"}
                await overpass._setzen(key, ergebnis, settings.HYDRANT_CACHE_TTL_SECONDS)
                return ergebnis | {"cache_status": "miss"}
    except (TimeoutError, shared_cache.LockTimeout) as exc:
        raise ExternerDienstFehler(
            code="OVERPASS_TIMEOUT", retryable=True, details={"cache_status": "miss"},
            meldung="Zeitlimit der Overpass-Gebaeudeabfrage erreicht; keine Referenzdaten verfuegbar"
        ) from exc
