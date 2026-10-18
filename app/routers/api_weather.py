"""Externe API: Push-Ingest lokaler Wetterstationen (Davis/Meteobridge).

Die Meteobridge erreicht die Cloud nur ausgehend, daher PUSH-Modell: die Station
sendet periodisch (z. B. alle 5 min) ihre Messwerte an diesen Endpoint. Authentifi-
zierung über einen stations-spezifischen Token als Query-Parameter (HTTPS).

GET und POST werden akzeptiert (Meteobridge Custom-HTTP sendet i. d. R. GET mit
Query-Parametern). Antwort 204 (No Content) bei Erfolg/Throttle, 401 bei ungültigem
Token, 404 wenn das Feature global deaktiviert ist.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import limiter as _limiter
from app.db import get_db
from app.services import weather_station_service as wx

router = APIRouter(prefix="/api/v1", tags=["Wetterstation"])
logger = logging.getLogger("einsatzleiter.weather")

# Eingehender Query-Parameter → internes Mess-Feld (FIELDS in weather_station_service).
_PARAM_MAP: dict[str, str] = {
    "temp":     "temp_c",
    "hum":      "hum_pct",
    "wind":     "wind_ms",
    "gust":     "gust_ms",
    "dir":      "wind_dir_deg",
    "press":    "pressure_hpa",
    "rainrate": "rain_rate_mmh",
    "rainday":  "rain_day_mm",
    "dew":      "dewpoint_c",
    "solar":    "solar_wm2",
    "uv":       "uv",
}


def _safe_float(val: str | None) -> float | None:
    if val is None:
        return None
    s = val.strip()
    if not s or s.lower() in ("null", "nan", "--", "n/a"):
        return None
    try:
        f = float(s.replace(",", "."))
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _parse_ts(raw: str | None) -> datetime | None:
    """Parst den Messzeitpunkt: Unix-Epoch (Sekunden) oder ISO-8601. None ⇒ jetzt."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # Epoch-Sekunden
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s), tz=UTC)
        except (ValueError, OSError):
            pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _ingest_rate_key(request: Request) -> str:
    """Rate-Limit-Bucket je Token (Fallback: IP)."""
    from hashlib import sha256
    token = request.query_params.get("token", "").strip()
    if token:
        return f"wxstation:{sha256(token.encode()).hexdigest()[:24]}"
    return request.client.host if request.client else "unknown"


@router.api_route(
    "/weather/ingest",
    methods=["GET", "POST"],
    include_in_schema=True,
    summary="Messwerte einer lokalen Wetterstation entgegennehmen",
    description=(
        "Push-Endpoint für lokale Wetterstationen (z. B. Davis via Meteobridge). "
        "Authentifizierung über `token` (Query-Parameter). Messwerte als Query-Parameter: "
        "temp, hum, wind, gust, dir, press, rainrate, rainday, dew, solar, uv; optional `ts` "
        "(Epoch-Sekunden oder ISO-8601). Antwort 204 bei Erfolg."
    ),
    responses={
        204: {"description": "Messwerte übernommen (oder gedrosselt verworfen)."},
        401: {"description": "Ungültiger oder unbekannter Stations-Token."},
        404: {"description": "Wetterstations-Ingest global deaktiviert."},
        429: {"description": "Rate-Limit überschritten."},
    },
)
@(_limiter.limit("120/minute", key_func=_ingest_rate_key) if _limiter else lambda f: f)
def weather_ingest(
    request: Request,
    db: Session = Depends(get_db),
):
    if not (settings.WEATHER_ENABLED and settings.WEATHER_STATION_INGEST_ENABLED):
        raise HTTPException(status_code=404, detail="Wetterstations-Ingest deaktiviert")

    qp = request.query_params
    token = qp.get("token", "").strip()
    station = wx.get_station_by_token(db, token)
    if station is None:
        raise HTTPException(status_code=401, detail="Ungültiger Stations-Token")

    values = {field: _safe_float(qp.get(param)) for param, field in _PARAM_MAP.items()}
    measured_at = _parse_ts(qp.get("ts"))

    result = wx.ingest(db, station, values, measured_at)
    if result.throttled:
        logger.debug("Wetter-Ingest gedrosselt: station=%s org=%s", station.id, station.org_id)
    return Response(status_code=204)
