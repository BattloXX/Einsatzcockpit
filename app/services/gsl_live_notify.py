"""Live-Status einer GSL per WebSocket und Web-Push verteilen."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentOrg
from app.models.major_incident import MajorIncident, MajorIncidentStatus
from app.services.incident_live_notify import _duration, _utcnow_naive

logger = logging.getLogger("einsatzleiter.gsl_live_notify")


def _claim_push(db: Session, lage: MajorIncident, sig: str, reason: str) -> bool:
    now = _utcnow_naive()
    conditions = [MajorIncident.id == lage.id]
    if reason != "closed":
        conditions.append(or_(
            MajorIncident.live_push_at.is_(None),
            MajorIncident.live_push_at < now - timedelta(seconds=90),
        ))
    if reason == "counts":
        conditions.append(or_(
            MajorIncident.live_push_sig.is_(None), MajorIncident.live_push_sig != sig,
        ))
    # Bewusste Bulk-Ausnahme: ein einzelner, bereits geladener PK wird atomar aktualisiert;
    # es gibt weder eine ungefilterte Tenant-Mutation noch ein Cross-Org-Ziel.
    result = db.execute(update(MajorIncident).where(and_(*conditions)).values(
        live_push_sig=sig, live_push_at=now,
    ))
    if result.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        return False
    db.commit()
    return True


def _dispatch_gsl_push(lage_id: int, org_id: int | None, reason: str) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.gsl_live_service import build_gsl_live_payload, format_counts
    from app.services.push_service import notify_org_web

    if org_id is None:
        return
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        if lage is None or lage.org_id != org_id:
            return
        payload = build_gsl_live_payload(db, lage)
        counts = payload["counts"]
        sig = f"{counts['neu']}-{counts['in_arbeit']}-{counts['erledigt']}"
        if not _claim_push(db, lage, sig, reason):
            return
        now = _utcnow_naive()
        if reason == "closed":
            kind, title = "gsl_live_end", "Lage beendet"
            body = f"{lage.name} - {counts['gesamt']} Einsatzstellen - Dauer {_duration(lage.started_at, now)}"
        else:
            kind = "gsl_live"
            prefix = "[UEBUNG] " if lage.is_exercise else ""
            title = f"{prefix}LAGE: {lage.name}"
            body = f"{format_counts(counts)} · seit {_duration(lage.started_at, now)}"
        extra = {"kind": kind, "tag": f"ec-gsl-{lage.id}", "live": {
            **counts, "started_at": payload["started_at"],
            "server_time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "is_exercise": lage.is_exercise, "alert": reason == "created",
        }}
        notify_org_web(db, org_id, title, body, payload["url"], extra=extra)
    finally:
        db.close()


async def notify_gsl_live(db: Session, lage: MajorIncident, *, org_id: int | None,
                          reason: str, background_tasks=None) -> None:
    from app.services.broadcast import broadcast_org
    from app.services.gsl_live_service import build_gsl_live_payload

    payload = build_gsl_live_payload(db, lage)
    if org_id is not None:
        lage_count = db.query(MajorIncident).filter(
            MajorIncident.org_id == org_id,
            MajorIncident.status == MajorIncidentStatus.active,
        ).count()
        collaborating = db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == org_id)
        incident_count = db.query(Incident).filter(
            Incident.status == "active",
            or_(Incident.primary_org_id == org_id, Incident.id.in_(collaborating)),
        ).count()
        await broadcast_org(org_id, {
            "type": "gsl_live", "lage": None if reason == "closed" else payload,
            "lage_count": lage_count,
            "incident_count": incident_count,
            "server_time": _utcnow_naive().strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    if background_tasks is not None:
        background_tasks.add_task(_dispatch_gsl_push, lage.id, org_id, reason)
        return
    try:
        await asyncio.to_thread(_dispatch_gsl_push, lage.id, org_id, reason)
    except Exception:
        logger.exception("Live-Push fehlgeschlagen (Lage %s)", lage.id)
