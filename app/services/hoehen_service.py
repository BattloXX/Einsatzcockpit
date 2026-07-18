"""Höhen-Service für den Förderstrecken-Planer (online, mit Cache).

Liefert Geländehöhen je Koordinate. Muster `hydrant_service`: server-seitiger Proxy
mit festem User-Agent, Timeout und `try/except`→Fallback (nie den Request crashen),
plus In-Memory-TTL-Cache und optionalem persistentem DB-Cache (HoehenCache).

Quellen:
- Primär (optional): Höhenservice Österreich (geoland.at), wenn HOEHEN_AT_URL gesetzt.
- Fallback/Standard: Open-Meteo Elevation API (frei, batch-fähig, ~90-m-Grobmodell).

Kein Offline-/DGM-Betrieb (bewusst nicht Teil dieses Moduls).
"""
from __future__ import annotations

import logging
import math
import time

import httpx

from app.config import settings
from app.models.hoehen_cache import hoehen_key

logger = logging.getLogger(__name__)

# In-Memory-Cache: key = (lat_key, lng_key) → (timestamp, hoehe_m, quelle)
_cache: dict[tuple[int, int], tuple[float, float, str]] = {}


# ── Geometrie-Helfer (rein, testbar) ─────────────────────────────────────────────

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resample_polyline(
    punkte: list[tuple[float, float]], segment_m: float = 25.0,
) -> list[dict]:
    """Zerlegt eine Polyline (lat/lng) in äquidistante Stützpunkte je `segment_m`.

    Rückgabe: Liste {s_m, lat, lng} inkl. Start- und Endpunkt. Für < 2 Punkte oder
    Länge 0 wird der Startpunkt zurückgegeben.
    """
    if not punkte:
        return []
    if len(punkte) == 1:
        return [{"s_m": 0.0, "lat": punkte[0][0], "lng": punkte[0][1]}]

    # Kumulierte Distanzen je Segment
    seg_laengen = [
        haversine_m(punkte[i][0], punkte[i][1], punkte[i + 1][0], punkte[i + 1][1])
        for i in range(len(punkte) - 1)
    ]
    gesamt = sum(seg_laengen)
    if gesamt <= 0:
        return [{"s_m": 0.0, "lat": punkte[0][0], "lng": punkte[0][1]}]

    step = max(segment_m, 1.0)
    n = int(math.floor(gesamt / step))
    ziel_s = [i * step for i in range(n + 1)]
    if ziel_s[-1] < gesamt:
        ziel_s.append(gesamt)

    stuetzpunkte: list[dict] = []
    seg_idx = 0
    seg_start_s = 0.0
    for s in ziel_s:
        # passendes Segment finden
        while seg_idx < len(seg_laengen) - 1 and s > seg_start_s + seg_laengen[seg_idx]:
            seg_start_s += seg_laengen[seg_idx]
            seg_idx += 1
        seg_len = seg_laengen[seg_idx] or 1e-9
        t = min(1.0, max(0.0, (s - seg_start_s) / seg_len))
        a, b = punkte[seg_idx], punkte[seg_idx + 1]
        lat = a[0] + t * (b[0] - a[0])
        lng = a[1] + t * (b[1] - a[1])
        stuetzpunkte.append({"s_m": round(s, 1), "lat": lat, "lng": lng})
    return stuetzpunkte


# ── HTTP-Quellen ──────────────────────────────────────────────────────────────────

async def _fetch_openmeteo(punkte: list[tuple[float, float]]) -> list[float | None]:
    """Höhen für einen Batch (≤ HOEHEN_BATCH_MAX) über Open-Meteo. [] → alle None."""
    if not punkte:
        return []
    lats = ",".join(f"{lat:.6f}" for lat, _ in punkte)
    lngs = ",".join(f"{lng:.6f}" for _, lng in punkte)
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.HOEHEN_USER_AGENT},
            timeout=settings.HOEHEN_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.get(
                settings.HOEHEN_OPENMETEO_URL,
                params={"latitude": lats, "longitude": lngs},
            )
            resp.raise_for_status()
            daten = resp.json()
        elev = daten.get("elevation") or []
        out: list[float | None] = []
        for i in range(len(punkte)):
            out.append(float(elev[i]) if i < len(elev) and elev[i] is not None else None)
        return out
    except Exception as exc:  # Netzfehler/Timeout → alle None, Aufrufer behandelt Lücken
        logger.warning("Open-Meteo-Höhenabfrage fehlgeschlagen: %s", exc)
        return [None] * len(punkte)


async def _fetch_at(punkte: list[tuple[float, float]]) -> list[float | None]:
    """Höhenservice Österreich (geoland.at), falls HOEHEN_AT_URL konfiguriert.

    Erwartet einen Punkt-/Batch-Abfragedienst, der eine Höhenliste liefert. Format
    variiert je Länderdienst; hier defensiv: bei Fehler/kein Treffer → None-Liste,
    sodass der Aufrufer auf Open-Meteo zurückfällt.
    """
    if not settings.HOEHEN_AT_URL or not punkte:
        return [None] * len(punkte)
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.HOEHEN_USER_AGENT},
            timeout=settings.HOEHEN_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.get(settings.HOEHEN_AT_URL, params={
                "lats": ",".join(f"{lat:.6f}" for lat, _ in punkte),
                "lngs": ",".join(f"{lng:.6f}" for _, lng in punkte),
            })
            resp.raise_for_status()
            daten = resp.json()
        werte = daten.get("hoehen") or daten.get("elevation") or []
        return [
            float(werte[i]) if i < len(werte) and werte[i] is not None else None
            for i in range(len(punkte))
        ]
    except Exception as exc:
        logger.warning("Höhenservice-Österreich-Abfrage fehlgeschlagen: %s", exc)
        return [None] * len(punkte)


def _batches(seq: list, groesse: int):
    for i in range(0, len(seq), groesse):
        yield seq[i:i + groesse]


# ── Öffentliche API ───────────────────────────────────────────────────────────────

async def hoehen_fuer_punkte(punkte: list[tuple[float, float]], db=None) -> dict:
    """Geländehöhen für eine Liste von Koordinaten.

    Reihenfolge: In-Memory-Cache → DB-Cache (falls `db`) → HTTP (AT primär, sonst
    Open-Meteo; AT-Lücken werden per Open-Meteo aufgefüllt). Ergebnisse werden in
    beide Caches geschrieben.

    Rückgabe: {"hoehen": [float|None …] (zur Eingabe ausgerichtet),
               "quelle": "cache"|"at"|"openmeteo"|"gemischt",
               "grob": bool (True wenn (auch) Open-Meteo genutzt)}.
    """
    n = len(punkte)
    hoehen: list[float | None] = [None] * n
    quellen: set[str] = set()
    now = time.time()

    offen: list[int] = []
    for i, (lat, lng) in enumerate(punkte):
        key = hoehen_key(lat, lng)
        c = _cache.get(key)
        if c and (now - c[0]) < settings.HOEHEN_CACHE_TTL_SECONDS:
            hoehen[i] = c[1]
            quellen.add("cache")
        else:
            offen.append(i)

    # DB-Cache
    if offen and db is not None:
        from app.models.hoehen_cache import HoehenCache
        keys = {hoehen_key(*punkte[i]) for i in offen}
        rows = (
            db.query(HoehenCache)
            .filter(HoehenCache.lat_key.in_({k[0] for k in keys}))
            .execution_options(include_all_tenants=True)
            .all()
        )
        by_key = {(r.lat_key, r.lng_key): r for r in rows}
        noch_offen: list[int] = []
        for i in offen:
            r = by_key.get(hoehen_key(*punkte[i]))
            if r is not None:
                hoehen[i] = r.hoehe_m
                _cache[hoehen_key(*punkte[i])] = (now, r.hoehe_m, r.quelle)
                quellen.add("cache")
            else:
                noch_offen.append(i)
        offen = noch_offen

    # HTTP (batch-weise)
    grob = False
    if offen:
        for batch_idx in _batches(offen, settings.HOEHEN_BATCH_MAX):
            batch_punkte = [punkte[i] for i in batch_idx]
            at = await _fetch_at(batch_punkte)
            # Lücken der AT-Quelle per Open-Meteo auffüllen
            fehlend = [j for j, v in enumerate(at) if v is None]
            om: list[float | None] = [None] * len(batch_punkte)
            if fehlend:
                om_res = await _fetch_openmeteo([batch_punkte[j] for j in fehlend])
                for k, j in enumerate(fehlend):
                    om[j] = om_res[k]
                grob = grob or any(v is not None for v in om)
            for j, i in enumerate(batch_idx):
                wert = at[j] if at[j] is not None else om[j]
                if wert is None:
                    continue
                quelle = "at" if at[j] is not None else "openmeteo"
                quellen.add(quelle)
                hoehen[i] = wert
                _cache[hoehen_key(*punkte[i])] = (now, wert, quelle)
                if db is not None:
                    _db_cache_schreiben(db, punkte[i], wert, quelle)
        if db is not None:
            db.commit()

    if len(quellen) > 1:
        quelle_gesamt = "gemischt"
    elif quellen:
        quelle_gesamt = next(iter(quellen))
    else:
        quelle_gesamt = "keine"
    return {"hoehen": hoehen, "quelle": quelle_gesamt, "grob": grob or quelle_gesamt == "openmeteo"}


def _db_cache_schreiben(db, punkt: tuple[float, float], hoehe: float, quelle: str) -> None:
    """Schreibt einen Höhenwert in den DB-Cache (idempotent je Koordinate)."""
    from app.models.hoehen_cache import HoehenCache
    lat_key, lng_key = hoehen_key(*punkt)
    vorhanden = (
        db.query(HoehenCache)
        .filter(HoehenCache.lat_key == lat_key, HoehenCache.lng_key == lng_key)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if vorhanden is None:
        db.add(HoehenCache(lat_key=lat_key, lng_key=lng_key, hoehe_m=hoehe, quelle=quelle))


async def hoehenprofil(
    polyline: list[tuple[float, float]], segment_m: float = 25.0, db=None,
) -> dict:
    """Höhenprofil entlang einer Route.

    Zerlegt die Polyline in `segment_m`-Stützpunkte und ergänzt je Punkt die
    Geländehöhe. Rückgabe: {"stuetzpunkte": [{s_m, lat, lng, hoehe_m} …],
    "quelle": …, "grob": bool}.
    """
    stuetzpunkte = resample_polyline(polyline, segment_m)
    if not stuetzpunkte:
        return {"stuetzpunkte": [], "quelle": "keine", "grob": False}
    koords = [(p["lat"], p["lng"]) for p in stuetzpunkte]
    res = await hoehen_fuer_punkte(koords, db=db)
    for p, h in zip(stuetzpunkte, res["hoehen"]):
        p["hoehe_m"] = h
    return {"stuetzpunkte": stuetzpunkte, "quelle": res["quelle"], "grob": res["grob"]}


def cache_leeren() -> None:
    """Leert den In-Memory-Cache (für Tests)."""
    _cache.clear()
