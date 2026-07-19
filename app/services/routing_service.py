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
import math

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _perp_dist_m(p: list, a: list, b: list) -> float:
    """Abstand des Punkts p vom Segment a→b in Metern (lokale, ebene Näherung)."""
    kx = math.cos(math.radians(a[0])) * 111320.0
    ky = 110574.0
    px, py = (p[1] - a[1]) * kx, (p[0] - a[0]) * ky
    bx, by = (b[1] - a[1]) * kx, (b[0] - a[0]) * ky
    seg2 = bx * bx + by * by
    if seg2 == 0.0:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg2))
    return math.hypot(px - t * bx, py - t * by)


def vereinfache_route(coords: list[list[float]], tol_m: float) -> list[list[float]]:
    """Douglas-Peucker-Vereinfachung einer [[lat,lng],…]-Linie (Endpunkte bleiben erhalten).

    Reduziert die dichte Straßen-Geometrie auf wenige markante Stützpunkte, damit sich die
    Förderleitung mit wenigen Griffen verschieben lässt. tol_m<=0 → unverändert.
    """
    if tol_m <= 0 or len(coords) < 3:
        return coords
    dmax, idx = 0.0, 0
    for i in range(1, len(coords) - 1):
        d = _perp_dist_m(coords[i], coords[0], coords[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol_m:
        links = vereinfache_route(coords[:idx + 1], tol_m)
        rechts = vereinfache_route(coords[idx:], tol_m)
        return links[:-1] + rechts
    return [coords[0], coords[-1]]


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
                # 'simplified' liefert bereits eine ausgedünnte Geometrie (Douglas-Peucker in OSRM)
                "overview": "simplified", "geometries": "geojson", "continue_straight": "true",
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
        # Zusätzlich serverseitig ausdünnen: wenige Stützpunkte → leicht verschiebbare Leitung
        coords = vereinfache_route(coords, settings.ROUTING_SIMPLIFY_TOLERANCE_M)
        return {"coords": coords, "laenge_m": float(routen[0].get("distance") or 0.0)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.info("Routing fehlgeschlagen (%s Punkte): %s", len(punkte), exc)
        return None
