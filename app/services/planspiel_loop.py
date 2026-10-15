"""Hintergrund-Loop fuer laufende Planspiele.

Laeuft im Leader-Worker wie die uebrigen Loops. Die eigentliche Absicherung
gegen Doppelausfuehrung liegt aber in der Datenbank: Jeder Run wird per
bedingtem UPDATE mit einem Lease beansprucht, jedes Ereignis zusaetzlich
einzeln (siehe planspiel_engine.ereignis_beanspruchen). Faellt der Leader-Lock
fail-open aus und laufen zwei Prozesse, fuehrt trotzdem nur einer aus.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings, SystemSettings
from app.models.planspiel import PlanspielRun
from app.services.loop_utils import iteration_watch
from app.services.planspiel_engine import (
    AusfuehrungsErgebnis,
    GeneriertErgebnis,
    nach_ausfuehrung_benachrichtigen,
    nach_generierung_benachrichtigen,
    tick,
)

logger = logging.getLogger("einsatzleiter.planspiel_loop")

_AKTIV = ("laeuft", "generierung_gestoppt")
_RUN_LEASE = timedelta(seconds=30)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def aktive_runs(db: Session) -> list[tuple[int, int]]:
    """(run_id, org_id) aller laufenden Runs von Orgs mit aktivem Modul."""
    system = db.query(SystemSettings).filter(SystemSettings.key == "planspiel_module_enabled").first()
    if system is None or system.value != "true":
        return []
    orgs = {
        row.org_id
        for row in db.query(OrgSettings.org_id)
        .filter(OrgSettings.planspiel_modul_aktiv.is_(True))
        .all()
    }
    runs = (
        db.query(PlanspielRun.id, PlanspielRun.org_id)
        .execution_options(include_all_tenants=True)
        .filter(PlanspielRun.status.in_(_AKTIV))
        .order_by(PlanspielRun.id)
        .all()
    )
    return [(run_id, org_id) for run_id, org_id in runs if org_id is not None and org_id in orgs]


def run_beanspruchen(db: Session, run_id: int, org_id: int, worker_id: str, jetzt: datetime) -> bool:
    """Beansprucht einen Run fuer diesen Worker (bedingtes UPDATE, Lease)."""
    result = db.execute(
        update(PlanspielRun)
        .where(
            PlanspielRun.id == run_id,
            PlanspielRun.org_id == org_id,
            PlanspielRun.status.in_(_AKTIV),
            or_(
                PlanspielRun.worker_lease_until.is_(None),
                PlanspielRun.worker_lease_until < jetzt,
                PlanspielRun.worker_id == worker_id,
            ),
        )
        .values(worker_lease_until=jetzt + _RUN_LEASE, worker_id=worker_id)
    )
    db.commit()
    return bool(getattr(result, "rowcount", 0) == 1)


def planspiel_runde(
    db_factory: Callable[[], Session] = SessionLocal,
    worker_id: str = WORKER_ID,
    jetzt: datetime | None = None,
) -> list[tuple[int, int, AusfuehrungsErgebnis | GeneriertErgebnis]]:
    """Eine Runde: alle beanspruchbaren Runs ticken. Liefert (org_id, run_id, Ergebnis)."""
    jetzt = jetzt or _utcnow()
    db = db_factory()
    try:
        set_tenant_context(db, None)
        kandidaten = aktive_runs(db)
    finally:
        db.close()
    ergebnisse: list[tuple[int, int, AusfuehrungsErgebnis | GeneriertErgebnis]] = []
    for run_id, org_id in kandidaten:
        claim_db = db_factory()
        try:
            set_tenant_context(claim_db, org_id)
            if not run_beanspruchen(claim_db, run_id, org_id, worker_id, jetzt):
                continue
        finally:
            claim_db.close()
        try:
            tick_ergebnis = tick(db_factory, run_id, org_id, jetzt)
        except Exception:
            logger.exception("planspiel.tick_failed run=%s", run_id)
            continue
        ergebnisse.extend((org_id, run_id, item) for item in tick_ergebnis.ausgefuehrt)
        ergebnisse.extend((org_id, run_id, item) for item in tick_ergebnis.generiert)
    return ergebnisse


async def planspiel_loop() -> None:
    """Leader-Loop; in Tests ueber PLANSPIEL_LOOP_ENABLED abschaltbar."""
    if not settings.PLANSPIEL_LOOP_ENABLED:
        return
    intervall = max(1, settings.PLANSPIEL_LOOP_INTERVAL_S)
    while True:
        try:
            with iteration_watch(logger, "planspiel_loop", intervall):
                ergebnisse = await asyncio.to_thread(planspiel_runde)
            for org_id, run_id, ergebnis in ergebnisse:
                if isinstance(ergebnis, GeneriertErgebnis):
                    await nach_generierung_benachrichtigen(org_id, run_id, [ergebnis])
                else:
                    await nach_ausfuehrung_benachrichtigen(org_id, run_id, ergebnis)
        except Exception:
            logger.exception("planspiel.loop_failed")
        await asyncio.sleep(intervall)
