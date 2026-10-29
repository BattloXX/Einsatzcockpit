"""Lokale OSM-Gebaeudeecken mit Overpass-Rueckfall ausserhalb der importierten Huellen."""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.mcp.fehler import ExternerDienstFehler
from app.models.osm import OsmGebaeude, OsmImportLauf
from app.services import hydrant_service as overpass
from app.services import shared_cache
from app.services.objekt_karte_service import ObjektKartenFehler
from app.services.overpass_endpunkte import OverpassNichtVerfuegbar, abfragen


def _lokale_umrisse(lat: float, lng: float, radius_m: int) -> dict | None:
    with SessionLocal() as db:
        laeufe = list(db.scalars(select(OsmImportLauf).where(
            OsmImportLauf.status == "aktiv", OsmImportLauf.min_lat <= lat, OsmImportLauf.max_lat >= lat,
            OsmImportLauf.min_lng <= lng, OsmImportLauf.max_lng >= lng,
        ).order_by(OsmImportLauf.datenstand.desc(), OsmImportLauf.id.desc())))
        if not laeufe:
            return None
        faktor_lat = 111_320.0
        faktor_lng = faktor_lat * max(0.000001, abs(math.cos(math.radians(lat))))
        dlat, dlng = radius_m / faktor_lat, radius_m / faktor_lng
        zeilen = db.scalars(select(OsmGebaeude).where(
            OsmGebaeude.lauf_id.in_([lauf.id for lauf in laeufe]),
            OsmGebaeude.min_lat <= lat + dlat, OsmGebaeude.max_lat >= lat - dlat,
            OsmGebaeude.min_lng <= lng + dlng, OsmGebaeude.max_lng >= lng - dlng,
        ))
        treffer = []
        gesehen = set()
        for g in zeilen:
            abstand = math.hypot(((g.min_lat + g.max_lat) / 2 - lat) * faktor_lat,
                                 ((g.min_lng + g.max_lng) / 2 - lng) * faktor_lng)
            halb = math.hypot((g.max_lat - g.min_lat) * faktor_lat, (g.max_lng - g.min_lng) * faktor_lng) / 2
            if abstand <= radius_m + halb:
                treffer.append((abstand, g))
        items = []
        verwendet = set()
        for _, g in sorted(treffer, key=lambda t: (t[0], t[1].osm_way_id)):
            if g.osm_way_id in gesehen:
                continue
            gesehen.add(g.osm_way_id)
            verwendet.add(g.lauf_id)
            items.append({"osm_id": g.osm_way_id, "quelle": "osm_gebaeude", "ecken": [
                {"nummer": i + 1, "lat": p[0], "lng": p[1]} for i, p in enumerate(json.loads(g.ecken_json))
            ]})
            if len(items) == 100:
                break
        # Bei mehreren Quellen gilt fuer die Sicherheit der aelteste verwendete Stand.
        relevante = [lauf for lauf in laeufe if not verwendet or lauf.id in verwendet]
        datum = min((lauf.datenstand for lauf in relevante if lauf.datenstand), default=None)
        unbekannt = any(lauf.datenstand is None for lauf in relevante)
        alter = max(0, (datetime.now(UTC).replace(tzinfo=None) - datum).days) if datum else None
        veraltet = unbekannt or (alter is not None and alter > settings.OSM_GEBAEUDE_MAX_ALTER_TAGE)
        ergebnis: dict = {"gebaeude": items, "quelle": "OpenStreetMap (lokaler Bestand "
                    + ", ".join(lauf.quelle for lauf in relevante) + ")", "cache_status": "lokal",
                    "datenstand": datum.isoformat() + "Z" if datum and not unbekannt else None,
                    "alter_tage": alter if not unbekannt else None, "veraltet": veraltet}
        if veraltet:
            ergebnis["warnung"] = (
                "OSM-Gebaeudedaten sind veraltet oder ihr Datenstand ist unbekannt; Import aktualisieren"
            )
        return ergebnis


async def gebaeude_umrisse(lat: float, lng: float, radius_m: int = 150) -> dict:
    if type(radius_m) is not int or not 10 <= radius_m <= 500:
        raise ObjektKartenFehler("radius_m muss zwischen 10 und 500 liegen")
    hinweis = {}
    if settings.OSM_GEBAEUDE_IMPORT_ENABLED:
        lokal = await asyncio.to_thread(_lokale_umrisse, lat, lng, radius_m)
        if lokal is not None:
            return lokal
        hinweis = {"quelle_hinweis": "kein lokaler Bestand fuer diesen Ort"}
    key = f"objekt-gebaeude:{lat:.7f}:{lng:.7f}:{radius_m}"
    start = asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(settings.OBJEKT_GEBAEUDE_TOTAL_TIMEOUT_SECONDS):
            async with overpass._sperre(key):
                cached = await overpass._lesen(key)
                if cached is not None:
                    return cached | {"cache_status": "hit"} | hinweis
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
                return ergebnis | {"cache_status": "miss"} | hinweis
    except (TimeoutError, shared_cache.LockTimeout) as exc:
        raise ExternerDienstFehler(
            code="OVERPASS_TIMEOUT", retryable=True, details={"cache_status": "miss"},
            meldung="Zeitlimit der Overpass-Gebaeudeabfrage erreicht; keine Referenzdaten verfuegbar"
        ) from exc
