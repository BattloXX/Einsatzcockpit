"""Service für lokale Wetterstationen (Davis/Meteobridge).

Verantwortlich für:
- Token-Lookup (Push-Authentifizierung) inkl. Tenant-Bypass (Org wird erst durch
  den Token bestimmt – die Abfrage berührt das tenant-scoped ``weather_station``,
  daher ``include_all_tenants=True``).
- Plausibilitätsprüfung der gemeldeten Messwerte.
- Aufnahme eines Pushes: denormalisierten Ist-Stand in der Haupt-DB aktualisieren
  und (falls Wetter-DB konfiguriert) eine Zeitreihen-Zeile schreiben.

Org-Isolation: Der Token ist genau einer Station (und damit einer Org) zugeordnet;
es werden nur deren Felder beschrieben. Die Zeitreihe wird mit der ``org_id`` der
Station geschrieben.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_api_key
from app.db_weather import get_weather_session, weather_db_enabled
from app.models.weather import WeatherReading, WeatherStation

logger = logging.getLogger("einsatzleiter.weather")

# In-Memory-Ringpuffer: bis zu 48 h Verlauf pro Station (Fallback ohne Wetter-DB).
# station_id → deque von SimpleNamespace-Objekten (ts + alle FIELDS)
_HISTORY_MAX = 2880  # 48 h bei 1 Push/min
_station_history: dict[int, deque] = {}


# ── Plausibilitätsgrenzen (Ausreißer werden verworfen, nicht der ganze Push) ───
# (min, max) je Messgröße; Werte außerhalb ⇒ None (Feld wird ignoriert).
_LIMITS: dict[str, tuple[float, float]] = {
    "temp_c":        (-60.0, 60.0),
    "hum_pct":       (0.0, 100.0),
    "wind_ms":       (0.0, 120.0),
    "gust_ms":       (0.0, 150.0),
    "wind_dir_deg":  (0.0, 360.0),
    "pressure_hpa":  (850.0, 1100.0),
    "rain_rate_mmh": (0.0, 500.0),
    "rain_day_mm":   (0.0, 2000.0),
    "dewpoint_c":    (-60.0, 60.0),
    "solar_wm2":     (0.0, 1500.0),
    "uv":            (0.0, 20.0),
}

# Reihenfolge der Mess-Felder (Snapshot-Spalten heißen last_<feld>).
FIELDS: tuple[str, ...] = tuple(_LIMITS.keys())


@dataclass
class IngestResult:
    accepted: bool
    throttled: bool = False
    stored_history: bool = False
    reason: str = ""


def get_station_by_token(db: Session, token: str) -> WeatherStation | None:
    """Findet die aktive Station zum Push-Token (Tenant-Bypass, fail-closed bei Miss)."""
    if not token:
        return None
    token_hash = hash_api_key(token)
    station = (
        db.query(WeatherStation)
        .execution_options(include_all_tenants=True)
        .filter(WeatherStation.ingest_token_hash == token_hash)
        .first()
    )
    if station is None or not station.active:
        return None
    return station


def clamp(field: str, value: float | None) -> float | None:
    """Gibt den Wert zurück, wenn er im Plausibilitätsbereich liegt, sonst None."""
    if value is None:
        return None
    lo, hi = _LIMITS.get(field, (float("-inf"), float("inf")))
    if value < lo or value > hi:
        logger.debug("Wetter-Ingest: Wert ausserhalb Grenzen verworfen %s=%s", field, value)
        return None
    return value


def _is_throttled(station: WeatherStation, now: datetime) -> bool:
    """True, wenn seit dem letzten akzeptierten Push das Mindestintervall nicht erreicht ist."""
    if station.last_seen_at is None:
        return False
    last = station.last_seen_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds() < settings.WEATHER_INGEST_MIN_INTERVAL_S


def ingest(
    db: Session,
    station: WeatherStation,
    values: dict[str, float | None],
    measured_at: datetime | None,
) -> IngestResult:
    """Nimmt einen Push entgegen: Snapshot (Haupt-DB) + Zeitreihe (Wetter-DB).

    ``db`` ist die Haupt-DB-Session des Requests. ``values`` enthält die rohen
    Messwerte (Schlüssel = FIELDS), die hier auf Plausibilität geprüft werden.
    """
    now = datetime.now(UTC)
    if _is_throttled(station, now):
        return IngestResult(accepted=False, throttled=True, reason="zu häufig")

    clean = {f: clamp(f, values.get(f)) for f in FIELDS}
    measured = measured_at or now

    # 1. Snapshot in der Haupt-DB (einsatzkritischer, schneller Pfad)
    for f in FIELDS:
        setattr(station, f"last_{f}", clean[f])
    station.last_measured_at = measured
    station.last_seen_at = now
    db.commit()

    # 2. Zeitreihe in der separaten Wetter-DB (best effort – Anzeige bleibt aktuell)
    stored = False
    if weather_db_enabled():
        wdb = None
        try:
            wdb = get_weather_session()
            wdb.add(WeatherReading(
                org_id=station.org_id,
                station_id=station.id,
                ts=measured,
                **clean,
            ))
            wdb.commit()
            stored = True
        except Exception as exc:
            logger.warning("Wetter-Zeitreihe konnte nicht gespeichert werden: %s", exc)
            if wdb is not None:
                wdb.rollback()
        finally:
            if wdb is not None:
                wdb.close()

    # 3. In-Memory-Ringpuffer (immer, auch ohne Wetter-DB)
    if station.id not in _station_history:
        _station_history[station.id] = deque(maxlen=_HISTORY_MAX)
    _station_history[station.id].append(SimpleNamespace(ts=measured, **clean))

    return IngestResult(accepted=True, stored_history=stored)


def get_station_history(station_id: int, cutoff: datetime) -> list:
    """Gibt In-Memory-Verlauf einer Station ab ``cutoff`` zurück (SimpleNamespace-Objekte)."""
    buf = _station_history.get(station_id)
    if not buf:
        return []
    return [r for r in buf if r.ts >= cutoff]
