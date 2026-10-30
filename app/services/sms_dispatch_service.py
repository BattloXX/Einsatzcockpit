"""SMS-Einsatzinfo-Dienst: automatischer Versand bei Alarmeingang.

Baut auf sms_service.send_sms (Gateway-WebSocket) auf.
Wird als BackgroundTask nach dem Einsatz-Commit aufgerufen.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import or_

from app.config import settings

# Sicher auf Modul-Ebene importierbar (keine Kreisabhaengigkeiten)
from app.core.audit import write_audit
from app.core.telefon import telefon_kompakt
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.sms import SmsEinsatzinfoRecipient, SmsLog, SmsLogRecipient
from app.services.db_transactions import current_read_session

# app.routers.ws muss lazy importiert bleiben (Kreisabhaengigkeit)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.master import Member

logger = logging.getLogger("einsatzleiter.sms_dispatch")

# Pro Gateway duerfen nur wenige Jobs gleichzeitig auf das gekoppelte Handy treffen.
SMS_CONCURRENCY_PER_GATEWAY = 7
EINSATZINFO_NACHVERSAND_FENSTER = timedelta(minutes=60)
_einsatzinfo_nachversand_locks: dict[int, asyncio.Lock] = {}


def _einsatzinfo_lease_until(now: datetime, recipient_count: int) -> datetime:
    return now + timedelta(minutes=3, seconds=20 * recipient_count)


# Standardvorlage fuer Einsatzinfo-SMS (Platzhalter in geschweiften Klammern)
_DEFAULT_TEMPLATE = "Einsatz {stichwort}: {adresse}. {meldung} {link}"

# Standardtext fuer den Grossschadenslage-Sonderalarm (siehe dispatch_gsl_alarm()).
# Bewusst ohne Umlaute (SMS-Zeichensatz, wie gsl_verleih_sms_*_text in verleih_service.py).
_DEFAULT_GSL_ALARM_TEXT = "Grossschadenslage! Alle Mitglieder ruecken sofort ins Geraetehaus ein."


@dataclass(frozen=True)
class EinsatzinfoDispatchResult:
    sent: bool
    reason: str | None = None
    recipient_count: int = 0
    handed_off: bool = False
    status: str | None = None


def default_einsatzinfo_template() -> str:
    """Gibt die systemweite Standard-Vorlage fuer Einsatzinfo-SMS zurueck."""
    return _DEFAULT_TEMPLATE


def render_template(template: str, ctx: dict) -> str:
    """Ersetzt Platzhalter in der Vorlage.

    Unbekannte oder fehlende Keys werden durch einen leeren String ersetzt
    (tolerantes Format, kein KeyError).

    Unterstuetzte Platzhalter:
      {stichwort}   Alarmtyp-Code (z.B. B2, T1)
      {adresse}     Strasse + Ort zusammengesetzt
      {ort}         Nur der Ort
      {meldung}     Meldungstext
      {einsatzgrund} Einsatzgrund
      {datum}       Datum der Alarmierung (TT.MM.JJJJ)
      {zeit}        Uhrzeit der Alarmierung (HH:MM)
      {link}        Öffentliche Einsatzinformation (No-Login-Link /alarm/{token})
      {anmeldelink}  Gast-Anmeldung mit PIN (nur mit Einsatz-PIN und Alarm-Token)
      {leitstellennummer} Leitstellen-Einsatznummer (falls vorhanden)
    """

    class _Safe(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_Safe(ctx))


def collect_einsatzinfo_recipients(
    db: Session,
    org_id: int,
    alarm_type_id: int | None,
) -> dict[str, Member]:
    """Sammelt alle Empfaenger fuer die Einsatzinfo-SMS.

    Gibt ein Dict {normalisierte_telefonnummer: Member} zurueck.
    Dedupliziert ueber die Telefonnummer.
    Filtert:
      - inaktive Mitglieder
      - Mitglieder ohne Telefonnummer

    Einbezogen werden:
      - Basis-Verteiler (alarm_type_id IS NULL)
      - Stichwort-spezifischer Verteiler (alarm_type_id == uebergebene ID)
    """

    result: dict[str, Member] = {}

    # Alle Empfaenger-Eintraege fuer diese Org laden:
    # Basis-Verteiler (alarm_type_id IS NULL) + Stichwort-Verteiler
    q = db.query(SmsEinsatzinfoRecipient).filter(
        SmsEinsatzinfoRecipient.org_id == org_id,
        or_(
            SmsEinsatzinfoRecipient.alarm_type_id.is_(None),
            SmsEinsatzinfoRecipient.alarm_type_id == alarm_type_id,
        ),
    )
    for entry in q.all():
        members = []
        if entry.group_id and entry.group:
            # Gruppe: alle Mitglieder expandieren
            for gm in entry.group.members:
                if gm.member:
                    members.append(gm.member)
        elif entry.member_id and entry.member:
            members.append(entry.member)

        for m in members:
            if not m.active:
                continue
            phone = (m.phone or "").strip()
            if not phone:
                continue
            norm = telefon_kompakt(phone)
            if norm and norm not in result:
                result[norm] = m

    return result


@dataclass(frozen=True)
class SmsSendResult:
    phone_number: str
    success: bool
    sent_at: datetime
    provider: str | None = None
    gateway_label: str | None = None
    job_id: str | None = None
    status: str | None = None


async def send_bulk_detailed(
    org_id: int,
    jobs: Sequence[tuple[str, str] | tuple[str, str, str]],
    ctx=None,
    on_result: Callable[[SmsSendResult], Awaitable[None]] | None = None,
) -> list[SmsSendResult]:
    """Sendet SMS begrenzt parallel und verteilt Gateway-Jobs round-robin."""
    from app.services.sms_service import resolve_sms_config, send_sms

    ctx = ctx or resolve_sms_config(org_id)
    from app.routers.ws import connected_gateway_token_ids_ordered

    gateway_ids = connected_gateway_token_ids_ordered(org_id) if "gateway" in ctx.chain else []
    gateway_labels: dict[int, str] = {}
    if gateway_ids:
        from app.models.user import SmsGatewayToken

        db = current_read_session(SessionLocal)
        set_tenant_context(db, None)
        try:
            gateway_rows = (
                db.query(SmsGatewayToken.id, SmsGatewayToken.label)
                .filter(
                    SmsGatewayToken.org_id == org_id,
                    SmsGatewayToken.id.in_(gateway_ids),
                )
                .all()
            )
            gateway_labels = {row[0]: row[1] for row in gateway_rows}
        finally:
            db.close()
    semaphore_keys: list[int | None] = list(gateway_ids) if gateway_ids else [None]
    semaphores = {
        key: asyncio.Semaphore(settings.EUS_SMS_CONCURRENCY if key is None else SMS_CONCURRENCY_PER_GATEWAY)
        for key in semaphore_keys
    }
    progress_lock = asyncio.Lock()

    async def _send(index: int, job) -> SmsSendResult:
        to, text = job[:2]
        job_id = job[2] if len(job) > 2 else None
        gateway_id = gateway_ids[index % len(gateway_ids)] if gateway_ids else None
        async with semaphores[gateway_id]:
            delivery = None
            try:
                if gateway_id is None:
                    kwargs = {"ctx": ctx}
                    if job_id is not None:
                        kwargs["job_id"] = job_id
                    if len(job) > 3:
                        kwargs["recipient_id"] = job[3]
                    delivery = await send_sms(org_id, to, text, **kwargs)
                else:
                    kwargs = {"ctx": ctx, "preferred_gateway_token_id": gateway_id}
                    if job_id is not None:
                        kwargs["job_id"] = job_id
                    if len(job) > 3:
                        kwargs["recipient_id"] = job[3]
                    delivery = await send_sms(org_id, to, text, **kwargs)
            except Exception as exc:
                logger.warning("SMS-Versand fehlgeschlagen an %s: %s", to[-4:] + "****", exc)
            gateway_token_id = getattr(delivery, "gateway_token_id", None)
            result = SmsSendResult(
                phone_number=to,
                success=bool(delivery),
                sent_at=datetime.now(UTC).replace(tzinfo=None),
                provider=getattr(delivery, "provider", None),
                job_id=getattr(delivery, "job_id", None),
                status=getattr(delivery, "status", None),
                gateway_label=(gateway_labels.get(gateway_token_id) if isinstance(gateway_token_id, int) else None),
            )
            if on_result is not None:
                async with progress_lock:
                    await on_result(result)
            return result

    return list(await asyncio.gather(*(_send(index, job) for index, job in enumerate(jobs))))


async def send_bulk(org_id: int, jobs: list[tuple[str, str]], ctx=None) -> tuple[int, int]:
    """Sendet SMS an mehrere Empfaenger sequentiell.

    jobs: Liste von (telefonnummer, text)-Tupeln.
    Rueckgabe: (anzahl_gesamt, anzahl_erfolgreich).
    """
    results = await send_bulk_detailed(org_id, jobs, ctx=ctx)
    return len(results), sum(result.success for result in results)


async def _send_bulk_with_progress(org_id: int, jobs, ctx, on_result):
    """Kompatibilitaet fuer bestehende Provider-Doubles ohne Progress-Callback."""
    try:
        return await send_bulk_detailed(org_id, jobs, ctx=ctx, on_result=on_result)
    except TypeError as exc:
        if "on_result" not in str(exc):
            raise
        results = await send_bulk_detailed(org_id, jobs, ctx=ctx)
        for result in results:
            await on_result(result)
        return results


def _nachversand_lock(org_id: int) -> asyncio.Lock:
    """Serieller Nachversand je Organisation innerhalb dieses App-Workers."""
    return _einsatzinfo_nachversand_locks.setdefault(org_id, asyncio.Lock())


def _markiere_abgelaufene_einsatzinfo(db: Session, org_id: int, now: datetime) -> int:
    """Verwirft offene Alarm-SMS nach dem garantierten 60-Minuten-Fenster."""
    from app.models.incident import IncidentLog

    pending = (
        db.query(SmsLogRecipient)
        .join(SmsLog)
        .filter(
            SmsLog.org_id == org_id,
            SmsLog.source == "alarm",
            SmsLog.sent_at <= now - EINSATZINFO_NACHVERSAND_FENSTER,
            SmsLogRecipient.success.is_(False),
            SmsLogRecipient.dispatch_job_id.is_(None),
            SmsLogRecipient.provider.in_(("ausstehend", "sendet")),
        )
        .all()
    )
    by_log: dict[int, list[SmsLogRecipient]] = {}
    for recipient in pending:
        by_log.setdefault(recipient.sms_log_id, []).append(recipient)
    for log_id, recipients in by_log.items():
        log_entry = db.get(SmsLog, log_id)
        if log_entry is None:
            continue
        for recipient in recipients:
            recipient.provider = "verworfen"
        log_entry.completed_at = now
        logger.warning(
            "Einsatzinfo-SMS nach 60 min verworfen (log_id=%s, org_id=%s, empfaenger=%s)",
            log_entry.id,
            org_id,
            len(recipients),
        )
        if log_entry.incident_id is not None:
            db.add(
                IncidentLog(
                    incident_id=log_entry.incident_id,
                    author_name="System",
                    level="warning",
                    text=f"Alarmierung SMS: {len(recipients)} Empfaenger nach 60 min verworfen (kein Gateway)",
                )
            )
    return len(pending)


def persistent_log_result(org_id: int, log_id: int) -> EinsatzinfoDispatchResult:
    db = current_read_session(SessionLocal)
    set_tenant_context(db, None)
    try:
        db.query(SmsLog).filter(SmsLog.org_id == org_id, SmsLog.id == log_id).one()
        rows = db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log_id).all()
        from app.models.sms import SmsDispatchJob

        jobs = dict(
            db.query(SmsDispatchJob.id, SmsDispatchJob.status)
            .filter(
                SmsDispatchJob.org_id == org_id,
                SmsDispatchJob.id.in_([r.dispatch_job_id for r in rows if r.dispatch_job_id]),
            )
            .all()
        )
        states = [
            jobs.get(r.dispatch_job_id or "")
            or (
                "sent"
                if r.success
                else "uncertain"
                if r.provider == "uncertain"
                else "queued"
                if r.provider in ("ausstehend", "sendet")
                else "failed"
            )
            for r in rows
        ]
        pending = any(state not in ("sent", "failed", "uncertain") for state in states)
        unknown = "uncertain" in states
        status = (
            "pending"
            if pending
            else "uncertain"
            if unknown
            else "sent"
            if states and all(state == "sent" for state in states)
            else "failed"
        )
        return EinsatzinfoDispatchResult(
            status == "sent",
            "wartet auf Versandbestaetigung" if pending else "Versandergebnis unklar" if unknown else None,
            states.count("sent"),
            handed_off=True,
            status=status,
        )
    finally:
        db.close()


async def _project_result(db, org_id: int, result: SmsSendResult) -> bool:
    if not result.job_id:
        return False
    from app.services import sms_outbox

    await asyncio.to_thread(sms_outbox.project, org_id, result.job_id)
    db.expire_all()
    return True


async def retry_pending_einsatzinfo(org_id: int) -> int:
    from app.services import sms_outbox
    from app.services.sms_service import resolve_sms_config

    async with _nachversand_lock(org_id):
        db = current_read_session(SessionLocal)
        set_tenant_context(db, None)
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            ctx = resolve_sms_config(org_id, db)
            # Auch alte 'sendet'-Eintraege ausserhalb des Nachversandfensters sind
            # unklar, nicht nachweislich ungesendet. Vor dem Verwerfen migrieren.
            legacy_logs = db.query(SmsLog).join(SmsLogRecipient).filter(
                SmsLog.org_id == org_id, SmsLog.source == "alarm", SmsLog.completed_at.is_(None),
                SmsLogRecipient.provider == "sendet", SmsLogRecipient.dispatch_job_id.is_(None),
            ).distinct().all()
            for log in legacy_logs:
                rows = db.query(SmsLogRecipient).filter(
                    SmsLogRecipient.sms_log_id == log.id, SmsLogRecipient.provider == "sendet",
                    SmsLogRecipient.dispatch_job_id.is_(None),
                ).all()
                sms_outbox.queue_log(db, log, {r.phone_number: (r.name, r.member_id) for r in rows}, ctx)
            db.flush()
            _markiere_abgelaufene_einsatzinfo(db, org_id, now)
            logs = (
                db.query(SmsLog)
                .filter(
                    SmsLog.org_id == org_id,
                    SmsLog.source == "alarm",
                    SmsLog.completed_at.is_(None),
                    SmsLog.sent_at > now - EINSATZINFO_NACHVERSAND_FENSTER,
                )
                .all()
            )
            for log in logs:
                rows = db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log.id).all()
                # Migration alter offener Logs: gleicher Job-Key und Inhalt bei jedem Nachversand.
                sms_outbox.queue_log(db, log, {r.phone_number: (r.name, r.member_id) for r in rows}, ctx)
            db.commit()
        finally:
            db.close()
        await sms_outbox.run_once()
        return 0  # Annahme durch die persistente Queue ist kein Versandnachweis.


async def einsatzinfo_nachversand_loop() -> None:
    """Prueft alle Organisationen alle 30 Sekunden, auch nach App-Neustarts."""
    from app.models.master import FireDept

    while True:
        db = current_read_session(SessionLocal)
        set_tenant_context(db, None)
        try:
            org_ids = [row[0] for row in db.query(FireDept.id).all()]
        finally:
            db.close()
        for org_id in org_ids:
            await retry_pending_einsatzinfo(org_id)
        await asyncio.sleep(30)


async def dispatch_manual_sms(
    org_id: int,
    log_id: int,
    text: str,
    recipients: Mapping[
        str,
        tuple[str | None, int | None] | tuple[str | None, int | None, str | None, int | None],
    ],
    triggered_by_user_id: int,
    target_type: str,
) -> None:
    """Fuehrt einen bereits protokollierten manuellen Versand im Hintergrund aus."""
    from app.services.sms_service import resolve_sms_config

    db = current_read_session(SessionLocal)
    set_tenant_context(db, None)
    try:
        log_entry = db.query(SmsLog).filter(SmsLog.id == log_id, SmsLog.org_id == org_id).first()
        if log_entry is None:
            logger.error("SMS-Log %s fuer Org %s nicht gefunden", log_id, org_id)
            return
        sms_ctx = resolve_sms_config(org_id, db)
        from app.services import sms_outbox

        jobs = sms_outbox.queue_log(db, log_entry, dict(recipients), sms_ctx)
        db.commit()
        by_phone = {r.phone_number: r for r in db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log_id)}

        async def _record(result: SmsSendResult) -> None:
            if await _project_result(db, org_id, result):
                return
            row = by_phone[result.phone_number]
            row.success, row.provider, row.gateway_label = result.success, result.provider, result.gateway_label
            row.sent_at = result.sent_at
            if result.success:
                log_entry.success_count += 1
            db.commit()

        results = await _send_bulk_with_progress(org_id, jobs, sms_ctx, _record)
        db.expire_all()
        log_entry.provider = ",".join(sorted(sms_ctx.providers_used)) or None
        if not any(r.job_id for r in results):
            log_entry.completed_at = datetime.now(UTC).replace(tzinfo=None)
        write_audit(
            db,
            "admin.sms.manual_send",
            org_id=org_id,
            user_id=triggered_by_user_id,
            payload={
                "recipient_count": len(results),
                "success_count": sum(result.success for result in results),
                "target_type": target_type,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Manueller SMS-Hintergrundversand fehlgeschlagen (log_id=%s)", log_id)
        failed_log = db.query(SmsLog).filter(SmsLog.id == log_id, SmsLog.org_id == org_id).first()
        if failed_log is not None:
            failed_log.completed_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()
    finally:
        db.close()


async def dispatch_einsatzinfo(
    org_id: int,
    alarm_type_code: str,
    address: str,
    ort: str | None,
    meldung: str | None,
    einsatzgrund: str | None,
    is_exercise: bool,
    triggered_by_user_id: int | None = None,
    link: str = "",
    leitstellennummer: str | None = None,
    incident_id: int | None = None,
    *,
    planspiel_run_id: int | None = None,
    variant: str = "",
    text_prefix: str = "",
) -> EinsatzinfoDispatchResult:
    """Versendet automatische Einsatzinfo-SMS nach Alarmeingang.

    Wird als BackgroundTask nach dem Einsatz-Commit aufgerufen.
    Oeffnet eine eigene DB-Session (unabhaengig vom Request-Lifecycle).

    Gates (kein Versand wenn):
      - einsatzinfo_sms_enabled == False
      - is_exercise == True UND einsatzinfo_sms_send_exercise == False
      - Kein SMS-Provider verfuegbar
      - Keine Empfaenger konfiguriert
    """
    from app.services.sms_service import resolve_sms_config, sms_available

    db = current_read_session(SessionLocal)
    set_tenant_context(db, None)  # system-level: alle Orgs sichtbar fuer Subqueries
    try:
        if incident_id is not None:
            from sqlalchemy import or_

            from app.models.incident import Incident, IncidentOrg

            allowed = (
                db.query(Incident)
                .filter(
                    Incident.id == incident_id,
                    or_(
                        Incident.primary_org_id == org_id, Incident.collaborating_orgs.any(IncidentOrg.org_id == org_id)
                    ),
                )
                .with_for_update()
                .first()
            )
            if allowed is None:
                return EinsatzinfoDispatchResult(False, "Einsatz gehoert nicht zur Organisation")
            existing = (
                db.query(SmsLog)
                .filter(SmsLog.org_id == org_id, SmsLog.incident_id == incident_id,
                        SmsLog.source == "alarm", SmsLog.variant == variant)
                .first()
            )
            if existing is not None:
                return persistent_log_result(org_id, existing.id)
        # Org-Einstellungen laden
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not org_settings or not org_settings.einsatzinfo_sms_enabled:
            reason = "deaktiviert"
            logger.info(
                "Einsatzinfo-SMS uebersprungen: %s (Einsatz %s, org_id=%d, stichwort=%s, is_exercise=%s)",
                reason,
                incident_id,
                org_id,
                alarm_type_code,
                is_exercise,
            )
            return EinsatzinfoDispatchResult(False, reason)

        from app.services.exercise_guard import darf_extern

        if not darf_extern(
            "sms",
            is_exercise=is_exercise,
            org_id=org_id,
            db=db,
            planspiel_run_id=planspiel_run_id,
        ):
            reason = "Uebung unterdrueckt"
            logger.info(
                "Einsatzinfo-SMS uebersprungen: %s (Einsatz %s, org_id=%d, stichwort=%s, is_exercise=%s)",
                reason,
                incident_id,
                org_id,
                alarm_type_code,
                is_exercise,
            )
            return EinsatzinfoDispatchResult(False, reason)

        # AlarmType fuer Stichwort-Override und ID-Lookup
        alarm_type = db.query(AlarmType).filter(AlarmType.org_id == org_id, AlarmType.code == alarm_type_code).first()
        alarm_type_id = alarm_type.id if alarm_type else None

        # Vorlage: Stichwort-Override > Org-Standard > systemweiter Default
        template = (
            (alarm_type.einsatzinfo_sms_template if alarm_type else None)
            or org_settings.einsatzinfo_sms_template
            or default_einsatzinfo_template()
        )

        # Zeitstempel
        now = datetime.now(UTC)
        # Lokale Zeit fuer Platzhalter (Europe/Vienna als Fallback)
        try:
            from zoneinfo import ZoneInfo

            dept = db.get(FireDept, org_id)
            tz_name = (dept.timezone if dept and dept.timezone else None) or "Europe/Vienna"
            local_now = now.astimezone(ZoneInfo(tz_name))
        except Exception:
            local_now = now

        anmeldelink = ""
        if incident_id is not None and allowed is not None and allowed.access_pin_hash and allowed.alarm_token:
            from app.config import settings

            anmeldelink = (
                f"{settings.effective_public_base_url.rstrip('/')}/alarm/{allowed.alarm_token}/anmelden"
            )

        # Platzhalter befuellen
        exercise_prefix = "[UEBUNG] " if is_exercise else ""
        ctx = {
            "stichwort": alarm_type_code,
            "adresse": address or "",
            "ort": ort or "",
            "meldung": meldung or "",
            "einsatzgrund": einsatzgrund or "",
            "datum": local_now.strftime("%d.%m.%Y"),
            "zeit": local_now.strftime("%H:%M"),
            "link": link or "",
            "anmeldelink": anmeldelink,
            "leitstellennummer": leitstellennummer or "",
        }
        # .strip(): entfernt trailing Space, falls {link} leer ist (Altdaten ohne AlarmToken)
        text = (text_prefix + exercise_prefix + render_template(template, ctx)).strip()

        # Empfaenger sammeln (inkl. Gruppen-Expansion, Dedup, Telefonnummer-Filter)
        recipients = collect_einsatzinfo_recipients(db, org_id, alarm_type_id)
        if not recipients:
            reason = "keine Empfaenger konfiguriert"
            log = logger.info if is_exercise else logger.warning
            log(
                "Einsatzinfo-SMS uebersprungen: %s (Einsatz %s, org_id=%d, stichwort=%s, is_exercise=%s)",
                reason,
                incident_id,
                org_id,
                alarm_type_code,
                is_exercise,
            )
            return EinsatzinfoDispatchResult(False, reason)

        # Der Outbox-Worker darf nach einem Crash keinen zweiten Alarm anlegen.
        if incident_id is not None:
            existing = (
                db.query(SmsLog)
                .filter(
                    SmsLog.org_id == org_id,
                    SmsLog.incident_id == incident_id,
                    SmsLog.source == "alarm", SmsLog.variant == variant,
                )
                .first()
            )
            if existing is not None:
                return persistent_log_result(org_id, existing.id)

        # Vor Versand protokollieren; Zwischen-Commits machen den Fortschritt sichtbar.
        jobs = [(phone, text) for phone in recipients]
        log_entry = SmsLog(
            org_id=org_id,
            sent_at=now.replace(tzinfo=None),
            completed_at=None,
            source="alarm", variant=variant,
            alarm_type_code=alarm_type_code,
            text=text,
            recipient_count=len(jobs),
            success_count=0,
            provider=None,
            incident_id=incident_id,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(log_entry)
        db.flush()
        from app.services import sms_outbox

        sms_ctx = resolve_sms_config(org_id, db)
        durable_jobs = sms_outbox.queue_log(
            db, log_entry, {phone: (member.full_name, member.id) for phone, member in recipients.items()}, sms_ctx
        )
        db.commit()
        pending_recipients = {
            r.phone_number: r for r in db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log_entry.id)
        }

        if not sms_available(org_id, db):
            log_entry.provider = "wartet_gateway"
            db.commit()
            deadline = (now + EINSATZINFO_NACHVERSAND_FENSTER).strftime("%H:%M")
            logger.warning(
                "Einsatzinfo-SMS wartet auf Gateway (Einsatz %s, org_id=%d, empfaenger=%d)",
                incident_id,
                org_id,
                len(jobs),
            )
            return EinsatzinfoDispatchResult(
                False,
                f"wartet auf SMS-Gateway (Nachversand bis {deadline})",
                len(jobs),
                handed_off=True,
                status="pending",
            )

        sms_ctx = resolve_sms_config(org_id, db)
        # Waehrend des Erstversands als "sendet" markieren: ein parallel per
        # Gateway-Connect/Loop gestarteter Nachversand greift nur "ausstehend"
        # und schickt dieselben Empfaenger so nicht doppelt an.
        for recipient in pending_recipients.values():
            recipient.provider = "sendet"
            recipient.lease_until = _einsatzinfo_lease_until(now.replace(tzinfo=None), len(pending_recipients))
        db.commit()

        async def _record(result: SmsSendResult) -> None:
            if await _project_result(db, org_id, result):
                return
            recipient = pending_recipients[result.phone_number]
            if result.success:
                recipient.success = True
                recipient.sent_at = result.sent_at
                recipient.provider = result.provider
                recipient.gateway_label = result.gateway_label
                log_entry.success_count += 1
                recipient.lease_until = None
            else:
                recipient.provider = "ausstehend"
                recipient.lease_until = None
            db.commit()

        try:
            results = await _send_bulk_with_progress(
                org_id,
                durable_jobs,
                sms_ctx,
                _record,
            )
        finally:
            for recipient in pending_recipients.values():
                if not recipient.dispatch_job_id and not recipient.success and recipient.provider == "sendet":
                    recipient.provider = "ausstehend"
                    recipient.lease_until = None
            db.commit()
        total = len(results)
        success = sum(result.success for result in results)
        if not any(r.job_id for r in results):
            log_entry.success_count = success
        log_entry.provider = ",".join(sorted(sms_ctx.providers_used)) or None
        if success == total:
            log_entry.completed_at = datetime.now(UTC).replace(tzinfo=None)
        write_audit(
            db,
            "sms.einsatzinfo_sent",
            org_id=org_id,
            user_id=triggered_by_user_id,
            payload={
                "alarm_type_code": alarm_type_code,
                "recipient_count": total,
                "success_count": success,
                "is_exercise": is_exercise,
            },
        )
        db.commit()

        logger.info(
            "Einsatzinfo-SMS gesendet (Einsatz %s, org_id=%d, stichwort=%s, is_exercise=%s, gesamt=%d, erfolgreich=%d)",
            incident_id,
            org_id,
            alarm_type_code,
            is_exercise,
            total,
            success,
        )
        if any(r.job_id for r in results):
            return persistent_log_result(org_id, log_entry.id)
        if success:
            return EinsatzinfoDispatchResult(True, recipient_count=success, handed_off=True)
        reason = "wartet auf SMS-Gateway (Nachversand innerhalb von 60 min)"
        logger.warning(
            "Einsatzinfo-SMS uebersprungen: %s (Einsatz %s, org_id=%d, stichwort=%s, is_exercise=%s)",
            reason,
            incident_id,
            org_id,
            alarm_type_code,
            is_exercise,
        )
        return EinsatzinfoDispatchResult(False, reason, total, handed_off=True)
    except Exception:
        logger.exception(
            "Fehler beim Einsatzinfo-SMS-Versand (org_id=%d, stichwort=%s)",
            org_id,
            alarm_type_code,
        )
        try:
            db.rollback()
            if "log_entry" in locals() and log_entry.id:
                failed_log = db.query(SmsLog).filter(SmsLog.id == log_entry.id, SmsLog.org_id == org_id).first()
                if failed_log is not None:
                    pending = (
                        db.query(SmsLogRecipient)
                        .filter(
                            SmsLogRecipient.sms_log_id == failed_log.id,
                            SmsLogRecipient.success.is_(False),
                            SmsLogRecipient.provider == "ausstehend",
                        )
                        .first()
                    )
                    if pending is None:
                        failed_log.completed_at = datetime.now(UTC).replace(tzinfo=None)
                    else:
                        failed_log.provider = "wartet_gateway"
                    db.commit()
        except Exception:
            pass
        return EinsatzinfoDispatchResult(False, "Versandfehler")
    finally:
        db.close()


async def dispatch_gsl_alarm(
    org_id: int,
    lage_name: str,
    is_exercise: bool,
    triggered_by_user_id: int | None = None,
    *,
    planspiel_run_id: int | None = None,
) -> None:
    """Versendet den einmaligen Grossschadenslage-Sonderalarm per SMS bei Ausrufung einer
    neuen Lage — unabhaengig von der stichwortbezogenen Einsatzinfo-SMS (anderer Text,
    andere Gates: OrgSettings.gsl_alarm_enabled statt einsatzinfo_sms_enabled).

    Empfaenger: derselbe Basis-Verteiler wie bei der Einsatzinfo-SMS (alarm_type_id=None) —
    bewusst kein eigener Verteiler, da der Sonderalarm alle erreichen soll.
    """
    from app.services.sms_service import resolve_sms_config, sms_available

    if not sms_available(org_id):
        logger.debug("Kein SMS-Provider verfuegbar (org_id=%d) - GSL-Alarm-SMS uebersprungen", org_id)
        return

    db = current_read_session(SessionLocal)
    set_tenant_context(db, None)
    try:
        sms_ctx = resolve_sms_config(org_id, db)
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not org_settings or not org_settings.gsl_alarm_enabled:
            logger.debug("GSL-Alarm-SMS deaktiviert (org_id=%d) — uebersprungen", org_id)
            return

        from app.services.exercise_guard import darf_extern

        if not darf_extern(
            "sms",
            is_exercise=is_exercise,
            org_id=org_id,
            db=db,
            planspiel_run_id=planspiel_run_id,
        ):
            logger.debug("GSL-Alarm-SMS bei Uebung unterdrueckt (org_id=%d) — uebersprungen", org_id)
            return

        exercise_prefix = "[UEBUNG] " if is_exercise else ""
        text_ = exercise_prefix + (org_settings.gsl_alarm_text or _DEFAULT_GSL_ALARM_TEXT).replace(
            "{lage}",
            lage_name,
        )

        recipients = collect_einsatzinfo_recipients(db, org_id, alarm_type_id=None)
        if not recipients:
            logger.debug("Keine SMS-Empfaenger konfiguriert (org_id=%d, GSL-Alarm)", org_id)
            return

        jobs = [(phone, text_) for phone in recipients]
        now = datetime.now(UTC).replace(tzinfo=None)
        log_entry = SmsLog(
            org_id=org_id,
            sent_at=now,
            completed_at=None,
            source="gsl_alarm",
            alarm_type_code=None,
            text=text_,
            recipient_count=len(jobs),
            success_count=0,
            provider=None,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(log_entry)
        db.flush()

        from app.services import sms_outbox

        jobs = sms_outbox.queue_log(
            db, log_entry, {phone: (member.full_name, member.id) for phone, member in recipients.items()}, sms_ctx
        )
        db.commit()
        by_phone = {
            r.phone_number: r for r in db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log_entry.id)
        }

        async def _record(result: SmsSendResult) -> None:
            if await _project_result(db, org_id, result):
                return
            row = by_phone[result.phone_number]
            row.success, row.provider, row.gateway_label = result.success, result.provider, result.gateway_label
            row.sent_at = result.sent_at
            if result.success:
                log_entry.success_count += 1
            db.commit()

        results = await _send_bulk_with_progress(org_id, jobs, sms_ctx, _record)
        total = len(results)
        success = sum(result.success for result in results)
        if not any(r.job_id for r in results):
            log_entry.success_count = success
        log_entry.provider = ",".join(sorted(sms_ctx.providers_used)) or None
        if not any(r.job_id for r in results):
            log_entry.completed_at = datetime.now(UTC).replace(tzinfo=None)
        write_audit(
            db,
            "sms.gsl_alarm_sent",
            org_id=org_id,
            user_id=triggered_by_user_id,
            payload={
                "lage_name": lage_name,
                "recipient_count": total,
                "success_count": success,
                "is_exercise": is_exercise,
            },
        )
        db.commit()

        logger.info(
            "GSL-Alarm-SMS gesendet (org_id=%d, gesamt=%d, erfolgreich=%d)",
            org_id,
            total,
            success,
        )
    except Exception:
        logger.exception("Fehler beim GSL-Alarm-SMS-Versand (org_id=%d)", org_id)
        try:
            db.rollback()
            if "log_entry" in locals() and log_entry.id:
                failed_log = db.query(SmsLog).filter(SmsLog.id == log_entry.id, SmsLog.org_id == org_id).first()
                if failed_log is not None:
                    failed_log.completed_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
        except Exception:
            pass
    finally:
        db.close()
