"""Standortmeldung der Android-Geraete: Validierung, Zustand und Veraltungspruefung.

Massgeblich ist die Messzeit (`last_location_at`); der Empfangszeitpunkt allein macht
eine alte GPS-Position nie zu einer frischen Messung.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

VERALTET_NACH = timedelta(hours=24)
FRISCH_BIS = timedelta(minutes=15)
ZUKUNFT_TOLERANZ = timedelta(minutes=5)
STATUS = {
    "ok", "permission_denied", "background_permission_missing", "location_disabled",
    "no_fix", "stale_fix", "mock_location", "error",
}
QUELLEN = {"live", "periodic", "incident", "manual"}
STATUS_TEXT = {
    "ok": "Position uebermittelt",
    "permission_denied": "Standortberechtigung fehlt",
    "background_permission_missing": "Hintergrund-Standort nicht erlaubt",
    "location_disabled": "Ortung am Geraet ausgeschaltet",
    "no_fix": "Keine Position bestimmbar",
    "stale_fix": "Nur veraltete Position verfuegbar",
    "mock_location": "Simulierte Position verworfen",
    "error": "Fehler bei der Standortmeldung",
}


def _naiv_utc(wert: datetime) -> datetime:
    return wert.astimezone(UTC).replace(tzinfo=None) if wert.tzinfo else wert


def parse_zeit(wert: Any) -> datetime | None:
    if not isinstance(wert, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+(Z|[+-]\d\d:?\d\d)?", wert):
        return None
    try:
        return _naiv_utc(datetime.fromisoformat(wert.replace("Z", "+00:00")))
    except ValueError:
        return None


def _bereinigt(text: Any, laenge: int) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)
    return re.sub(r"[\r\n\t]+", " ", text)[:laenge]


def verarbeite(token: Any, daten: dict, jetzt: datetime) -> dict:
    """Uebernimmt eine Meldung in den DeviceToken; liefert {"position": bool, "frisch": bool}.

    Altclients senden nur lat/lng: Messzeit = Empfangszeit, Status ok, Quelle live.
    """
    status = daten.get("status") if daten.get("status") in STATUS else None
    quelle = daten.get("source") if daten.get("source") in QUELLEN else "live"
    token.last_location_attempt_at = jetzt
    token.last_location_source = quelle
    hat_koordinaten = daten.get("lat") is not None or daten.get("lng") is not None
    if not hat_koordinaten:
        token.last_location_status = status or "error"
        token.last_location_error = _bereinigt(daten.get("error"), 200)
        return {"position": False, "frisch": False}
    lat, lng = float(daten["lat"]), float(daten["lng"])
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("lat/lng ausserhalb des gueltigen Bereichs")
    gemessen = parse_zeit(daten.get("measured_at")) if daten.get("measured_at") else jetzt
    if gemessen is None:
        raise ValueError("measured_at ungueltig")
    if gemessen > jetzt + ZUKUNFT_TOLERANZ:
        gemessen = jetzt  # Geraeteuhr geht vor; nie eine Messung aus der Zukunft speichern
    gemessen = min(gemessen, jetzt)
    if jetzt - gemessen > VERALTET_NACH:
        token.last_location_status = "stale_fix"
        token.last_location_error = None
        return {"position": False, "frisch": False}
    token.last_location_status = "ok"
    token.last_location_error = None
    token.last_location_received_at = jetzt
    if token.last_location_at is None or gemessen >= token.last_location_at:
        token.last_lat, token.last_lng = lat, lng
        token.last_location_at = gemessen
        accuracy = daten.get("accuracy")
        genau = float(accuracy) if isinstance(accuracy, (int, float)) and accuracy > 0 else None
        token.last_location_accuracy_m = genau
        return {"position": True, "frisch": jetzt - gemessen <= FRISCH_BIS}
    return {"position": False, "frisch": False}


def zustand(token: Any, jetzt: datetime) -> dict:
    """Anzeige-/Auswertungszustand: Alter der Messung, veraltet, letzter Status."""
    alter = (jetzt - token.last_location_at).total_seconds() if token.last_location_at else None
    veraltet = alter is None or alter > VERALTET_NACH.total_seconds()
    status = token.last_location_status
    return {
        "letzte_messung_at": token.last_location_at,
        "letzter_empfang_at": token.last_location_received_at,
        "letzter_versuch_at": token.last_location_attempt_at,
        "alter_s": alter, "veraltet": veraltet,
        "status": status, "status_text": STATUS_TEXT.get(status or "", "Noch keine Standortmeldung"),
        "fehler": token.last_location_error, "quelle": token.last_location_source,
        "genauigkeit_m": token.last_location_accuracy_m,
    }
