"""Nachträgliche, mandantengetrennte Zuordnung wartender Eingangsmails."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang
from app.services.mail_eingang_service import ereignis
from app.services.mail_zuordnung_service import ordne_zu

logger = logging.getLogger("einsatzleiter.mail_zuordnung_loop")
_CLAIM = "in_bearbeitung"
_CLAIM_LEASE = timedelta(minutes=5)
_BATCH_SIZE = 25


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _lookback(settings_row: OrgSettings) -> timedelta:
    return timedelta(minutes=max(5, min(int(settings_row.mail_eingang_rueckblick_min or 30), 240)))


def _claim_waiting_mails(db: Session, now: datetime, limit: int = _BATCH_SIZE) -> list[int]:
    """Atomar reservieren; SKIP LOCKED wirkt auf MariaDB, das UPDATE auf SQLite."""
    query = (
        db.query(OrgMailEingang.id)
        .execution_options(include_all_tenants=True)
        .join(OrgSettings, OrgSettings.org_id == OrgMailEingang.org_id)
        .filter(
            OrgMailEingang.message_id_ref.is_(None),
            OrgMailEingang.abgerufen_at.is_not(None),
            OrgSettings.mail_eingang_modus != "aus",
            (
                ((OrgMailEingang.status == "wartend") & (OrgMailEingang.zuordnung == "wartend"))
                | ((OrgMailEingang.zuordnung == _CLAIM) & (OrgMailEingang.zugeordnet_at < now - _CLAIM_LEASE))
            ),
        )
        .order_by(OrgMailEingang.empfangen_at, OrgMailEingang.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    candidate_ids = [row_id for (row_id,) in query.all()]
    claimed: list[int] = []
    for row_id in candidate_ids:
        result = db.execute(
            update(OrgMailEingang)
            .where(
                OrgMailEingang.id == row_id,
                OrgMailEingang.message_id_ref.is_(None),
                (
                    ((OrgMailEingang.status == "wartend") & (OrgMailEingang.zuordnung == "wartend"))
                    | ((OrgMailEingang.zuordnung == _CLAIM) & (OrgMailEingang.zugeordnet_at < now - _CLAIM_LEASE))
                ),
            )
            .values(zuordnung=_CLAIM, zugeordnet_at=now)
        )
        changed = getattr(result, "rowcount", 0)
        if changed:
            claimed.append(row_id)
    db.commit()
    return claimed


def _eligible_incident_ids(db: Session, row: OrgMailEingang, settings_row: OrgSettings) -> set[int]:
    """Aktiv jetzt, und entweder schon beim Empfang aktiv oder zeitnah gestartet."""
    latest_start = row.empfangen_at + _lookback(settings_row)
    incidents = (
        db.query(Incident.id)
        .execution_options(include_all_tenants=True)
        .filter(
            Incident.primary_org_id == row.org_id,
            Incident.status == "active",
            Incident.is_exercise.is_(False),
            (Incident.started_at <= row.empfangen_at) | (Incident.started_at <= latest_start),
        )
        .all()
    )
    return {incident_id for (incident_id,) in incidents}


async def _process_claimed_mail(row_id: int, now: datetime) -> bool:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = (
            db.query(OrgMailEingang)
            .execution_options(include_all_tenants=True)
            .filter(OrgMailEingang.id == row_id, OrgMailEingang.zuordnung == _CLAIM)
            .first()
        )
        if row is None or row.org_id is None:
            return False
        settings_row = db.query(OrgSettings).execution_options(include_all_tenants=True).filter(
            OrgSettings.org_id == row.org_id
        ).first()
        if settings_row is None or settings_row.mail_eingang_modus == "aus":
            row.status, row.zuordnung, row.zugeordnet_at = "wartend", "wartend", None
            db.commit()
            return False
        if row.empfangen_at + _lookback(settings_row) < now:
            row.status, row.zuordnung = "posteingang", "wartend"
            row.zuordnung_grund, row.zugeordnet_at = "Rueckblickfenster abgelaufen", None
            ereignis(db, row, "rueckblick_abgelaufen", row.zuordnung_grund)
            db.commit()
            return False
        assigned = await ordne_zu(db, row_id, incident_ids=_eligible_incident_ids(db, row, settings_row))
        if not assigned:
            db.refresh(row)
            if row.message_id_ref is None and row.zuordnung == _CLAIM:
                row.status, row.zuordnung, row.zugeordnet_at = "wartend", "wartend", None
                db.commit()
        return assigned
    except Exception:
        db.rollback()
        # Kein Mailinhalt, Betreff oder Absender im Log.
        logger.exception("mail_zuordnung.failed (mail_id=%s)", row_id)
        row = db.query(OrgMailEingang).execution_options(include_all_tenants=True).filter(
            OrgMailEingang.id == row_id, OrgMailEingang.zuordnung == _CLAIM
        ).first()
        if row is not None:
            row.status, row.zuordnung, row.zugeordnet_at = "wartend", "wartend", None
            db.commit()
        return False
    finally:
        db.close()


async def run_mail_zuordnung_once(db: Session | None = None, now: datetime | None = None) -> int:
    """Bearbeitet einen kleinen Batch; einzelne defekte Mails bleiben isoliert."""
    now = now or _now()
    own_session = db is None
    db = db or SessionLocal()
    if own_session:
        set_tenant_context(db, None)
    try:
        claimed = _claim_waiting_mails(db, now)
    except Exception:
        db.rollback()
        logger.exception("mail_zuordnung.claim_failed")
        return 0
    finally:
        if own_session:
            db.close()
    results = await asyncio.gather(*(_process_claimed_mail(row_id, now) for row_id in claimed))
    return sum(results)


async def mail_zuordnung_loop() -> None:
    """Leader-Loop, durch Konfiguration insbesondere in Tests abschaltbar."""
    if not settings.MAIL_ZUORDNUNG_LOOP_ENABLED:
        return
    while True:
        try:
            await run_mail_zuordnung_once()
        except Exception:
            logger.exception("mail_zuordnung.loop_failed")
        await asyncio.sleep(max(1, settings.MAIL_ZUORDNUNG_LOOP_INTERVAL_S))
