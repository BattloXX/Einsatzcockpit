"""48h-Auto-Close-Lifecycle für Einsätze.

Loop läuft alle 60s und prüft alle aktiven Einsätze:
- Phase 1: Wenn `started_at` älter als `incident_autoclose_after_hours` Stunden
  und noch keine Warnung versendet → WebSocket-Broadcast "Offen halten?"
  + `autoclose_warn_sent_at` setzen.
- Phase 2: Wenn Warnung versendet und älter als `incident_autoclose_grace_minutes`
  Minuten → Einsatz auto-schließen.

Per `POST /einsatz/{id}/autoclose/keepopen` (Frontend-Banner) wird
`autoclose_warn_sent_at` zurückgesetzt und `started_at` aktualisiert →
nächster 48h-Zyklus beginnt.

Konfiguration über `SystemSettings`:
- `incident_autoclose_enabled` (true/false, Default: true)
- `incident_autoclose_after_hours` (Default: 48)
- `incident_autoclose_grace_minutes` (Default: 60)
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import SystemSettings
from app.services.broadcast import manager
from app.services.incident_service import close_incident

logger = logging.getLogger("einsatzleiter.autoclose")


def _load_cfg(db) -> dict:
    def _get(key: str, default: str) -> str:
        row = db.query(SystemSettings).filter_by(key=key).first()
        return row.value if row and row.value else default

    try:
        return {
            "enabled": _get("incident_autoclose_enabled", "true").lower() == "true",
            "after_hours": int(_get("incident_autoclose_after_hours", "48")),
            "grace_minutes": int(_get("incident_autoclose_grace_minutes", "60")),
        }
    except (ValueError, TypeError):
        # Fallback bei fehlerhaften Settings-Werten
        return {"enabled": True, "after_hours": 48, "grace_minutes": 60}


async def _send_warning(incident_id: int, grace_minutes: int) -> None:
    """WebSocket-Broadcast an alle Clients dieses Einsatzes."""
    try:
        await manager.broadcast(incident_id, {
            "type": "autoclose_warning",
            "grace_minutes": grace_minutes,
        })
    except Exception:
        logger.exception("autoclose: WebSocket-Broadcast für Einsatz %s fehlgeschlagen", incident_id)


def _check_incidents_sync(db) -> list[tuple[int, int]]:
    """Synchrone Prüfung — gibt Liste der zu broadcastenden Warnungen zurück.

    Returns: Liste von (incident_id, grace_minutes) für die ein WS-Broadcast
    ausgelöst werden soll. Phase-2-Closes werden inline ausgeführt.
    """
    cfg = _load_cfg(db)
    if not cfg["enabled"]:
        return []
    now = datetime.now(UTC)
    warn_threshold = now - timedelta(hours=cfg["after_hours"])
    grace_threshold = now - timedelta(minutes=cfg["grace_minutes"])
    to_warn: list[tuple[int, int]] = []

    active = db.query(Incident).filter(Incident.status == "active").all()
    for inc in active:
        opened = inc.started_at or inc.created_at
        # DB-Datetimes sind tz-naiv (gespeichert als UTC) — Threshold gleichen
        warn_th_naive = warn_threshold.replace(tzinfo=None)
        grace_th_naive = grace_threshold.replace(tzinfo=None)
        opened_naive = opened.replace(tzinfo=None) if opened.tzinfo else opened

        if inc.autoclose_warn_sent_at is None and opened_naive <= warn_th_naive:
            # Phase 1: Warnung versenden
            inc.autoclose_warn_sent_at = now.replace(tzinfo=None)
            db.commit()
            to_warn.append((inc.id, cfg["grace_minutes"]))
        elif (inc.autoclose_warn_sent_at is not None
              and inc.autoclose_warn_sent_at.replace(tzinfo=None) <= grace_th_naive):
            # Phase 2: Auto-Close
            try:
                close_incident(db, inc, user_id=None)
                db.commit()
                logger.info("autoclose: Einsatz %s automatisch geschlossen (48h ohne Bestätigung)", inc.id)
            except Exception:
                db.rollback()
                logger.exception("autoclose: Fehler beim Schließen von Einsatz %s", inc.id)
    return to_warn


async def autoclose_loop() -> None:
    """Background-Task: alle 60s aktive Einsätze prüfen."""
    logger.info("autoclose_loop gestartet")
    while True:
        try:
            await asyncio.sleep(60)
            db = SessionLocal()
            try:
                to_warn = _check_incidents_sync(db)
            finally:
                db.close()
            for incident_id, grace_minutes in to_warn:
                await _send_warning(incident_id, grace_minutes)
        except asyncio.CancelledError:
            logger.info("autoclose_loop beendet")
            break
        except Exception:
            logger.exception("autoclose_loop: Iteration fehlgeschlagen")
