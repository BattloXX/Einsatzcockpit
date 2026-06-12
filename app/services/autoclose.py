"""48h-Auto-Close-Lifecycle für Einsätze – pro Organisation konfigurierbar.

Loop läuft alle 60s und prüft alle aktiven Einsätze:
- Phase 1: Wenn `started_at` älter als `autoclose_after_hours` Stunden
  und noch keine Warnung versendet → WebSocket-Broadcast "Offen halten?"
  + `autoclose_warn_sent_at` setzen.
- Phase 2: Wenn Warnung versendet und älter als `autoclose_grace_minutes`
  Minuten → Einsatz auto-schließen.

Konfigurationsreihenfolge (erste nicht-NULL-Quelle gewinnt):
  1. OrgSettings.autoclose_* (je Org)
  2. SystemSettings `incident_autoclose_*` (globaler Fallback)
  3. Hardcoded Defaults (enabled=True, after_hours=48, grace_minutes=60)
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.services.broadcast import manager
from app.services.incident_service import close_incident

logger = logging.getLogger("einsatzleiter.autoclose")


def _global_cfg(db) -> dict:
    """Liest globale SystemSettings-Fallback-Werte."""
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
        return {"enabled": True, "after_hours": 48, "grace_minutes": 60}


def _org_cfg(org_settings: OrgSettings | None, global_cfg: dict) -> dict:
    """Baut org-spezifische Konfig: NULL-Felder fallen auf globale Defaults zurück."""
    if org_settings is None:
        return global_cfg
    return {
        "enabled": (
            org_settings.autoclose_enabled
            if org_settings.autoclose_enabled is not None
            else global_cfg["enabled"]
        ),
        "after_hours": (
            org_settings.autoclose_after_hours
            if org_settings.autoclose_after_hours is not None
            else global_cfg["after_hours"]
        ),
        "grace_minutes": (
            org_settings.autoclose_grace_minutes
            if org_settings.autoclose_grace_minutes is not None
            else global_cfg["grace_minutes"]
        ),
    }


async def _send_warning(incident_id: int, grace_minutes: int) -> None:
    try:
        await manager.broadcast(incident_id, {
            "type": "autoclose_warning",
            "grace_minutes": grace_minutes,
        })
    except Exception:
        logger.exception("autoclose: Broadcast für Einsatz %s fehlgeschlagen", incident_id)


def _check_incidents_sync(db) -> list[tuple[int, int]]:
    """Prüft alle aktiven Einsätze, iteriert je Org mit org-spezifischer Konfig.

    Returns: Liste von (incident_id, grace_minutes) für WS-Broadcasts.
    """
    global_cfg = _global_cfg(db)
    now = datetime.now(UTC)
    to_warn: list[tuple[int, int]] = []

    active = db.query(Incident).filter(Incident.status == "active").all()

    # Org-Settings gecacht damit wir nicht N Queries pro Einsatz machen
    org_settings_cache: dict[int | None, dict] = {}

    for inc in active:
        org_id = inc.primary_org_id

        if org_id not in org_settings_cache:
            org_s = (
                db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
                if org_id else None
            )
            org_settings_cache[org_id] = _org_cfg(org_s, global_cfg)

        cfg = org_settings_cache[org_id]
        if not cfg["enabled"]:
            continue

        warn_threshold = now - timedelta(hours=cfg["after_hours"])
        grace_threshold = now - timedelta(minutes=cfg["grace_minutes"])

        opened = inc.started_at or inc.created_at
        warn_th_naive = warn_threshold.replace(tzinfo=None)
        grace_th_naive = grace_threshold.replace(tzinfo=None)
        opened_naive = opened.replace(tzinfo=None) if opened.tzinfo else opened

        if inc.autoclose_warn_sent_at is None and opened_naive <= warn_th_naive:
            inc.autoclose_warn_sent_at = now.replace(tzinfo=None)
            db.commit()
            to_warn.append((inc.id, cfg["grace_minutes"]))
        elif (inc.autoclose_warn_sent_at is not None
              and inc.autoclose_warn_sent_at.replace(tzinfo=None) <= grace_th_naive):
            try:
                close_incident(db, inc, user_id=None)
                db.commit()
                logger.info(
                    "autoclose: Einsatz %s automatisch geschlossen (org=%s, %sh + %smin)",
                    inc.id, org_id, cfg["after_hours"], cfg["grace_minutes"],
                )
            except Exception:
                db.rollback()
                logger.exception("autoclose: Fehler beim Schließen von Einsatz %s", inc.id)

    return to_warn


async def autoclose_loop() -> None:
    logger.info("autoclose_loop gestartet")
    while True:
        try:
            await asyncio.sleep(60)
            db = SessionLocal()
            set_tenant_context(db, None)
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
