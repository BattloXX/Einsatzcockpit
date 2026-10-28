"""OSM-Gebaeudeecken als Referenzquelle, mit gemeinsamem Overpass-Backoff."""

from __future__ import annotations

import asyncio
import hashlib
import time

import httpx

from app.config import settings
from app.services import hydrant_service as overpass
from app.services import shared_cache
from app.services.objekt_karte_service import ObjektKartenFehler


async def gebaeude_umrisse(lat: float, lng: float, radius_m: int = 150) -> dict:
    if type(radius_m) is not int or not 10 <= radius_m <= 500:
        raise ObjektKartenFehler("radius_m muss zwischen 10 und 500 liegen")
    key = f"objekt-gebaeude:{lat:.7f}:{lng:.7f}:{radius_m}"
    breaker = "hydranten-breaker:" + hashlib.sha256(settings.HYDRANT_OVERPASS_URL.encode()).hexdigest()
    start = time.monotonic()
    try:
        async with asyncio.timeout(settings.HYDRANT_TOTAL_TIMEOUT_SECONDS):
            async with overpass._sperre(key):
                cached = await overpass._lesen(key)
                if cached is not None:
                    return cached
                async with overpass._sperre(breaker):
                    zustand = await overpass._lesen(breaker, redis_zuerst=True) or {}
                    negativ = await overpass._lesen(key + ":negativ") or {}
                    if zustand.get("offen_bis", 0) > time.time() or negativ.get("bis", 0) > time.time():
                        raise ObjektKartenFehler("Overpass derzeit nicht erreichbar; bitte spaeter erneut versuchen")
                    try:
                        async with httpx.AsyncClient(
                            headers={"User-Agent": settings.HYDRANT_USER_AGENT},
                            timeout=settings.HYDRANT_TIMEOUT_SECONDS,
                        ) as client:
                            antwort = await client.post(
                                settings.HYDRANT_OVERPASS_URL,
                                data={
                                    "data": (
                                        '[out:json][timeout:25];way["building"]'
                                        f"(around:{radius_m},{lat},{lng});out geom 100;"
                                    )
                                },
                            )
                            antwort.raise_for_status()
                            daten = antwort.json()
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
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        await overpass._fehler(key, breaker, zustand, exc, start)
                        raise ObjektKartenFehler(
                            "Overpass-Gebaeudeabfrage fehlgeschlagen; keine Referenzdaten verfuegbar"
                        ) from exc
                    await overpass._setzen(
                        breaker, {"fehler": [], "offen_bis": 0}, settings.HYDRANT_BREAKER_WINDOW_SECONDS
                    )
                    await overpass._setzen(key + ":negativ", {"anzahl": 0, "bis": 0}, 1)
                    ergebnis = {"gebaeude": items[:100], "quelle": "OpenStreetMap / Overpass"}
                    await overpass._setzen(key, ergebnis, settings.HYDRANT_CACHE_TTL_SECONDS)
                    return ergebnis
    except (TimeoutError, shared_cache.LockTimeout) as exc:
        raise ObjektKartenFehler(
            "Zeitlimit der Overpass-Gebaeudeabfrage erreicht; keine Referenzdaten verfuegbar"
        ) from exc
