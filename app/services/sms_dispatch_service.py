"""SMS-Einsatzinfo-Dienst: automatischer Versand bei Alarmeingang.

Baut auf sms_service.send_sms (Gateway-WebSocket) auf.
Wird als BackgroundTask nach dem Einsatz-Commit aufgerufen.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.config import settings

# Sicher auf Modul-Ebene importierbar (keine Kreisabhaengigkeiten)
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.sms import SmsEinsatzinfoRecipient, SmsLog, SmsLogRecipient

# app.routers.ws muss lazy importiert bleiben (Kreisabhaengigkeit)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.master import Member

logger = logging.getLogger("einsatzleiter.sms_dispatch")

# Pro Gateway duerfen nur wenige Jobs gleichzeitig auf das gekoppelte Handy treffen.
SMS_CONCURRENCY_PER_GATEWAY = 7

# Standardvorlage fuer Einsatzinfo-SMS (Platzhalter in geschweiften Klammern)
_DEFAULT_TEMPLATE = "Einsatz {stichwort}: {adresse}. {meldung} {link}"

# Standardtext fuer den Grossschadenslage-Sonderalarm (siehe dispatch_gsl_alarm()).
# Bewusst ohne Umlaute (SMS-Zeichensatz, wie gsl_verleih_sms_*_text in verleih_service.py).
_DEFAULT_GSL_ALARM_TEXT = "Grossschadenslage! Alle Mitglieder ruecken sofort ins Geraetehaus ein."

_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)]")


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
      {leitstellennummer} Leitstellen-Einsatznummer (falls vorhanden)
    """
    class _Safe(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_Safe(ctx))


def _normalize_phone(phone: str) -> str:
    """Normalisiert eine Telefonnummer fuer Deduplizierung.

    Entfernt Leerzeichen, Bindestriche und Klammern.
    """
    return _PHONE_STRIP_RE.sub("", phone).strip()


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
    from sqlalchemy import or_

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
            norm = _normalize_phone(phone)
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


async def send_bulk_detailed(
    org_id: int,
    jobs: list[tuple[str, str]],
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

        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            gateway_rows = db.query(SmsGatewayToken.id, SmsGatewayToken.label).filter(
                SmsGatewayToken.org_id == org_id,
                SmsGatewayToken.id.in_(gateway_ids),
            ).all()
            gateway_labels = {row[0]: row[1] for row in gateway_rows}
        finally:
            db.close()
    semaphore_keys: list[int | None] = list(gateway_ids) if gateway_ids else [None]
    semaphores = {
        key: asyncio.Semaphore(
            settings.EUS_SMS_CONCURRENCY
            if key is None
            else SMS_CONCURRENCY_PER_GATEWAY
        )
        for key in semaphore_keys
    }
    progress_lock = asyncio.Lock()

    async def _send(index: int, to: str, text: str) -> SmsSendResult:
        gateway_id = gateway_ids[index % len(gateway_ids)] if gateway_ids else None
        async with semaphores[gateway_id]:
            delivery = None
            try:
                if gateway_id is None:
                    delivery = await send_sms(org_id, to, text, ctx=ctx)
                else:
                    delivery = await send_sms(
                        org_id,
                        to,
                        text,
                        ctx=ctx,
                        preferred_gateway_token_id=gateway_id,
                    )
            except Exception as exc:
                logger.warning("SMS-Versand fehlgeschlagen an %s: %s", to[-4:] + "****", exc)
            gateway_token_id = getattr(delivery, "gateway_token_id", None)
            result = SmsSendResult(
                phone_number=to,
                success=bool(delivery),
                sent_at=datetime.now(UTC).replace(tzinfo=None),
                provider=getattr(delivery, "provider", None),
                gateway_label=(
                    gateway_labels.get(gateway_token_id)
                    if isinstance(gateway_token_id, int)
                    else None
                ),
            )
            if on_result is not None:
                async with progress_lock:
                    await on_result(result)
            return result

    return list(await asyncio.gather(*(
        _send(index, to, text) for index, (to, text) in enumerate(jobs)
    )))


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


async def dispatch_manual_sms(
    org_id: int,
    log_id: int,
    text: str,
    recipients: dict[str, tuple[str | None, int | None]],
    triggered_by_user_id: int,
    target_type: str,
) -> None:
    """Fuehrt einen bereits protokollierten manuellen Versand im Hintergrund aus."""
    from app.services.sms_service import resolve_sms_config

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        log_entry = db.query(SmsLog).filter(SmsLog.id == log_id, SmsLog.org_id == org_id).first()
        if log_entry is None:
            logger.error("SMS-Log %s fuer Org %s nicht gefunden", log_id, org_id)
            return
        sms_ctx = resolve_sms_config(org_id, db)

        async def _record(result: SmsSendResult) -> None:
            name, member_id = recipients[result.phone_number]
            db.add(SmsLogRecipient(
                sms_log_id=log_id,
                member_id=member_id,
                phone_number=result.phone_number,
                name=name,
                success=result.success,
                sent_at=result.sent_at,
                provider=result.provider,
                gateway_label=result.gateway_label,
            ))
            if result.success:
                log_entry.success_count += 1
            db.commit()

        results = await _send_bulk_with_progress(
            org_id, [(phone, text) for phone in recipients], sms_ctx, _record
        )
        log_entry.provider = ",".join(sorted(sms_ctx.providers_used)) or None
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
) -> None:
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
    if not sms_available(org_id):
        logger.debug("Kein SMS-Provider verfuegbar (org_id=%d) - Einsatzinfo-SMS uebersprungen", org_id)
        return

    db = SessionLocal()
    set_tenant_context(db, None)  # system-level: alle Orgs sichtbar fuer Subqueries
    try:
        sms_ctx = resolve_sms_config(org_id, db)
        # Org-Einstellungen laden
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not org_settings or not org_settings.einsatzinfo_sms_enabled:
            logger.debug(
                "Einsatzinfo-SMS deaktiviert (org_id=%d) — uebersprungen", org_id
            )
            return

        if is_exercise and not org_settings.einsatzinfo_sms_send_exercise:
            logger.debug(
                "Einsatzinfo-SMS bei Uebung unterdrueckt (org_id=%d) — uebersprungen", org_id
            )
            return

        # AlarmType fuer Stichwort-Override und ID-Lookup
        alarm_type = (
            db.query(AlarmType)
            .filter(AlarmType.org_id == org_id, AlarmType.code == alarm_type_code)
            .first()
        )
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
            "leitstellennummer": leitstellennummer or "",
        }
        # .strip(): entfernt trailing Space, falls {link} leer ist (Altdaten ohne AlarmToken)
        text = (exercise_prefix + render_template(template, ctx)).strip()

        # Empfaenger sammeln (inkl. Gruppen-Expansion, Dedup, Telefonnummer-Filter)
        recipients = collect_einsatzinfo_recipients(db, org_id, alarm_type_id)
        if not recipients:
            logger.debug(
                "Keine SMS-Empfaenger konfiguriert (org_id=%d, stichwort=%s)",
                org_id, alarm_type_code,
            )
            return

        # Vor Versand protokollieren; Zwischen-Commits machen den Fortschritt sichtbar.
        jobs = [(phone, text) for phone in recipients]
        log_entry = SmsLog(
            org_id=org_id,
            sent_at=now.replace(tzinfo=None),
            completed_at=None,
            source="alarm",
            alarm_type_code=alarm_type_code,
            text=text,
            recipient_count=len(jobs),
            success_count=0,
            provider=None,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(log_entry)
        db.flush()
        db.commit()

        async def _record(result: SmsSendResult) -> None:
            member = recipients[result.phone_number]
            db.add(SmsLogRecipient(
                sms_log_id=log_entry.id,
                member_id=member.id,
                phone_number=result.phone_number,
                name=member.full_name,
                success=result.success,
                sent_at=result.sent_at,
                provider=result.provider,
                gateway_label=result.gateway_label,
            ))
            if result.success:
                log_entry.success_count += 1
            db.commit()

        results = await _send_bulk_with_progress(org_id, jobs, sms_ctx, _record)
        total = len(results)
        success = sum(result.success for result in results)
        log_entry.success_count = success
        log_entry.provider = ",".join(sorted(sms_ctx.providers_used)) or None
        log_entry.completed_at = datetime.now(UTC).replace(tzinfo=None)
        write_audit(
            db, "sms.einsatzinfo_sent",
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
            "Einsatzinfo-SMS gesendet (org_id=%d, stichwort=%s, gesamt=%d, erfolgreich=%d)",
            org_id, alarm_type_code, total, success,
        )
    except Exception:
        logger.exception(
            "Fehler beim Einsatzinfo-SMS-Versand (org_id=%d, stichwort=%s)",
            org_id, alarm_type_code,
        )
        try:
            db.rollback()
            if "log_entry" in locals() and log_entry.id:
                failed_log = db.query(SmsLog).filter(
                    SmsLog.id == log_entry.id, SmsLog.org_id == org_id
                ).first()
                if failed_log is not None:
                    failed_log.completed_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def dispatch_gsl_alarm(
    org_id: int,
    lage_name: str,
    is_exercise: bool,
    triggered_by_user_id: int | None = None,
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

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sms_ctx = resolve_sms_config(org_id, db)
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not org_settings or not org_settings.gsl_alarm_enabled:
            logger.debug("GSL-Alarm-SMS deaktiviert (org_id=%d) — uebersprungen", org_id)
            return

        if is_exercise and not org_settings.einsatzinfo_sms_send_exercise:
            logger.debug("GSL-Alarm-SMS bei Uebung unterdrueckt (org_id=%d) — uebersprungen", org_id)
            return

        exercise_prefix = "[UEBUNG] " if is_exercise else ""
        text_ = exercise_prefix + (org_settings.gsl_alarm_text or _DEFAULT_GSL_ALARM_TEXT).replace(
            "{lage}", lage_name,
        )

        recipients = collect_einsatzinfo_recipients(db, org_id, alarm_type_id=None)
        if not recipients:
            logger.debug("Keine SMS-Empfaenger konfiguriert (org_id=%d, GSL-Alarm)", org_id)
            return

        jobs = [(phone, text_) for phone in recipients]
        now = datetime.now(UTC).replace(tzinfo=None)
        log_entry = SmsLog(
            org_id=org_id, sent_at=now, completed_at=None,
            source="gsl_alarm", alarm_type_code=None,
            text=text_, recipient_count=len(jobs), success_count=0, provider=None,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(log_entry)
        db.flush()
        db.commit()

        async def _record(result: SmsSendResult) -> None:
            member = recipients[result.phone_number]
            db.add(SmsLogRecipient(
                sms_log_id=log_entry.id,
                member_id=member.id,
                phone_number=result.phone_number,
                name=member.full_name,
                success=result.success,
                sent_at=result.sent_at,
                provider=result.provider,
                gateway_label=result.gateway_label,
            ))
            if result.success:
                log_entry.success_count += 1
            db.commit()

        results = await _send_bulk_with_progress(org_id, jobs, sms_ctx, _record)
        total = len(results)
        success = sum(result.success for result in results)
        log_entry.success_count = success
        log_entry.provider = ",".join(sorted(sms_ctx.providers_used)) or None
        log_entry.completed_at = datetime.now(UTC).replace(tzinfo=None)
        write_audit(
            db, "sms.gsl_alarm_sent", org_id=org_id, user_id=triggered_by_user_id,
            payload={"lage_name": lage_name, "recipient_count": total, "success_count": success,
                     "is_exercise": is_exercise},
        )
        db.commit()

        logger.info(
            "GSL-Alarm-SMS gesendet (org_id=%d, gesamt=%d, erfolgreich=%d)",
            org_id, total, success,
        )
    except Exception:
        logger.exception("Fehler beim GSL-Alarm-SMS-Versand (org_id=%d)", org_id)
        try:
            db.rollback()
            if "log_entry" in locals() and log_entry.id:
                failed_log = db.query(SmsLog).filter(
                    SmsLog.id == log_entry.id, SmsLog.org_id == org_id
                ).first()
                if failed_log is not None:
                    failed_log.completed_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
        except Exception:
            pass
    finally:
        db.close()
