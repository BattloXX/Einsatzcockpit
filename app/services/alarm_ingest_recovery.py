"""Wiederaufnahme liegengebliebener serieller Alarm-Ingests."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import and_, func, or_

from app.config import settings
from app.core.async_bridge import run_async
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmIngest
from app.models.master import FireDept
from app.services import alarm_apply_service
from app.services.loop_utils import iteration_watch
from app.services.serial_alarm_service import process_ingest

logger = logging.getLogger("einsatzleiter.alarm")


def _eligible(now: datetime):
    errors = [and_(
        AlarmIngest.process_status == "error",
        func.coalesce(AlarmIngest.process_attempts, 0) == attempt,
        func.coalesce(AlarmIngest.parsed_at, AlarmIngest.received_at)
        < now - timedelta(seconds=30 * 2 ** max(attempt - 1, 0)),
    ) for attempt in range(5)]
    return or_(
        and_(AlarmIngest.process_status == "stored",
             AlarmIngest.received_at < now - timedelta(seconds=20)),
        *errors,
        and_(AlarmIngest.process_status == "processing", or_(
            AlarmIngest.lease_until <= now,
            and_(AlarmIngest.lease_until.is_(None),
                 AlarmIngest.received_at < now - timedelta(minutes=2)),
        )),
    )


def run_alarm_ingest_recovery_once() -> None:
    """Pro Org hoechstens 100 faellige Datensaetze aufnehmen.

    Fehler warten 30, 60, 120, 240 Sekunden ab dem letzten Parse-Versuch;
    Altbestand ohne parsed_at verwendet received_at. Ein Claim bleibt bis
    zum Verarbeitungscommit gesperrt, der Callback gehoert nur dem Gewinner.
    """
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org_ids = [row[0] for row in db.query(FireDept.id).all()]
    for org_id in org_ids:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            now = datetime.now(UTC).replace(tzinfo=None)
            ids = [row[0] for row in db.query(AlarmIngest.id).filter(
                AlarmIngest.org_id == org_id, _eligible(now),
            ).order_by(AlarmIngest.id).limit(100).all()]
        for ingest_id in ids:
            with SessionLocal() as db:
                set_tenant_context(db, None)

                def after_applied(ingest: AlarmIngest, org_id: int = org_id) -> None:
                    if ingest.dedup_action == "duplicate":
                        return
                    tasks = BackgroundTasks()
                    alarm_apply_service.nachlauf(
                        db, ingest, org_id=org_id, background_tasks=tasks,
                        base_url=settings.effective_public_base_url,
                    )
                    run_async(tasks)

                try:
                    ingest = process_ingest(
                        db, ingest_id, org_id=org_id, on_applied=after_applied,
                        claim_filter=_eligible(datetime.now(UTC).replace(tzinfo=None)),
                    )
                    alarm_apply_service.melde_ingest_geaendert(ingest)
                except Exception:
                    db.rollback()
                    logger.exception("Alarm-Recovery fehlgeschlagen (ingest %s, org %s)",
                                     ingest_id, org_id)


async def alarm_ingest_recovery_loop() -> None:
    while True:
        try:
            with iteration_watch(logger, "alarm_ingest_recovery_loop", 15):
                await asyncio.to_thread(run_alarm_ingest_recovery_once)
        except Exception:
            logger.exception("Alarm-Recovery-Iteration fehlgeschlagen")
        await asyncio.sleep(15)
