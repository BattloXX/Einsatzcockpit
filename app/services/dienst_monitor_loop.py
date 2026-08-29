"""Hintergrund-Loop fuer die Dienstueberwachung aller Organisationen."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.dienst_monitor")


async def dienst_monitor_loop() -> None:
    from app.config import settings

    if not settings.DIENST_MONITOR_ENABLED:
        return
    while True:
        try:
            await asyncio.sleep(settings.DIENST_MONITOR_INTERVAL_S)
            with iteration_watch(logger, "dienst_monitor_loop", settings.DIENST_MONITOR_INTERVAL_S):
                await _run_all_orgs()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Dienstueberwachung: Durchlauf fehlgeschlagen")


async def _run_all_orgs() -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings, SystemSettings

    def laden() -> list[int]:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            return [o.org_id for o in db.query(OrgSettings).all() if o.dienst_monitor_enabled is not False]
        finally:
            db.close()

    for org_id in await asyncio.to_thread(laden):
        try:
            await _process_org(org_id)
        except Exception:
            logger.exception("Dienstueberwachung fuer Org %s fehlgeschlagen", org_id)

    def zeitstempel() -> None:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            row = db.get(SystemSettings, "dienst_monitor_last_run")
            wert = datetime.now(UTC).replace(tzinfo=None).isoformat()
            if row:
                row.value = wert
                row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            else:
                db.add(SystemSettings(key="dienst_monitor_last_run", value=wert))
            db.commit()
        finally:
            db.close()

    await asyncio.to_thread(zeitstempel)


async def _process_org(org_id: int) -> None:
    from app.config import settings
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.dienst_monitor import DienstStatus
    from app.models.master import FireDept, OrgSettings
    from app.services.dienst_monitor_dispatch import dispatch_meldung
    from app.services.dienst_monitor_service import (
        claim_meldung,
        entscheide,
        pruefe_dienste,
        rollback_claim,
    )

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        org = db.get(FireDept, org_id)
        if not org_settings or not org:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        for check in pruefe_dienste(db, org_id, now):
            row = (
                db.query(DienstStatus)
                .filter(DienstStatus.org_id == org_id, DienstStatus.key == check.key)
                .execution_options(include_all_tenants=True)
                .first()
            )
            if row is None:
                row = DienstStatus(org_id=org_id, key=check.key, state="unknown", since=now)
                db.add(row)
                db.flush()
            if check.relevant and row.state != check.state:
                row.state = check.state
                row.since = now
            if check.state == "ok":
                row.last_ok_at = now
                row.last_error = None
            elif check.state in ("down", "teilweise"):
                row.last_error = check.detail[:500]
            entscheidung = entscheide(
                check, row, org_settings.dienst_monitor_karenz_min, org_settings.dienst_monitor_wiederholung_min, now
            )
            db.commit()
            if not entscheidung.art:
                continue
            db.refresh(row)
            vorher_outage, vorher_repeat = row.outage_notified_at, row.last_repeat_at
            if not claim_meldung(db, row, org_id, entscheidung.art, now, org_settings.dienst_monitor_wiederholung_min):
                continue
            db.refresh(row)
            ok = await dispatch_meldung(
                db=db,
                org_id=org_id,
                org=org,
                org_settings=org_settings,
                row=row,
                check=check,
                art=entscheidung.art,
                base_url=settings.PUBLIC_BASE_URL or settings.APP_BASE_URL,
            )
            if not ok:
                rollback_claim(db, row.id, org_id, entscheidung.art, now, vorher_outage, vorher_repeat)
            elif entscheidung.art == "entwarnung":
                row.down_since = None
                row.last_repeat_at = None
                db.commit()
    finally:
        db.close()
