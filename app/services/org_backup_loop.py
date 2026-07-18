"""Geplanter Push der Org-Backups an das je Org konfigurierte Remote-Ziel (PR 3).

Muster: weather_alert_loop.py — Orgs im System-Kontext laden, faellige verarbeiten,
Registrierung in app/main.py lifespan, Kill-Switch ORG_BACKUP_ENABLED.

Der eigentliche Lauf (Export + Upload) ist blockierend (Datei/Netz) und laeuft daher
in asyncio.to_thread. Ergebnis wird je Org in last_run_at/last_status/last_error
festgehalten (Statusanzeige im Admin-UI, Monitoring).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.org_backup_loop")


def ist_faellig(cfg: object, jetzt: datetime) -> bool:
    """True, wenn fuer diese Org-Config jetzt ein geplanter Push ansteht.

    jetzt ist naive UTC (DB-Konvention). Gefeuert wird ab der eingestellten Stunde,
    hoechstens einmal pro Kalendertag (last_run_at-Datum < heute); bei 'weekly'
    zusaetzlich nur am eingestellten Wochentag.
    """
    if not getattr(cfg, "enabled", False) or not getattr(cfg, "is_fully_configured", False):
        return False
    if jetzt.hour < getattr(cfg, "hour", 3):
        return False
    if getattr(cfg, "schedule", "daily") == "weekly":
        wd = getattr(cfg, "weekday", None)
        if wd is None or jetzt.weekday() != wd:
            return False
    last = getattr(cfg, "last_run_at", None)
    return last is None or last.date() < jetzt.date()


def _lade_faellige_ids() -> list[int]:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_backup import OrgBackupConfig
    db = SessionLocal()
    set_tenant_context(db, None)
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    try:
        rows = (db.query(OrgBackupConfig)
                .filter(OrgBackupConfig.enabled == True)  # noqa: E712
                .execution_options(include_all_tenants=True).all())
        return [c.id for c in rows if ist_faellig(c, jetzt)]
    finally:
        db.close()


def run_org_backup_sync(cfg_id: int) -> str:
    """Exportiert + laedt das Org-Backup einer Config hoch. Rueckgabe: 'ok'|'error'.

    Setzt last_run_at/last_status/last_error. Wirft nicht — Fehler werden am Datensatz
    vermerkt, damit ein Org-Fehler den Loop nicht abbricht.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_backup import OrgBackupConfig
    from app.services import remote_backup_service as rbs
    from app.services.org_export_service import export_org

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = (db.query(OrgBackupConfig)
               .filter(OrgBackupConfig.id == cfg_id)
               .execution_options(include_all_tenants=True).first())
        if cfg is None:
            return "error"
        from app.services.org_export_service import areas_aus_string
        tmp = Path(tempfile.mkdtemp(prefix="orgbackup_push_"))
        try:
            ziel = export_org(db, cfg.org_id, tmp, include_media=cfg.include_media,
                              areas=areas_aus_string(cfg.include_areas))
            with rbs.org_remote_config(cfg) as remote:
                rbs.upload(remote, [ziel], tmp)
                # Alte Push-Archive am Ziel aufraeumen (behaelt keep_count neueste).
                rbs.prune_remote(remote, f"org-backup-{cfg.org_id}-", cfg.keep_count)
            cfg.last_status = "ok"
            cfg.last_error = None
        except Exception as exc:  # noqa: BLE001 — Fehler am Datensatz vermerken
            cfg.last_status = "error"
            cfg.last_error = str(exc)[:1000]
            logger.exception("Org-Backup-Push fehlgeschlagen (org %s)", cfg.org_id)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            cfg.last_run_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()
        return cfg.last_status or "error"
    finally:
        db.close()


async def _run_faellige() -> None:
    faellig = await asyncio.to_thread(_lade_faellige_ids)
    for cfg_id in faellig:
        try:
            await asyncio.to_thread(run_org_backup_sync, cfg_id)
        except Exception:
            logger.exception("org_backup_loop: Config %s fehlgeschlagen", cfg_id)


async def org_backup_loop() -> None:
    """Prueft periodisch faellige Org-Backups und schiebt sie ans jeweilige Ziel."""
    from app.config import settings
    if not settings.ORG_BACKUP_ENABLED:
        logger.info("org_backup_loop: deaktiviert (ORG_BACKUP_ENABLED=False)")
        return
    logger.info("org_backup_loop gestartet (Intervall %ds)", settings.ORG_BACKUP_LOOP_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(settings.ORG_BACKUP_LOOP_INTERVAL_S)
            with iteration_watch(logger, "org_backup_loop", settings.ORG_BACKUP_LOOP_INTERVAL_S):
                await _run_faellige()
        except asyncio.CancelledError:
            logger.info("org_backup_loop beendet")
            break
        except Exception:
            logger.exception("org_backup_loop: Iteration fehlgeschlagen")
