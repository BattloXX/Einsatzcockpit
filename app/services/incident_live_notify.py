"""Live-Status eines Einsatzes per WebSocket und Web-Push verteilen.

Der WebSocket-Pfad laeuft sofort in der aufrufenden Session und wird nie gedrosselt.
Der langsamere Web-Push-Pfad verwendet dagegen eine eigene Session und persistenten
Drosselzustand, damit mehrere Gunicorn-Worker keine doppelten Updates verschicken.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentOrg

logger = logging.getLogger("einsatzleiter.incident_live_notify")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _live_extra(payload: dict, *, alert: bool, kind: str) -> dict:
    now = _utcnow_naive()
    return {
        "kind": kind,
        "tag": f"ec-einsatz-{payload['id']}",
        "live": {
            "incident_id": payload["id"],
            "alarm_type_code": payload["alarm_type_code"],
            "address": payload["address"],
            "started_at": payload["started_at"],
            "server_time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": payload["phase"],
            "phase_index": payload["phase_index"],
            "phase_count": payload["phase_count"],
            "phase_label": payload["phase_label"],
            "unit_count": payload["unit_count"],
            "is_exercise": payload["is_exercise"],
            "alert": alert,
        },
    }


def _duration(started_at: datetime | None, now: datetime) -> str:
    if started_at is None:
        return "unbekannt"
    started = started_at.replace(tzinfo=None)
    minutes = max(0, int((now - started).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def _claim_push(db: Session, incident: Incident, phase_index: int, reason: str) -> bool:
    now = _utcnow_naive()
    conditions = [Incident.id == incident.id]
    if reason != "closed":
        conditions.append(
            or_(Incident.live_push_at.is_(None), Incident.live_push_at < now - timedelta(seconds=90))
        )
    if reason == "unit_status":
        conditions.append(
            or_(Incident.live_push_phase.is_(None), Incident.live_push_phase != phase_index)
        )
    # Bewusste Bulk-Ausnahme: ein einzelner, bereits geladener PK wird atomar aktualisiert;
    # es gibt weder eine ungefilterte Tenant-Mutation noch ein Cross-Org-Ziel.
    result = db.execute(
        update(Incident)
        .where(and_(*conditions))
        .values(live_push_phase=phase_index, live_push_at=now)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        return False
    db.commit()
    return True


def _dispatch_live_push(incident_id: int, org_id: int | None, reason: str) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.einsatz_live_service import build_incident_live_payload
    from app.services.push_service import notify_org_web

    if org_id is None or reason == "reopened":
        # Wiedereroeffnung soll die Live-Anzeige synchron halten (WS-Broadcast oben
        # in notify_incident_live laeuft unabhaengig davon immer), aber keinen neuen
        # Push ausloesen -- das waere sonst eine Benachrichtigung fuer ein Ereignis,
        # das kein neuer Alarm ist.
        return
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if incident is None:
            return
        payload = build_incident_live_payload(db, incident)
        if not _claim_push(db, incident, payload["phase_index"], reason):
            return
        address = payload["address"] or "Kein Ort angegeben"
        alarm_code = payload["alarm_type_code"]
        if reason == "closed":
            now = _utcnow_naive()
            kind = "einsatz_live_end"
            title = "Einsatz beendet"
            body = f"{alarm_code} - {address} - Dauer {_duration(incident.started_at, now)}"
            url = f"/archiv/{incident.id}"
        else:
            kind = "einsatz_live"
            prefix = "[UEBUNG] " if incident.is_exercise else ""
            title = f"{prefix}{alarm_code}"
            body = f"{address} -- {payload['phase_label']} ({payload['unit_count']} Einheiten)"
            url = payload["url"]
        notify_org_web(
            db, org_id, title, body, url,
            extra=_live_extra(payload, alert=reason == "created", kind=kind),
        )
    finally:
        db.close()


async def notify_incident_live(
    db: Session,
    incident: Incident,
    *,
    org_id: int | None,
    reason: str,
    background_tasks=None,
) -> None:
    """Broadcastet immer sofort und stellt den gedrosselten Push getrennt zu."""
    from app.services.broadcast import broadcast_org
    from app.services.einsatz_live_service import build_incident_live_payload

    payload = build_incident_live_payload(db, incident)
    if org_id is not None:
        collaborating = db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == org_id)
        incident_count = db.query(Incident).filter(
            Incident.status == "active",
            or_(Incident.primary_org_id == org_id, Incident.id.in_(collaborating)),
        ).count()
        await broadcast_org(
            org_id, {"type": "einsatz_live", **payload, "incident_count": incident_count}
        )

    if background_tasks is not None:
        background_tasks.add_task(_dispatch_live_push, incident.id, org_id, reason)
        return
    try:
        await asyncio.to_thread(_dispatch_live_push, incident.id, org_id, reason)
    except Exception:
        logger.exception("Live-Push fehlgeschlagen (Einsatz %s)", incident.id)
