"""Dauerhafte SMS-Zustandsmaschine. Kein Transporterfolg gilt als SMS-Erfolg.

Lockreihenfolge: Org-Cursor -> Gateway -> Auftrag. Ein Versand nach moeglicher
Aussenwirkung wird nie automatisch an einen anderen Provider/Gateway wiederholt.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import GatewayMessage
from app.models.sms import SmsDispatchAttempt as Attempt
from app.models.sms import SmsDispatchCursor as Cursor
from app.models.sms import SmsDispatchJob as Job
from app.models.sms import SmsLog, SmsLogRecipient
from app.models.user import SmsGatewayToken
from app.services import gateway_presence as presence
from app.services import gateway_routing as routing
from app.services import ws_bus

logger = logging.getLogger("einsatzleiter.sms_outbox")
TERMINAL = ("sent", "failed", "uncertain")
ACTIVE = ("claimed", "dispatched", "acknowledged")
LEASE_S = 60
MAX_IN_FLIGHT = 7
_task: asyncio.Task | None = None


def database():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def snapshot(org_id: int, job_id: str) -> dict | None:
    with database() as db:
        job = db.query(Job).filter(Job.org_id == org_id, Job.id == job_id).first()
        if job is None:
            return None
        used = [
            row[0]
            for row in db.query(Attempt.provider)
            .filter(Attempt.org_id == org_id, Attempt.job_id == job.id, Attempt.provider.isnot(None))
            .distinct()
        ]
        return {
            "job_id": job.id,
            "status": job.status,
            "generation": job.generation,
            "provider": job.provider,
            "gateway_token_id": job.gateway_id,
            "error": job.error,
            "received_at": job.received_at,
            "attempted_at": job.attempted_at,
            "accepted_at": job.accepted_at,
            "delivery_status": job.delivery_status,
            "providers_used": used,
        }


def enqueue_in(
    db,
    org_id: int,
    job_key: str,
    phone: str,
    text: str,
    providers: list[str],
    preferred: int | None = None,
    recipient_id: int | None = None,
) -> str:
    if not 0 < len(job_key) <= 128:
        raise ValueError("Ungueltige SMS-Job-ID")
    payload_hash = hashlib.sha256((phone + "\0" + text).encode()).hexdigest()
    job = db.query(Job).filter(Job.org_id == org_id, Job.job_key == job_key).first()
    if job is None:
        now = presence.utc()
        job = Job(
            id=uuid4().hex,
            org_id=org_id,
            job_key=job_key,
            payload_hash=payload_hash,
            phone=phone,
            text=text,
            providers=list(providers),
            preferred_gateway_id=preferred,
            next_attempt_at=now,
            expires_at=now + timedelta(hours=1),
            created_at=now,
        )
        try:
            with db.begin_nested():
                db.add(job)
                db.flush()
        except IntegrityError:
            job = db.query(Job).filter(Job.org_id == org_id, Job.job_key == job_key).with_for_update().first()
            if job is None:
                raise
    if job.payload_hash != payload_hash:
        raise ValueError("SMS-Job-ID wurde mit anderem Inhalt wiederverwendet")
    if recipient_id is not None:
        recipient = (
            db.query(SmsLogRecipient)
            .join(SmsLog)
            .filter(SmsLog.org_id == org_id, SmsLogRecipient.id == recipient_id)
            .first()
        )
        if recipient is None or recipient.phone_number != phone:
            raise ValueError("SMS-Empfaenger gehoert nicht zum Auftrag")
        recipient.dispatch_job_id, recipient.dispatch_status = job.id, job.status
    return job.id


def ensure(
    org_id: int,
    job_key: str,
    phone: str,
    text: str,
    providers: list[str],
    preferred: int | None = None,
    recipient_id: int | None = None,
) -> str:
    with database() as db:
        jid = enqueue_in(db, org_id, job_key, phone, text, providers, preferred, recipient_id)
        db.commit()
        return jid


def queue_log(db, log: SmsLog, recipients: dict, ctx) -> list[tuple]:
    """Protokoll, Empfaengersnapshots und SMS-Auftraege in derselben Transaktion."""
    assert log.org_id is not None
    db.flush()
    db.query(SmsLog).filter(SmsLog.org_id == log.org_id, SmsLog.id == log.id).with_for_update().one()
    existing = {r.phone_number: r for r in db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log.id)}
    jobs = []
    for phone, details in recipients.items():
        row = existing.get(phone)
        if row is not None and (row.success or row.dispatch_status in TERMINAL):
            continue
        if row is None:
            name, member_id = details[:2]
            row = SmsLogRecipient(
                sms_log_id=log.id,
                phone_number=phone,
                name=name,
                member_id=member_id,
                source_type=details[2] if len(details) > 2 else None,
                source_id=details[3] if len(details) > 3 else None,
                success=False,
                sent_at=log.sent_at,
                provider="ausstehend",
            )
            db.add(row)
            db.flush()
        address_key = hashlib.sha256(phone.encode()).hexdigest()[:24]
        key = (
            f"alarm:{log.incident_id}:{address_key}"
            if log.source == "alarm" and log.incident_id is not None
            else f"{log.source}:{log.id}:{address_key}"
        )
        enqueue_in(db, log.org_id, key, phone, log.text, ctx.chain, recipient_id=row.id)
        if log.source == "alarm":
            db.query(Job).filter(
                Job.org_id == log.org_id, Job.id == row.dispatch_job_id, Job.status == "queued"
            ).update({"expires_at": log.sent_at.replace(tzinfo=None) + timedelta(hours=1)}, synchronize_session=False)
        jobs.append((phone, log.text, key, row.id))
    return jobs


def claim(org_id: int, job_id: str) -> str | None:
    now, token = presence.utc(), uuid4().hex
    with database() as db:
        changed = (
            db.query(Job)
            .filter(
                Job.org_id == org_id,
                Job.id == job_id,
                Job.status == "queued",
                Job.next_attempt_at <= now,
                Job.expires_at > now,
            )
            .update(
                {
                    "status": "claimed",
                    "claim_token": token,
                    "claimed_by": presence.worker_id(),
                    "generation": Job.generation + 1,
                    "lease_until": now + timedelta(seconds=LEASE_S),
                    "gateway_id": None,
                    "connection_id": None,
                    "provider": None,
                    "may_have_sent": False,
                    "received_at": None,
                    "attempted_at": None,
                    "accepted_at": None,
                    "error": None,
                },
                synchronize_session=False,
            )
        )
        if not changed:
            return None
        job = db.query(Job).filter(Job.org_id == org_id, Job.id == job_id).populate_existing().one()
        db.add(Attempt(job_id=job.id, generation=job.generation, org_id=org_id, created_at=now))
        db.commit()
        return token


def route(db, connection, message, *, job_key=None):
    now = presence.utc()
    item = GatewayMessage(
        id=uuid4().hex,
        org_id=connection.org_id,
        gateway_kind="sms",
        gateway_id=connection.gateway_id,
        connection_id=connection.connection_id,
        worker_id=connection.worker_id,
        message=message,
        job_key=job_key,
        state="queued",
        created_at=now,
        expires_at=now + timedelta(seconds=LEASE_S),
    )
    db.add(item)
    db.flush()
    return item.id


async def notify(item_ids):
    for item_id, connection in item_ids:
        await routing.deliver(item_id)
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, item_id))


def _cursor(db, org_id):
    row = db.query(Cursor).filter(Cursor.org_id == org_id).first()
    if row is None:
        try:
            with db.begin_nested():
                db.add(Cursor(org_id=org_id))
                db.flush()
        except IntegrityError:
            pass
    return db.query(Cursor).filter(Cursor.org_id == org_id).with_for_update().populate_existing().one()


def _prepare(org_id, job_id, token, eus_available: bool, eus_timeout: int):
    with database() as db:
        if db.get_bind().dialect.name == "sqlite":
            # SQLite ignoriert FOR UPDATE. Auch dort Cursor und Kapazitaet atomar lesen/aendern.
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            # Nach dem Warten auf den Org-Cursor muss die Kapazitaetsabfrage den
            # letzten Commit sehen, keinen vorher erzeugten Repeatable-Read-Snapshot.
            db.connection(execution_options={"isolation_level": "READ COMMITTED"})
        cursor = _cursor(db, org_id)
        gateways = (
            db.query(SmsGatewayToken)
            .filter(SmsGatewayToken.org_id == org_id)
            .order_by(SmsGatewayToken.id)
            .with_for_update()
            .all()
        )
        job = (
            db.query(Job)
            .filter(Job.org_id == org_id, Job.id == job_id, Job.status == "claimed", Job.claim_token == token)
            .with_for_update()
            .first()
        )
        if job is None:
            return None, []
        attempt = (
            db.query(Attempt)
            .filter(Attempt.org_id == org_id, Attempt.job_id == job.id, Attempt.generation == job.generation)
            .one()
        )
        now = presence.utc()
        if job.lease_until <= now:
            return None, []
        selected = None
        index = job.provider_index
        while index < len(job.providers):
            provider = job.providers[index]
            if provider == "eus" and eus_available:
                selected = "eus"
                break
            if provider == "gateway":
                online = [gw for gw in gateways if presence.online(gw) and gw.worker_id]
                free = [
                    gw
                    for gw in online
                    if len(
                        db.query(Job.id)
                        .filter(
                            Job.org_id == org_id, Job.gateway_id == gw.id, Job.status.in_(ACTIVE), Job.lease_until > now
                        )
                        .all()
                    )
                    < MAX_IN_FLIGHT
                ]
                free.sort(
                    key=lambda gw: (gw.priority, -(gw.connected_at.timestamp() if gw.connected_at else 0), -gw.id)
                )
                if free:
                    preferred = next((gw for gw in free if gw.id == job.preferred_gateway_id), None)
                    if preferred is not None:
                        selected = preferred
                    else:
                        start = next((i + 1 for i, gw in enumerate(free) if gw.id == cursor.last_gateway_id), 0)
                        selected = free[start % len(free)]
                        cursor.last_gateway_id = selected.id
                    break
                if online:  # Auslastung ist kein Grund fuer einen Providerwechsel.
                    break
            index += 1
        if selected is None:
            attempt.outcome, attempt.finished_at = "not_dispatched", now
            job.status, job.claim_token, job.lease_until = "queued", None, None
            job.next_attempt_at = now + timedelta(seconds=2)
            db.commit()
            return None, []
        job.provider_index = index
        job.provider = attempt.provider = "eus" if selected == "eus" else "gateway"
        job.status = attempt.outcome = "dispatched"
        job.lease_until = now + timedelta(seconds=max(LEASE_S, eus_timeout * 4 + 30) if selected == "eus" else LEASE_S)
        if selected == "eus":
            job.may_have_sent = True
            job.attempted_at = now
            db.commit()
            return "eus", []
        assert isinstance(selected, SmsGatewayToken)
        assert selected.connection_id and selected.worker_id and selected.presence_expires_at
        job.gateway_id = attempt.gateway_id = selected.id
        job.connection_id = selected.connection_id
        job.protocol = attempt.protocol = selected.sms_protocol
        job.may_have_sent = job.protocol < 2
        connection = presence.Connection(
            "sms", org_id, selected.id, selected.connection_id, selected.worker_id, selected.presence_expires_at
        )
        wire_id = f"{job.id}:{job.generation}"
        message = {
            "type": "sms.prepare" if job.protocol >= 2 else "sms.send",
            "id": wire_id,
            "job_id": job.id,
            "generation": job.generation,
            "to": job.phone,
            "text": job.text,
        }
        attempt.route_id = route(db, connection, message, job_key=wire_id)
        item_id = attempt.route_id
        db.commit()
        return "gateway", [(item_id, connection)]


def _complete(db, job, attempt, outcome: str, error=None, *, not_sent=False):
    now = presence.utc()
    attempt.outcome, attempt.error, attempt.finished_at = outcome, error, now
    if outcome == "failed" and not_sent and job.provider_index + 1 < len(job.providers):
        job.provider_index += 1
        job.status, job.may_have_sent = "queued", False
        job.claim_token, job.lease_until = None, None
        job.gateway_id, job.connection_id = None, None
        job.next_attempt_at = now
        job.error = error
        return
    job.status, job.error, job.finished_at = outcome, error, now
    job.lease_until = None
    if outcome == "sent":
        job.accepted_at = now
    if not_sent:
        job.may_have_sent = False


def _eus_result(org_id, job_id, token, outcome, error=None, safe=False):
    with database() as db:
        job = (
            db.query(Job)
            .filter(Job.org_id == org_id, Job.id == job_id, Job.claim_token == token, Job.status.in_(ACTIVE))
            .with_for_update()
            .first()
        )
        if job is None:
            return
        attempt = (
            db.query(Attempt)
            .filter(Attempt.org_id == org_id, Attempt.job_id == job.id, Attempt.generation == job.generation)
            .one()
        )
        _complete(db, job, attempt, outcome, error, not_sent=safe)
        db.commit()


async def drive(org_id, job_id, ctx=None):
    token = await asyncio.to_thread(claim, org_id, job_id)
    if token is None:
        return
    from app.services.eus_sms_service import EusSmsError
    from app.services.sms_service import resolve_sms_config, send_via_eus

    ctx = ctx or await asyncio.to_thread(resolve_sms_config, org_id)
    provider, routes = await asyncio.to_thread(
        _prepare, org_id, job_id, token, bool(ctx.eus), ctx.eus.timeout if ctx.eus else 15
    )
    await notify(routes)
    if provider == "eus":
        with database() as db:
            job = db.query(Job).filter(Job.org_id == org_id, Job.id == job_id, Job.claim_token == token).first()
            if job is None or job.status != "dispatched":
                return
            phone, text = job.phone, job.text
        try:
            await send_via_eus(ctx.eus, phone, text)
        except EusSmsError as exc:
            safe = exc.safe_not_sent
            await asyncio.to_thread(
                _eus_result,
                org_id,
                job_id,
                token,
                "failed" if safe else "uncertain",
                "eus_rejected" if safe else "eus_result_unknown",
                safe,
            )
        except httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout:
            await asyncio.to_thread(_eus_result, org_id, job_id, token, "failed", "eus_not_connected", True)
        except httpx.HTTPError, ValueError:
            await asyncio.to_thread(_eus_result, org_id, job_id, token, "uncertain", "eus_result_unknown")
        else:
            await asyncio.to_thread(_eus_result, org_id, job_id, token, "sent")


def parse_wire(message):
    wire = message.get("id")
    if not isinstance(wire, str) or len(wire) > 64 or ":" not in wire:
        return None
    jid, gen = wire.rsplit(":", 1)
    if len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid) or not gen.isascii() or not gen.isdecimal():
        return None
    generation = int(gen)
    if generation <= 0 or message.get("job_id", jid) != jid or message.get("generation", generation) != generation:
        return None
    return jid, generation


def event(connection, token_hash, message, *, reconcile=False):
    identity = parse_wire(message)
    if identity is None:
        return False, [], None
    jid, gen = identity
    with database() as db:
        if (
            presence.state(
                db, "sms", connection.org_id, connection.gateway_id, token_hash, connection.connection_id, lock=True
            )
            != "connected"
        ):
            return False, [], None
        job = (
            db.query(Job)
            .filter(
                Job.org_id == connection.org_id,
                Job.id == jid,
                Job.generation == gen,
                Job.gateway_id == connection.gateway_id,
                Job.provider == "gateway",
            )
            .with_for_update()
            .first()
        )
        if job is None:
            old_attempt = (
                db.query(Attempt)
                .filter(
                    Attempt.org_id == connection.org_id,
                    Attempt.job_id == jid,
                    Attempt.generation == gen,
                    Attempt.gateway_id == connection.gateway_id,
                    Attempt.provider == "gateway",
                )
                .first()
            )
            # Ein Ergebnis einer ersetzten Generation bestaetigen, ohne den aktuellen
            # Auftrag anzufassen. Sonst bleibt die lokale Ergebnis-Outbox endlos offen.
            return False, [], "superseded" if old_attempt is not None else None
        if job.status in TERMINAL:
            return False, [], job.status
        attempt = (
            db.query(Attempt)
            .filter(Attempt.org_id == connection.org_id, Attempt.job_id == jid, Attempt.generation == gen)
            .one()
        )
        kind, routes, changed = message.get("type"), [], False
        now = presence.utc()
        if kind == "sms.ack" and job.status in ("dispatched", "acknowledged"):
            first = job.status == "dispatched"
            job.received_at = job.received_at or now
            job.status = attempt.outcome = "acknowledged"
            if job.protocol >= 2 and (first or (reconcile and job.connection_id != connection.connection_id)):
                # Die Freigabe und ihre moegliche Aussenwirkung werden zusammen persistiert.
                job.may_have_sent = True
                job.connection_id = connection.connection_id
                job.lease_until = now + timedelta(seconds=LEASE_S)
                item = route(
                    db,
                    connection,
                    {"type": "sms.commit", "id": message["id"], "job_id": jid, "generation": gen},
                    job_key=message["id"],
                )
                attempt.grant_route_id = item
                routes.append((item, connection))
            changed = first
        elif kind == "sms.attempt" and job.status in ("dispatched", "acknowledged") and job.may_have_sent:
            job.attempted_at = job.attempted_at or now
            changed = True
        elif kind == "sms.result" and job.status in ("dispatched", "acknowledged"):
            if message.get("ok") is True and (job.protocol < 2 or message.get("provider_accepted") is True):
                _complete(db, job, attempt, "sent")
            elif (
                job.protocol >= 2
                and message.get("outcome") == "failed"
                and message.get("not_sent") is True
                and message.get("attempted") is False
                and job.attempted_at is None
            ):
                _complete(db, job, attempt, "failed", "gateway_rejected_before_send", not_sent=True)
            else:
                _complete(db, job, attempt, "uncertain", "gateway_result_unknown")
            changed = True
        db.commit()
        return changed, routes, job.status


def hello(connection, token_hash, payload):
    protocol = 2 if "sms-transaction-v2" in (payload.get("capabilities") or []) else 1
    with database() as db:
        db.query(SmsGatewayToken).filter(
            SmsGatewayToken.org_id == connection.org_id,
            SmsGatewayToken.id == connection.gateway_id,
            SmsGatewayToken.connection_id == connection.connection_id,
            SmsGatewayToken.token_hash == token_hash,
            SmsGatewayToken.revoked_at.is_(None),
        ).update({"sms_protocol": protocol}, synchronize_session=False)
        db.commit()
    routes, abandoned = [], []
    for item in (payload.get("open_jobs") or [])[:256]:
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        msg = {**item, "type": "sms.ack" if state == "received" else "sms.attempt"}
        _changed, controls, status = event(connection, token_hash, msg, reconcile=True)
        routes.extend(controls)
        if status is None or status in (*TERMINAL, "superseded"):
            abandoned.append({"type": "sms.abandon", "id": item.get("id"), "status": status})
    return routes, abandoned


def _cancel_queued(db, org_id, route_id):
    if route_id is None:
        return False
    return bool(
        db.query(GatewayMessage)
        .filter(GatewayMessage.org_id == org_id, GatewayMessage.id == route_id, GatewayMessage.state == "queued")
        .update({"state": "expired"}, synchronize_session=False)
    )


def sweep():
    now = presence.utc()
    with database() as db:
        ids = (
            db.query(Job.org_id, Job.id)
            .filter(
                or_(
                    (Job.status.in_(ACTIVE)) & (Job.lease_until <= now),
                    (Job.status == "queued") & (Job.expires_at <= now),
                )
            )
            .execution_options(include_all_tenants=True)
            .limit(100)
            .all()
        )
    for org_id, jid in ids:
        with database() as db:
            job = db.query(Job).filter(Job.org_id == org_id, Job.id == jid).with_for_update().first()
            if job is None or job.status in TERMINAL:
                continue
            if job.status == "queued" and job.expires_at > now:
                continue
            if job.status in ACTIVE and (job.lease_until is None or job.lease_until > now):
                continue
            if job.status == "queued":
                job.status, job.error, job.finished_at = "failed", "queue_expired_without_send", now
            else:
                attempt = (
                    db.query(Attempt)
                    .filter(Attempt.org_id == org_id, Attempt.job_id == jid, Attempt.generation == job.generation)
                    .one()
                )
                safe = job.status == "claimed" or not job.may_have_sent
                if not safe and job.provider == "gateway":
                    if job.protocol >= 2:
                        grants = (
                            db.query(GatewayMessage)
                            .filter(
                                GatewayMessage.org_id == org_id,
                                GatewayMessage.gateway_kind == "sms",
                                GatewayMessage.gateway_id == job.gateway_id,
                                GatewayMessage.job_key == f"{jid}:{job.generation}",
                            )
                            .all()
                        )
                        grants = [g for g in grants if g.message.get("type") == "sms.commit"]
                        safe = bool(grants) and all(g.state == "queued" for g in grants)
                        if safe:
                            safe = all(_cancel_queued(db, org_id, g.id) for g in grants)
                    else:
                        safe = _cancel_queued(db, org_id, attempt.route_id)
                if safe:
                    if attempt.route_id:
                        _cancel_queued(db, org_id, attempt.route_id)
                    attempt.outcome, attempt.finished_at = "not_dispatched", now
                    job.status, job.may_have_sent = "queued", False
                    job.claim_token, job.lease_until = None, None
                    job.next_attempt_at = now
                else:
                    _complete(db, job, attempt, "uncertain", "lease_expired_after_possible_send")
            db.commit()


def project(org_id, job_id):
    with database() as db:
        job = db.query(Job).filter(Job.org_id == org_id, Job.id == job_id).first()
        if job is None:
            return
        log_ids = [
            r[0]
            for r in db.query(SmsLogRecipient.sms_log_id)
            .join(SmsLog)
            .filter(SmsLog.org_id == org_id, SmsLogRecipient.dispatch_job_id == job_id)
            .distinct()
        ]
        for log_id in log_ids:
            log = db.query(SmsLog).filter(SmsLog.org_id == org_id, SmsLog.id == log_id).with_for_update().one()
            # Ein wartender Projektor darf keinen juengeren Status ueberschreiben.
            db.refresh(job)
            recipients = (
                db.query(SmsLogRecipient)
                .filter(SmsLogRecipient.sms_log_id == log_id, SmsLogRecipient.dispatch_job_id == job_id)
                .all()
            )
            for recipient in recipients:
                recipient.dispatch_status = job.status
                recipient.success = job.status == "sent"
                recipient.provider = (
                    job.provider
                    if job.status == "sent"
                    else (
                        "uncertain"
                        if job.status == "uncertain"
                        else "verworfen"
                        if job.status == "failed"
                        else "sendet"
                    )
                )
                recipient.sent_at = job.accepted_at or recipient.sent_at
                recipient.lease_until = job.lease_until
                if job.gateway_id:
                    token = (
                        db.query(SmsGatewayToken)
                        .filter(SmsGatewayToken.org_id == org_id, SmsGatewayToken.id == job.gateway_id)
                        .first()
                    )
                    recipient.gateway_label = token.label if token else None
            db.flush()
            log.success_count = (
                db.query(SmsLogRecipient.id)
                .filter(SmsLogRecipient.sms_log_id == log_id, SmsLogRecipient.success.is_(True))
                .count()
            )
            pending = (
                db.query(SmsLogRecipient.id)
                .filter(
                    SmsLogRecipient.sms_log_id == log_id,
                    SmsLogRecipient.success.is_(False),
                    SmsLogRecipient.provider.in_(("ausstehend", "sendet")),
                )
                .count()
            )
            if not pending:
                log.completed_at = presence.utc()
            else:
                log.completed_at = None
            db.commit()
        from app.models.api_message import ApiMessage, ApiMessageRecipient
        from app.services.api_message_dispatch_loop import _finish

        message_ids = [
            r[0]
            for r in db.query(ApiMessageRecipient.message_id)
            .filter(ApiMessageRecipient.org_id == org_id, ApiMessageRecipient.dispatch_job_id == job_id)
            .distinct()
        ]
        for message_id in message_ids:
            message = (
                db.query(ApiMessage)
                .filter(ApiMessage.org_id == org_id, ApiMessage.id == message_id)
                .with_for_update()
                .one()
            )
            db.refresh(job)
            for recipient in db.query(ApiMessageRecipient).filter(
                ApiMessageRecipient.org_id == org_id,
                ApiMessageRecipient.message_id == message_id,
                ApiMessageRecipient.dispatch_job_id == job_id,
            ):
                recipient.status = job.status if job.status in TERMINAL else "sending"
                recipient.provider = job.provider
                recipient.sent_at = job.accepted_at
                recipient.error_message = job.error
            db.flush()
            _finish(db, message)
            db.commit()


async def send(org_id, phone, text, *, job_key, ctx, preferred=None, recipient_id=None, timeout=15):  # noqa: ASYNC109
    jid = await asyncio.to_thread(ensure, org_id, job_key, phone, text, ctx.chain, preferred, recipient_id)
    deadline = asyncio.get_running_loop().time() + timeout
    projected_status = None
    while True:
        await drive(org_id, jid, ctx)
        state = await asyncio.to_thread(snapshot, org_id, jid)
        ctx.providers_used.update(state["providers_used"])
        if state["status"] != projected_status:
            await asyncio.to_thread(project, org_id, jid)
            projected_status = state["status"]
        if (
            state["status"] in TERMINAL
            or (state["status"] == "queued" and not state["error"])
            or asyncio.get_running_loop().time() >= deadline
        ):
            return state
        await asyncio.sleep(0.1)


async def run_once():
    await asyncio.to_thread(sweep)
    with database() as db:
        jobs = (
            db.query(Job.org_id, Job.id)
            .filter(Job.status.notin_(TERMINAL))
            .execution_options(include_all_tenants=True)
            .order_by(Job.next_attempt_at)
            .limit(100)
            .all()
        )
        projections = (
            db.query(Job.org_id, Job.id)
            .join(SmsLogRecipient, SmsLogRecipient.dispatch_job_id == Job.id)
            .filter(SmsLogRecipient.dispatch_status != Job.status)
            .execution_options(include_all_tenants=True)
            .limit(100)
            .all()
        )
        from app.models.api_message import ApiMessageRecipient

        projections += (
            db.query(Job.org_id, Job.id)
            .join(ApiMessageRecipient, ApiMessageRecipient.dispatch_job_id == Job.id)
            .filter(Job.status.in_(TERMINAL), ApiMessageRecipient.status != Job.status)
            .execution_options(include_all_tenants=True)
            .limit(100)
            .all()
        )
    await asyncio.gather(*(drive(org, jid) for org, jid in jobs))
    for org, jid in projections:
        await asyncio.to_thread(project, org, jid)


async def _loop():
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SMS-Outbox-Iteration fehlgeschlagen")
        await asyncio.sleep(1)


async def start():
    global _task
    if not settings.TESTING and (_task is None or _task.done()):
        _task = asyncio.create_task(_loop())


async def stop():
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
