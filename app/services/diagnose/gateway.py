"""Vorhandene Versandfristen und Presence-Leases lesen, keine Payloads."""
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

from app.models.admin_security import utcnow
from app.models.dienst_monitor import DienstStatus
from app.models.gateway import Gateway, GatewayMessage, PrintJob
from app.models.incident import IncidentAlarmJob
from app.models.sms import SmsDispatchAttempt, SmsDispatchJob
from app.models.user import SmsGatewayToken
from app.services.diagnose.helpers import collect_ids
from app.services.diagnose.registry import MAX_IDS, CheckResult, Finding, register_check
from app.services.worker_monitor import PRINT_OPEN, SMS_OPEN


@register_check("gateway.jobs", "gateway", "Presence, Versandfristen, Attempts und Quittierungen")
def check(db, budget):
    now = utcnow()
    old = now - timedelta(minutes=5)
    result = CheckResult()
    for model, states, date, lease, attempts in (
        (PrintJob, PRINT_OPEN, PrintJob.erstellt_am, PrintJob.lease_expires_at, PrintJob.attempts),
        (SmsDispatchJob, SMS_OPEN, SmsDispatchJob.created_at, SmsDispatchJob.lease_until, SmsDispatchJob.generation),
        (IncidentAlarmJob, ("pending", "claimed"), IncidentAlarmJob.next_attempt_at,
         IncidentAlarmJob.lease_until, IncidentAlarmJob.attempt_count),
    ):
        name = model.__tablename__
        collect_ids(db, budget, result, name, and_(model.status.in_(states), or_(lease <= now,
            and_(lease.is_(None), date < old))), [name, "ueberfaellig"], "Ueberfaellige Versandauftraege")
        collect_ids(db, budget, result, name, and_(model.status.in_(states), attempts >= 5),
                    [name, "attempts"], "Viele Versandversuche")
    collect_ids(db, budget, result, "sms_dispatch_job", and_(SmsDispatchJob.status.in_(SMS_OPEN),
        SmsDispatchJob.expires_at <= now), "sms_abgelaufen", "SMS-Versandfrist abgelaufen")
    collect_ids(db, budget, result, "sms_dispatch_job", and_(SmsDispatchJob.status == "dispatched",
        SmsDispatchJob.received_at.is_(None), SmsDispatchJob.lease_until <= now), "sms_ack", "SMS-Quittierung fehlt")
    collect_ids(db, budget, result, "print_job", and_(PrintJob.status == "sent",
        PrintJob.phase.in_(("prepared", "dispatched", "leased", "fetching")), PrintJob.lease_expires_at <= now),
        "druck_ack", "Druck-Quittierung fehlt")
    for model, expiry, connection, token, expected, configured in (
        (Gateway, Gateway.agent_presence_expires_at, Gateway.agent_connection_id,
         Gateway.agent_connection_token_hash, Gateway.device_token_hash,
         and_(Gateway.kind.in_(("linux", "windows_agent")), Gateway.device_token_hash.isnot(None))),
        (SmsGatewayToken, SmsGatewayToken.presence_expires_at, SmsGatewayToken.connection_id,
         SmsGatewayToken.connection_token_hash, SmsGatewayToken.token_hash, SmsGatewayToken.revoked_at.is_(None)),
    ):
        collect_ids(db, budget, result, model.__tablename__, and_(configured, or_(expiry.is_(None),
            expiry <= now, connection.is_(None), token.is_(None), token != expected)),
            [model.__tablename__, "presence"], "Gateway ohne gueltige Praesenz")
    collect_ids(db, budget, result, "gateway_message", and_(GatewayMessage.state == "queued",
        GatewayMessage.expires_at <= now), "transport_abgelaufen", "Gateway-Transportzustellung abgelaufen")
    collect_ids(db, budget, result, "dienst_status", and_(DienstStatus.key == "alarm_seriell",
        DienstStatus.state.in_(("down", "teilweise"))), "wt_dienst", "W&T-Dienstmonitor auffaellig")
    a = SmsDispatchAttempt.__table__
    rows = budget.rows(db, select(a.c.job_id).where(a.c.outcome == "claimed", a.c.finished_at.is_(None))
        .group_by(a.c.job_id).having(func.count() > 1).order_by(a.c.job_id))
    if rows:
        result.findings.append(Finding("mehrfach_claim", "Mehrere offene SMS-Claims (Indikator)", "fail",
            {"anzahl_mindestens": len(rows)}, {"sms_dispatch_job": [r[0] for r in rows[:MAX_IDS]]}))
    return result
