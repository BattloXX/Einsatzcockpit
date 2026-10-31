"""Crash-sichere, persistente Alarmierung fuer Einsaetze."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.fehlerlog import log_fehler
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.services.background_runtime import create_leader_task
from app.services.db_transactions import commit_db_operation, retry_claim
from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.alarm_outbox")
TERMINAL = {"sent", "suppressed", "failed", "uncertain"}
CHANNELS = ("sms", "push", "teams", "mail")
_LEASE = timedelta(minutes=5)
_running_incident_tasks: set[asyncio.Task] = set()
_duplikat_audit_zeiten: dict[tuple[int, str], float] = {}
_DUPLIKAT_AUDIT_LIMIT = 1_000
_duplikat_audit_sperre = Lock()


def _protokolliere_doppelten_eingang(db: Session, einsatz: Incident, quelle: str) -> None:
    """Wiederholte Eingangsmeldungen ohne Personendaten gedrosselt protokollieren."""
    try:
        from app.core.audit import write_audit

        with _duplikat_audit_sperre:
            jetzt = time.monotonic()
            schluessel = (einsatz.id, quelle)
            zuletzt = _duplikat_audit_zeiten.get(schluessel)
            if zuletzt is not None and jetzt - zuletzt < 3600:
                return
            if len(_duplikat_audit_zeiten) >= _DUPLIKAT_AUDIT_LIMIT:
                del _duplikat_audit_zeiten[next(iter(_duplikat_audit_zeiten))]
            _duplikat_audit_zeiten[schluessel] = jetzt
        # Die Prozesssperre niemals waehrend eines Datenbankzugriffs halten.
        with db.begin_nested():
            write_audit(
                db, "incident.alarm_duplicate_ingest", org_id=einsatz.primary_org_id,
                incident_id=einsatz.id, payload={"source": quelle},
            )
            db.flush()
    except Exception as exc:
        logger.warning("Duplikat-Audit fehlgeschlagen (Quelle %s, Fehler %s)", quelle, type(exc).__name__)


def _event_key(incident: Incident, org_id: int | None, channel: str, variant: str) -> str:
    """Stabiler Ereignisschluessel je Organisation, Einsatznummer und Versandtyp."""
    nummer = (incident.lis_operation_number or "").strip().lower() or f"inc{incident.id}"
    schluessel = f"{org_id or 0}:{nummer}:{channel}:{variant}"
    if len(schluessel) > 160:
        nummer = hashlib.sha1(nummer.encode()).hexdigest()
        schluessel = f"{org_id or 0}:{nummer}:{channel}:{variant}"
    return schluessel


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _address(incident: Incident) -> str:
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


def teams_manuell_erneut(db: Session, incident_id: int, org_id: int, user_id: int) -> str:
    """Fordert den Erstalarm einmalig neu an; der Aufrufer uebergibt eine saubere Session."""
    from app.core.audit import write_audit

    def anfordern() -> str:
        # Nach einem Rollback auch bereits geladene Objekte neu aus der DB lesen.
        job = db.query(IncidentAlarmJob).filter(
            IncidentAlarmJob.incident_id == incident_id, IncidentAlarmJob.org_id == org_id,
            IncidentAlarmJob.channel == "teams", IncidentAlarmJob.variant == "",
        ).populate_existing().first()
        if job is None:
            return "nicht_gefunden"
        if job.status not in ("uncertain", "failed"):
            return "nicht_noetig"
        kontext = dict(job.context or {})
        kontext["manual_resend"] = True
        ergebnis = db.execute(update(IncidentAlarmJob).where(
            IncidentAlarmJob.id == job.id, IncidentAlarmJob.org_id == org_id,
            IncidentAlarmJob.channel == "teams", IncidentAlarmJob.variant == "",
            IncidentAlarmJob.status.in_(("uncertain", "failed")),
        ).values(status="pending", next_attempt_at=_now(), finished_at=None,
                 lease_until=None, last_error=None, context=kontext))
        if getattr(ergebnis, "rowcount", 0) != 1:
            return "nicht_noetig"
        db.add(IncidentLog(
            incident_id=incident_id, author_name="System", level="info",
            text="Teams-Alarmkarte manuell erneut angefordert",
        ))
        write_audit(
            db, "incident.alarm_teams_resend", org_id=org_id, user_id=user_id,
            incident_id=incident_id, payload={"job_id": job.id, "manual_resend": True},
        )
        return "ok"

    return commit_db_operation(db, anfordern, label="teams_manuell_erneut")


def enqueue_incident_alarm(
    db: Session, incident: Incident, *, org_id: int | None, source: str | None,
    triggered_by_user_id: int | None = None, base_url: str | None = None, push_url: str | None = None,
) -> None:
    """Fuegt die Versandauftraege hinzu; der Aufrufer bestimmt den Commit."""
    from app.services.incident_notify import alarm_unterdrueckung_grund

    existing = {
        channel for (channel,) in db.query(IncidentAlarmJob.channel).filter(
            IncidentAlarmJob.incident_id == incident.id, IncidentAlarmJob.variant == ""
        ).all()
    }
    # SessionLocal laeuft mit autoflush=False: noch nicht geflushte Jobs aus einem
    # frueheren Aufruf in derselben Transaktion sieht die Query oben nicht. Ohne
    # diese Pruefung legte die LIS-Neuanlage (lis_sync: enqueue + notify_incident_created)
    # jeden Kanal doppelt an, der Commit scheiterte an uq_incident_alarm_job_incident_channel
    # und der neue Einsatz wurde bei jedem Poll zurueckgerollt (Vorfall 2026-10-06).
    existing |= {
        obj.channel for obj in db.new
        if isinstance(obj, IncidentAlarmJob) and obj.incident_id == incident.id and not obj.variant
    }
    grund = alarm_unterdrueckung_grund(db, incident, org_id)
    suppressed = grund is not None
    now = _now()
    context = {
        "source": source,
        "triggered_by_user_id": triggered_by_user_id,
        "base_url": base_url or settings.effective_public_base_url,
        "push_url": push_url or f"/einsatz/{incident.id}",
        "suppressed_reason": grund,
    }
    for channel in CHANNELS:
        if channel not in existing:
            try:
                with db.begin_nested():
                    db.add(IncidentAlarmJob(
                        org_id=org_id, incident_id=incident.id, channel=channel, variant="",
                        event_key=_event_key(incident, org_id, channel, ""),
                        status="suppressed" if suppressed else "pending", next_attempt_at=now,
                        finished_at=now if suppressed else None, context=context,
                    ))
                    db.flush()
            except IntegrityError:
                logger.info("Alarmauftrag bereits vorhanden (Einsatz %s, Kanal %s)", incident.id, channel)
    if suppressed and not existing:
        db.add(IncidentLog(
            incident_id=incident.id, author_name="System", level="info",
            text=f"Keine Alarmierung (SMS/Push/Teams/Mail): {grund}",
        ))


def enqueue_nachalarmierung(
    db: Session, incident: Incident, *, org_id: int | None, key: str, codes: list[str],
    source: str | None, base_url: str | None, push_url: str | None,
) -> bool:
    """Nachalarm in der aufrufenden Transaktion; True bei neuen Versandauftraegen."""
    from app.services.incident_notify import alarm_unterdrueckung_grund

    # Serialisiert auch unterschiedliche, ueberlappende Code-Saetze unter MariaDB.
    db.query(Incident.id).filter(
        Incident.id == incident.id, Incident.primary_org_id == org_id,
    ).with_for_update().one()
    jobs = db.query(IncidentAlarmJob).filter(
        IncidentAlarmJob.incident_id == incident.id, IncidentAlarmJob.org_id == org_id,
        IncidentAlarmJob.variant != "",
    ).with_for_update().all()
    jobs += [obj for obj in db.new if isinstance(obj, IncidentAlarmJob)
             and obj.incident_id == incident.id and obj.org_id == org_id and obj.variant]
    # Exakte Tokens statt Substring: r2 darf r20 nicht sperren. Stichwort und
    # Zusatzcode besitzen getrennte Namensraeume.
    namespace = key.split(":", 1)[0]
    seen = {code for job in jobs if job.variant.startswith(f"nach:{namespace}:")
            for code in (job.context or {}).get("codes", [])}
    codes = sorted(set(codes) - seen)
    if not codes:
        return False
    variant = f"nach:{namespace}:{'+'.join(codes)}"
    if len(variant) > 40:
        # Spalte variant ist String(40): lange Code-Saetze per Hash kuerzen. Die
        # Idempotenz je Code laeuft ueber context["codes"], nicht ueber die Variante.
        variant = f"nach:{namespace}:h{hashlib.sha1('+'.join(codes).encode()).hexdigest()[:16]}"
    grund = alarm_unterdrueckung_grund(db, incident, org_id)
    now = _now()
    context = {
        "source": source, "base_url": base_url or settings.effective_public_base_url,
        "push_url": push_url or f"/einsatz/{incident.id}", "suppressed_reason": grund,
        "nachalarmierung": True, "codes": codes,
    }
    created = False
    for channel in CHANNELS:
        if any(job.channel == channel and job.variant == variant for job in jobs):
            continue
        try:
            with db.begin_nested():
                db.add(IncidentAlarmJob(
                    org_id=org_id, incident_id=incident.id, channel=channel, variant=variant,
                    event_key=_event_key(incident, org_id, channel, variant),
                    status="suppressed" if grund is not None else "pending", next_attempt_at=now,
                    finished_at=now if grund is not None else None, context=context,
                ))
                db.flush()
            created = True
        except IntegrityError:
            # Nur den konkurrierenden Insert verwerfen, nie Ingest/Merge/Commit.
            continue
    return created


def nachalarm_praefix(job: IncidentAlarmJob) -> str:
    if not job.context.get("nachalarmierung"):
        return ""
    return "NACHALARMIERUNG " + ", ".join(job.context.get("codes", [])) + " "


def _claim(db: Session, job: IncidentAlarmJob, now: datetime) -> bool:
    # Der Claim besitzt bereits seine Commits; keine Versandwirkung wiederholen.
    job_id, org_id, channel = job.id, job.org_id, job.channel
    return retry_claim(db, lambda: _claim_once(db, job_id, org_id, channel, now))


def _claim_once(db: Session, job_id: int, org_id: int | None, channel: str, now: datetime) -> bool:
    due = and_(IncidentAlarmJob.status.in_(("pending", "retry")), IncidentAlarmJob.next_attempt_at <= now)
    stale = and_(IncidentAlarmJob.status == "sending", IncidentAlarmJob.lease_until < now)
    # Nur SMS besitzen stabile, dauerhaft deduplizierte Unterauftraege. Andere
    # Kanaele nach moeglichem externem Versand niemals an einen zweiten Worker geben.
    if channel != "sms":
        db.execute(update(IncidentAlarmJob).where(
            IncidentAlarmJob.id == job_id, IncidentAlarmJob.org_id == org_id, stale,
        ).values(status="uncertain", lease_until=None, finished_at=now,
                 last_error="Worker-Kontakt nach Versandbeginn verloren; Ergebnis pruefen"))
        db.commit()
        eligible = due
    else:
        eligible = or_(due, stale)
    versandbeginn = {"send_started_at": now, "may_have_sent": True} if channel in {"teams", "mail"} else {}
    result = db.execute(
        update(IncidentAlarmJob)
        .where(IncidentAlarmJob.id == job_id, IncidentAlarmJob.org_id == org_id, eligible)
        .values(status="sending", lease_until=now + _LEASE,
                attempt_count=IncidentAlarmJob.attempt_count + 1, **versandbeginn)
    )
    db.commit()
    return bool(getattr(result, "rowcount", 0) == 1)


async def _send_sms(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.exercise_guard import darf_extern
    from app.services.sms_dispatch_service import dispatch_einsatzinfo
    org_id = job.org_id
    if not org_id:
        return "suppressed", "keine Organisation zugeordnet"
    if not darf_extern(
        "sms", is_exercise=incident.is_exercise, org_id=org_id, db=db,
        planspiel_run_id=incident.planspiel_run_id,
    ):
        logger.info("Einsatzinfo-SMS uebersprungen: Uebung unterdrueckt (Einsatz %s)", incident.id)
        return "suppressed", "Uebung unterdrueckt"
    address = _address(incident)
    info_link = (
        f"{settings.effective_public_base_url.rstrip('/')}/alarm/{incident.alarm_token}"
        if incident.alarm_token else ""
    )
    result = await dispatch_einsatzinfo(
        org_id, incident.alarm_type_code, address, incident.address_city, incident.report_text,
        incident.reason, incident.is_exercise, job.context.get("triggered_by_user_id"), info_link,
        incident.lis_operation_number, incident.id,
        planspiel_run_id=incident.planspiel_run_id,
        **({"variant": job.variant, "text_prefix": nachalarm_praefix(job)} if job.variant else {}),
    )
    if result is None:
        return "uncertain", "keine Versandbestaetigung"
    if result.status in ("pending", "uncertain", "failed"):
        return "retry" if result.status == "pending" else result.status, result.reason
    if result.sent:
        if result.reason:
            return "sent", result.reason
        return "sent", f"gesendet an {result.recipient_count}"
    return ("retry" if result.handed_off else "suppressed"), result.reason


async def _send_push(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.exercise_guard import darf_extern
    from app.services.incident_notify import _send_incident_push, _send_incident_wake_only
    org_id = job.org_id
    address = _address(incident)
    title = f"{'[ÜBUNG] ' if incident.is_exercise else ''}🚒 Einsatz: {incident.alarm_type_code}"
    title = nachalarm_praefix(job) + title
    body = address or incident.report_text or "Kein Ort angegeben"
    url = job.context.get("push_url") or f"/einsatz/{incident.id}"
    user_id = job.context.get("triggered_by_user_id")
    if not darf_extern(
        "push", is_exercise=incident.is_exercise, org_id=org_id, db=db,
        planspiel_run_id=incident.planspiel_run_id,
    ):
        if incident.is_exercise:
            await _send_incident_wake_only(incident.id, org_id, title, body, url, user_id)
        return "suppressed", "Uebung unterdrueckt"
    live_extra = None
    try:
        from app.services.einsatz_live_service import build_incident_live_payload
        from app.services.incident_live_notify import _live_extra
        live_extra = _live_extra(build_incident_live_payload(db, incident), alert=True, kind="einsatz_live")
    except Exception:
        logger.exception("Live-Push-Payload fehlgeschlagen (Einsatz %s)", incident.id)
    await _send_incident_push(
        incident.id, org_id, title, body, url, live_extra, user_id,
        fcm_data={"incident_id": str(incident.id), "is_exercise": "1" if incident.is_exercise else "0"},
    )
    return "sent", None


async def _send_teams(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.exercise_guard import darf_extern
    from app.services.teams_alarm_service import post_incident_card
    if not darf_extern(
        "teams", is_exercise=incident.is_exercise, org_id=job.org_id, db=db,
        planspiel_run_id=incident.planspiel_run_id,
    ):
        logger.info("Teams-Alarmierung uebersprungen: Uebung unterdrueckt (Einsatz %s)", incident.id)
        return "suppressed", "Uebung unterdrueckt"
    base_url = job.context.get("base_url")
    if not base_url:
        return "suppressed", "keine Base-URL verfuegbar"
    result = await post_incident_card(
        db, incident, base_url=base_url,
        **({"text_prefix": nachalarm_praefix(job)} if job.variant else {}),
    )
    if result is not None and result.uncertain:
        return "uncertain", result.reason or "Zustellung unklar"
    if result is None or result.sent:
        return "sent", None
    if result.retryable:
        raise RuntimeError(result.reason)
    if result.reason and result.reason.startswith("HTTP "):
        return "failed", result.reason
    return "suppressed", result.reason


async def _send_mail(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.einsatzinfo_mail_service import dispatch_einsatzinfo_mail

    if not job.org_id:
        return "suppressed", "keine Organisation zugeordnet"
    result = await dispatch_einsatzinfo_mail(
        job.org_id, incident.id, job.context.get("triggered_by_user_id"),
        **({"variant": job.variant, "text_prefix": nachalarm_praefix(job)} if job.variant else {}),
    )
    if result.sent:
        return "sent", result.reason or f"gesendet an {result.recipient_count}"
    return "suppressed", result.reason


def _retry_at(channel: str, attempt: int, now: datetime) -> datetime | None:
    delays = {
        "teams": (5, 20, 60, 300, 900), "push": (10, 60), "sms": (10, 60),
        "mail": (10, 60, 300),
    }[channel]
    return now + timedelta(seconds=delays[attempt - 1]) if attempt <= len(delays) else None


async def _run_claimed(job_id: int, generation: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = db.get(IncidentAlarmJob, job_id)
        if job is None or job.status != "sending" or job.attempt_count != generation:
            return
        incident = db.get(Incident, job.incident_id)
        if incident is None:
            return
        try:
            sender = {
                "sms": _send_sms, "push": _send_push, "teams": _send_teams,
                "mail": _send_mail,
            }[job.channel]
            outcome, reason = await sender(db, incident, job)
        except Exception as exc:
            log_fehler(
                logger, "Alarmkanal fehlgeschlagen", exc, level=logging.ERROR,
                job_id=job.id, incident_id=incident.id, org_id=job.org_id,
                channel=job.channel, versuch=job.attempt_count,
            )
            now = _now()
            retry_at = _retry_at(job.channel, job.attempt_count, now)
            job.status = "retry" if retry_at else "failed"
            job.next_attempt_at = retry_at or now
            job.finished_at = now if retry_at is None else None
            # SQL-Fehler koennen SQL und Parameter enthalten; nur andere Meldungen
            # fuer die Admin-Diagnose speichern, niemals im Anwendungslog ausgeben.
            message = "Datenbankfehler (Details unterdrueckt)" if isinstance(exc, StatementError) else str(exc)
            job.last_error = f"{type(exc).__name__}: {' '.join(message.split())[:200]}"
        else:
            job.status = outcome
            job.last_error = reason
            job.finished_at = _now() if outcome in TERMINAL else None
            if outcome == "retry":
                # Nur den persistenten Status abgleichen, keine neue SMS erzeugen.
                job.next_attempt_at = _now() + timedelta(seconds=10)
        job.lease_until = None
        # Ein alter Worker darf nach Lease-Recovery keine neuere Generation oder
        # ein terminales uncertain ueberschreiben. Die Zeilensperre bleibt bis Commit.
        result = db.execute(update(IncidentAlarmJob).where(
            IncidentAlarmJob.id == job_id, IncidentAlarmJob.org_id == job.org_id,
            IncidentAlarmJob.status == "sending", IncidentAlarmJob.attempt_count == generation,
        ).values(status=job.status, lease_until=None, last_error=job.last_error,
                 next_attempt_at=job.next_attempt_at, finished_at=job.finished_at))
        if getattr(result, "rowcount", 0) != 1:
            db.rollback()
            return
        if job.channel == "sms" and job.status in TERMINAL:
            status_text = {"suppressed": "uebersprungen", "failed": "fehlgeschlagen", "uncertain": "unklar"}
            sms_text = "Alarmierung SMS: gesendet"
            if job.status == "sent" and job.last_error:
                sms_text += f" ({job.last_error})"
            db.add(IncidentLog(
                incident_id=incident.id, author_name="System",
                level="info" if job.status == "sent" else "warning",
                text=sms_text if job.status == "sent"
                else f"Alarmierung SMS {status_text[job.status]}: {job.last_error or 'keine Annahmebestaetigung'}",
            ))
        if job.channel == "teams":
            db.add(IncidentLog(
                incident_id=incident.id, author_name="System",
                level="info" if job.status == "sent" else "warning",
                text="Alarmierung Teams: gesendet" if job.status == "sent"
                else f"Alarmierung Teams: Zustellung unklar, nicht automatisch wiederholt: {job.last_error}"
                if job.status == "uncertain"
                else f"Alarmierung Teams uebersprungen: {job.last_error}",
            ))
        if job.channel == "mail" and job.status in TERMINAL:
            mail_text = "Alarmierung Mail: gesendet"
            if job.status == "sent" and job.last_error:
                mail_text += f" ({job.last_error})"
            db.add(IncidentLog(
                incident_id=incident.id, author_name="System",
                level="info" if job.status == "sent" else "warning",
                text=mail_text if job.status == "sent"
                else f"Alarmierung Mail fehlgeschlagen: {job.last_error}" if job.status == "failed"
                else f"Alarmierung Mail uebersprungen: {job.last_error}",
            ))
        db.commit()
        _finish_if_done(db, incident, job.org_id, job.context.get("triggered_by_user_id"))
    except Exception as exc:
        db.rollback()
        log_fehler(logger, "Alarm-Outbox fehlgeschlagen", exc, level=logging.ERROR, job_id=job_id)
    finally:
        db.close()


def _finish_if_done(db: Session, incident: Incident, org_id: int | None, user_id: int | None) -> None:
    jobs = db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident.id).all()
    if not jobs or any(job.status not in TERMINAL for job in jobs):
        return
    from app.core.audit import write_audit
    from app.models.user import AuditLog
    finished = db.query(AuditLog.id).filter(
        AuditLog.action == "incident.alarm_finished", AuditLog.incident_id == incident.id,
    ).first()
    if finished:
        return
    payload = {job.channel: {"status": job.status, "reason": job.last_error} for job in jobs}
    write_audit(db, "incident.alarm_finished", org_id=org_id, user_id=user_id, incident_id=incident.id, payload=payload)
    # Bereits beim Anlegen unterdrueckte Alarme haben ihren gemeinsamen Logeintrag.
    if not all(job.status == "suppressed" and job.context.get("suppressed_reason") for job in jobs):
        db.add(IncidentLog(
            incident_id=incident.id, author_name="System", level="info", text="Alarmierung abgeschlossen",
        ))
    db.commit()
    logger.info("Alarmierung abgeschlossen (Einsatz %s, Kanaele=%s)", incident.id, len(jobs))


async def process_incident_alarm(incident_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        now = _now()
        jobs = db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident_id).all()
        claimed = [(job.id, job.attempt_count) for job in jobs if _claim(db, job, now)]
    finally:
        db.close()
    await asyncio.gather(*(_run_claimed(job_id, generation) for job_id, generation in claimed))
    # Eine reine Recovery zu uncertain startet keinen Sender, muss den Alarm aber
    # ebenfalls abschliessen koennen.
    if not claimed:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            incident = db.get(Incident, incident_id)
            if incident is not None:
                _finish_if_done(db, incident, incident.primary_org_id, None)


async def run_alarm_outbox_once() -> list[asyncio.Task]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        now = _now()
        ids = [row[0] for row in db.query(IncidentAlarmJob.incident_id).filter(or_(
            and_(IncidentAlarmJob.status.in_(("pending", "retry")), IncidentAlarmJob.next_attempt_at <= now),
            and_(IncidentAlarmJob.status == "sending", IncidentAlarmJob.lease_until < now),
        )).distinct().all()]
    finally:
        db.close()
    running_ids = {task.get_name() for task in _running_incident_tasks}
    started = []
    for incident_id in ids:
        if str(incident_id) in running_ids:
            continue
        task = create_leader_task(process_incident_alarm(incident_id), name=str(incident_id))
        _running_incident_tasks.add(task)
        task.add_done_callback(_running_incident_tasks.discard)
        started.append(task)
    return started


async def alarm_outbox_loop() -> None:
    try:
        while True:
            try:
                with iteration_watch(logger, "alarm_outbox_loop", 5):
                    await run_alarm_outbox_once()
            except Exception as exc:
                logger.error("Alarm-Outbox-Iteration fehlgeschlagen (error=%s)", type(exc).__name__)
            await asyncio.sleep(5)
    finally:
        # Diese Kinder sind keine Root-Loops. Ohne Drain koennten sie nach einer
        # Lock-Freigabe parallel zum neuen Leader weitersenden.
        pending = list(_running_incident_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
