"""Kleiner In-Memory-Lieferstatus der LIS-Poll-Schleife.

Der Status ist absichtlich pro Prozess: auch die Poll-Loops selbst teilen keinen
Zustand zwischen Prozessen. Nach einem Neustart verhindert die kurze Schonfrist,
dass DIBOS und LIS sofort gleichzeitig dieselben Fahrzeugdaten schreiben.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.lis import OrgLisConfig

_started_at = datetime.now(UTC)
_last_ok: dict[int, datetime] = {}


def mark_lis_ok(org_id: int) -> None:
    _last_ok[org_id] = datetime.now(UTC)


def mark_lis_failed(org_id: int) -> None:
    # Bewusst kein Zurücksetzen von _last_ok: ein einzelner Fehlzyklus soll den
    # Fallback nicht sofort umschalten — erst wenn der letzte Erfolg älter als die
    # Frist in lis_delivers_for() ist. Die Funktion markiert die Stelle im LIS-Sync
    # und ist der Ansatzpunkt für eine spätere Dienstüberwachung.
    return None


def lis_delivers_for(db, org_id: int, incident) -> bool:
    """True, wenn LIS für diesen Einsatz autoritativ liefert: Org hat LIS aktiv und
    vollständig konfiguriert, der letzte erfolgreiche ActiveParticipation-Abruf ist
    höchstens max(3 × Poll-Intervall, 120 s) her (bzw. Schonfrist nach Neustart),
    und LIS hat den Einsatz verknüpft (lis_operation_id). Sonst übernimmt DIBOS
    Fahrzeugstatus/-positionen als Fallback (siehe dibos_enrich.py)."""
    config = db.query(OrgLisConfig).filter(OrgLisConfig.org_id == org_id).first()
    if not config or not config.enabled or not config.is_fully_configured or not incident.lis_operation_id:
        return False
    now = datetime.now(UTC)
    last_ok = _last_ok.get(org_id)
    if last_ok is None:
        return now - _started_at <= timedelta(seconds=120)
    max_age = max(3 * config.poll_interval_seconds, 120)
    return now - last_ok <= timedelta(seconds=max_age)
