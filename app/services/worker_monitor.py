"""Gemeinsame Betriebsdiagnose: explizite Tenant-Filter, keine Payloads oder Tokens."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_

from app.models.gateway import Gateway, PrintJob
from app.models.sms import SmsDispatchJob
from app.models.user import SmsGatewayToken
from app.services import gateway_presence as presence
from app.services.system_diagnose import active_workers as active_workers
from app.services.system_diagnose import worker_status

SMS_OPEN = ("queued", "claimed", "dispatched", "acknowledged")
PRINT_OPEN = ("queued", "leased", "fetching", "sent", "printing")


def _job_counts(db, model, org_id, active, expiry, now):
    counts = dict(db.query(model.status, func.count()).filter(model.org_id == org_id)
                  .group_by(model.status).all())
    query = db.query(model).filter(model.org_id == org_id, model.status.in_(active))
    return {"open": sum(counts.get(s, 0) for s in active), "uncertain": counts.get("uncertain", 0),
            "overdue": query.filter(expiry <= now).count(), "states": counts}


def snapshot(db, org_id: int, now: datetime | None = None) -> dict:
    now = presence.utc(now)
    runtime = worker_status(db, now)["evidence"]
    gateways = []
    for kind, model in (("sms", SmsGatewayToken), ("print", Gateway)):
        query = db.query(model).filter(model.org_id == org_id)
        if kind == "sms":
            rows = query.filter(SmsGatewayToken.revoked_at.is_(None)).all()
        else:
            rows = query.filter(Gateway.kind.in_(("linux", "windows_agent")),
                                Gateway.device_token_hash.isnot(None)).all()
        for row in rows:
            sms = kind == "sms"
            gateways.append({"kind": "sms" if sms else row.kind, "id": row.id,
                             "name": row.label if sms else row.name, "online": presence.online(row, now),
                             "heartbeat_at": row.last_heartbeat_at if sms else row.agent_heartbeat_at,
                             "expires_at": row.presence_expires_at if sms else row.agent_presence_expires_at,
                             "worker_id": row.worker_id if sms else row.agent_worker_id})
    sms = _job_counts(db, SmsDispatchJob, org_id, SMS_OPEN, SmsDispatchJob.expires_at, now)
    sms["overdue"] = db.query(SmsDispatchJob).filter(
        SmsDispatchJob.org_id == org_id, SmsDispatchJob.status.in_(SMS_OPEN),
        or_(SmsDispatchJob.expires_at <= now,
            and_(SmsDispatchJob.status != "queued", SmsDispatchJob.lease_until <= now),
            and_(SmsDispatchJob.status == "queued", SmsDispatchJob.created_at <= now - timedelta(minutes=5))),
    ).count()
    sms["overdue_acks"] = db.query(SmsDispatchJob).filter(
        SmsDispatchJob.org_id == org_id, SmsDispatchJob.status == "dispatched",
        SmsDispatchJob.received_at.is_(None), SmsDispatchJob.lease_until <= now,
    ).count()
    printing = _job_counts(db, PrintJob, org_id, PRINT_OPEN, PrintJob.lease_expires_at, now)
    printing["overdue_acks"] = db.query(PrintJob).filter(
        PrintJob.org_id == org_id, PrintJob.status == "sent",
        PrintJob.phase.in_(("prepared", "dispatched", "leased", "fetching")),
        PrintJob.lease_expires_at <= now,
    ).count()
    printing["overdue"] = db.query(PrintJob).filter(
        PrintJob.org_id == org_id, PrintJob.status.in_(PRINT_OPEN),
        or_(PrintJob.lease_expires_at <= now,
            and_(PrintJob.lease_expires_at.is_(None), PrintJob.erstellt_am <= now - timedelta(minutes=5))),
    ).count()
    return {"worker_count": runtime["worker_count"], "workers": runtime["workers"],
            "leader_workers": runtime["leader_workers"], "last_success_at": runtime["last_success_at"],
            "redis": runtime["redis"],
            "gateways": gateways, "sms_connected": sum(g["online"] for g in gateways if g["kind"] == "sms"),
            "print_connected": sum(g["online"] for g in gateways if g["kind"] != "sms"),
            "sms_jobs": sms, "print_jobs": printing}


def checks(diagnostics: dict):
    from app.services.dienst_monitor_service import DienstCheck, DienstTeil

    data = diagnostics
    # Ohne registrierten Worker kann auch kein aktiver Leader bestaetigt werden.
    faulty = [w for w in data["workers"] if w["leader_error"] or
              any(loop["overdue"] or loop["state"] == "failed" for loop in w["loops"])]
    broken_start = any(w["leader"] and w["leader_error"] == "loop_start_failed" for w in data["workers"])
    state = "down" if len(data["leader_workers"]) != 1 or broken_start else "teilweise" if faulty else "ok"
    result = [DienstCheck("worker_runtime", state,
                          f'{data["worker_count"]} aktive Worker; {len(data["leader_workers"])} Leader; '
                          f'{len(faulty)} Worker mit Lock-/Loop-Stoerung.', True)]
    redis = data["redis"]
    state = {"ok": "ok", "partial": "teilweise", "down": "down", "not_configured": "unknown"}[redis["state"]]
    result.append(DienstCheck("redis_bus", state,
                             f'{redis["connected_workers"]}/{redis["configured_workers"]} '
                             'konfigurierte Worker verbunden. '
                             'Kritische Jobs werden aus der DB verarbeitet.', redis["configured"]))
    for key, label in (("sms_jobs", "SMS"), ("print_jobs", "Druck")):
        jobs = data[key]
        state = "down" if jobs["overdue"] else "teilweise" if jobs["uncertain"] else "ok"
        detail = (f'{jobs["open"]} offen, {jobs["overdue"]} ueberfaellig, '
                  f'{jobs["overdue_acks"]} ACKs ueberfaellig, {jobs["uncertain"]} unklar.')
        result.append(DienstCheck(key, state, detail, True, (DienstTeil(key, label, state, detail),)))
    return result
