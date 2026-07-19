"""Straßen-Routing für den Förderstrecken-Planer.

Liefert für Start-/End-Punkt (plus optionale Zwischenpunkte) einen straßenfolgenden
Streckenverlauf. Muster `hoehen_service`: server-seitiger Proxy mit festem User-Agent,
Timeout und `try/except`→Fallback (nie den Request crashen).

Quelle: OSRM (`settings.ROUTING_OSRM_URL`, öffentlicher Demo-Server als Default; für den
Produktivbetrieb eine eigene OSRM-Instanz konfigurieren). Ist die URL leer oder der Dienst
nicht erreichbar, gibt der Service None zurück — der Aufrufer fällt dann auf die Luftlinie
bzw. das manuelle Zeichnen zurück (die gezeichnete Linie bleibt jederzeit maßgeblich).
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def strassen_route(
    punkte: list[tuple[float, float]],
) -> dict | None:
    """Straßenroute entlang der Wegpunkte (jeweils (lat, lng)).

    Rückgabe: {"coords": [[lat, lng], …], "laenge_m": float} oder None bei Fehler/
    deaktiviertem Routing. `punkte` braucht mindestens Start und Ende (2 Punkte);
    weitere Punkte werden als Zwischenziele (Vias) in Reihenfolge angefahren.
    """
    if not settings.ROUTING_OSRM_URL or len(punkte) < 2:
        return None
    profile = settings.ROUTING_PROFILE or "driving"
    # OSRM erwartet lng,lat; Wegpunkte mit ';' getrennt
    koords = ";".join(f"{lng},{lat}" for lat, lng in punkte)
    url = f"{settings.ROUTING_OSRM_URL.rstrip('/')}/route/v1/{profile}/{koords}"
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.ROUTING_USER_AGENT},
            timeout=settings.ROUTING_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.get(url, params={
                "overview": "full", "geometries": "geojson", "continue_straight": "true",
            })
        if resp.status_code != 200:
            logger.info("Routing OSRM Status %s für %s Punkte", resp.status_code, len(punkte))
            return None
        daten = resp.json()
        routen = daten.get("routes") or []
        if not routen:
            return None
        geom = (routen[0].get("geometry") or {}).get("coordinates") or []
        coords = [[float(c[1]), float(c[0])] for c in geom if len(c) >= 2]  # [lng,lat] → [lat,lng]
        if len(coords) < 2:
            return None
        return {"coords": coords, "laenge_m": float(routen[0].get("distance") or 0.0)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.info("Routing fehlgeschlagen (%s Punkte): %s", len(punkte), exc)
        return None
