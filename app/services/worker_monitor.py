"""Gemeinsame Betriebsdiagnose: explizite Tenant-Filter, keine Payloads oder Tokens."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_

from app.models.gateway import Gateway, PrintJob
from app.models.sms import SmsDispatchJob
from app.models.user import SmsGatewayToken
from app.models.worker_runtime import WorkerRuntime
from app.services import gateway_presence as presence
from app.services import ws_bus

SMS_OPEN = ("queued", "claimed", "dispatched", "acknowledged")
PRINT_OPEN = ("queued", "leased", "fetching", "sent", "printing")


def _datetime(value):
    return presence.utc(datetime.fromisoformat(value)) if value else None


def _job_counts(db, model, org_id, active, expiry, now):
    counts = dict(db.query(model.status, func.count()).filter(model.org_id == org_id)
                  .group_by(model.status).all())
    query = db.query(model).filter(model.org_id == org_id, model.status.in_(active))
    return {"open": sum(counts.get(s, 0) for s in active), "uncertain": counts.get("uncertain", 0),
            "overdue": query.filter(expiry <= now).count(), "states": counts}


def snapshot(db, org_id: int, now: datetime | None = None) -> dict:
    now = presence.utc(now)
    recent = db.query(WorkerRuntime).filter(WorkerRuntime.heartbeat_at > now - timedelta(days=1))\
        .order_by(WorkerRuntime.worker_id).all()
    workers = [worker for worker in recent if worker.expires_at > now]
    leaders = [w for w in workers if w.is_leader]
    worker_rows = []
    for worker in workers:
        loops = []
        for name, values in sorted(worker.loops.items()):
            row = {"name": name, **values}
            last_start = values.get("last_started_at")
            running_age = 0.0
            if values.get("state") == "running" and last_start:
                try:
                    running_age = max(0, (now - presence.utc(datetime.fromisoformat(last_start))).total_seconds())
                except ValueError:
                    running_age = float("inf")
            row["overdue"] = running_age > max(60, 3 * values.get("interval_s", 0))
            for field in ("last_started_at", "last_success_at", "last_failure_at"):
                if row.get(field):
                    row[field] = _datetime(row[field])
            loops.append(row)
        worker_rows.append({"worker_id": worker.worker_id, "hostname": worker.hostname, "pid": worker.pid,
                            "heartbeat_at": worker.heartbeat_at, "expires_at": worker.expires_at,
                            "leader": worker.is_leader, "leader_since": worker.leader_since,
                            "leader_error": worker.leader_error,
                            "redis": {**worker.redis, "last_error_at": _datetime(worker.redis.get("last_error_at"))},
                            "loops": loops})
    redis_workers = [w for w in workers if w.redis.get("configured")]
    redis_up = sum(bool(w.redis.get("connected")) for w in redis_workers)
    configured = ws_bus.enabled() or bool(redis_workers)
    redis_state = ("not_configured" if not configured else "down" if not redis_workers
                   else "ok" if redis_up == len(redis_workers)
                   else "down" if not redis_up else "partial")
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
    last_success = max((values.get("last_success_at") for w in recent for values in w.loops.values()
                        if values.get("last_success_at")), default=None)
    return {"worker_count": len(workers), "workers": worker_rows,
            "leader_workers": [w.worker_id for w in leaders], "last_success_at": _datetime(last_success),
            "redis": {"state": redis_state, "configured": configured,
                      "connected_workers": redis_up, "configured_workers": len(redis_workers)},
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
