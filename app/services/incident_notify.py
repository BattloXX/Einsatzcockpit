"""Zentrale Benachrichtigung bei Einsatzanlage: SMS-Einsatzinfo + Web-Push (+ Teams,
sobald die Teams-Alarmierung umgesetzt ist).

Grund: bisher rief jeder der drei Erzeugungspfade (REST-API `api_v1.py`, manuelle UI
`ui_incident.py`, LIS/IPR-Hintergrund-Sync `lis_sync.py`) SMS/Push unabhaengig und
unterschiedlich vollstaendig auf — nur die REST-API loeste beides aus, die manuelle UI nur
SMS (kein Push), der LIS-Sync gar nichts. `notify_incident_created()` buendelt beides an
einer Stelle, damit alle drei Pfade konsistent alarmieren.

`create_incident()` selbst ist kein geeigneter Ort fuer diese Seiteneffekte: es laeuft vor
dem Commit (ein Rollback wuerde sonst bereits verschickte Benachrichtigungen ueber einen nie
existierenden Einsatz erzeugen), hat kein `BackgroundTasks`-Objekt zur Verfuegung, und der
LIS-Sync-Pfad laeuft ausserhalb eines Requests ganz ohne `BackgroundTasks`. Diese Funktion
wird daher explizit von den Routern/dem LIS-Loop **nach** dem Commit aufgerufen.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentLog

logger = logging.getLogger("einsatzleiter.incident_notify")


def incident_alarm_started(db: Session, incident_id: int) -> bool:
    """Liefert, ob die Alarmierung für einen Einsatz bereits eingeplant wurde."""
    from app.models.user import AuditLog

    return (
        db.query(AuditLog.id)
        .filter(
            AuditLog.action == "incident.alarm_started",
            AuditLog.incident_id == incident_id,
        )
        .first()
        is not None
    )


def incident_needs_alarm_backfill(db: Session, incident: Incident) -> bool:
    """Prüft, ob ein frisch verknüpfter aktiver Einsatz noch nicht alarmiert wurde."""
    started_at = incident.started_at
    if started_at is None or incident.status != "active":
        return False
    if started_at.tzinfo is not None:
        started_at = started_at.astimezone(UTC).replace(tzinfo=None)
    now = datetime.now(UTC).replace(tzinfo=None)
    return now - started_at <= timedelta(minutes=15) and not incident_alarm_started(db, incident.id)


async def _send_incident_push(
    incident_id: int,
    org_id: int | None,
    title: str,
    body: str,
    url: str,
    extra: dict | None,
    triggered_by_user_id: int | None,
) -> None:
    """Sendet den Alarm-Push mit eigener Session und schreibt das Ergebnis ins Audit-Log."""
    from app.core.audit import write_audit
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import FcmToken, PushSubscription, User
    from app.services.push_service import notify_all, notify_org

    def _run() -> None:
        push_db = SessionLocal()
        set_tenant_context(push_db, None)
        try:
            if org_id is not None:
                user_ids = push_db.query(User.id).filter(User.org_id == org_id)
                recipient_count = (
                    push_db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).count()
                    + push_db.query(FcmToken).filter(FcmToken.user_id.in_(user_ids)).count()
                )
                sent_count = notify_org(
                    push_db,
                    org_id,
                    title,
                    body,
                    url,
                    source="einsatz_alarm",
                    channel_id="einsatz_alarm",
                    extra=extra,
                )
            else:
                recipient_count = push_db.query(PushSubscription).count() + push_db.query(FcmToken).count()
                sent_count = notify_all(
                    push_db,
                    title,
                    body,
                    url,
                    source="einsatz_alarm",
                    channel_id="einsatz_alarm",
                    extra=extra,
                )
            write_audit(
                push_db,
                "push.einsatz_alarm_sent",
                org_id=org_id,
                user_id=triggered_by_user_id,
                incident_id=incident_id,
                payload={
                    "sent_count": sent_count,
                    "recipient_count": recipient_count,
                    "channel_id": "einsatz_alarm",
                },
            )
            push_db.commit()
            if sent_count == 0 and recipient_count > 0:
                logger.warning(
                    "Kein Alarm-Push zugestellt (Einsatz %s, Empfaenger=%s)",
                    incident_id,
                    recipient_count,
                )
        except Exception:
            push_db.rollback()
            logger.exception("Push-Benachrichtigung fehlgeschlagen (Einsatz %s)", incident_id)
        finally:
            push_db.close()

    await asyncio.to_thread(_run)


async def _send_incident_wake_only(
    incident_id: int,
    org_id: int | None,
    title: str,
    body: str,
    url: str,
    triggered_by_user_id: int | None,
) -> None:
    """Weckt Android-Geraete fuer einen stillen Uebungseinsatz-Statusabgleich."""
    from app.core.audit import write_audit
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import FcmToken, User
    from app.services.push_service import notify_org_fcm_wake_only

    if org_id is None:
        return

    def _run() -> None:
        push_db = SessionLocal()
        set_tenant_context(push_db, None)
        try:
            user_ids = push_db.query(User.id).filter(User.org_id == org_id)
            recipient_count = push_db.query(FcmToken).filter(FcmToken.user_id.in_(user_ids)).count()
            sent_count = notify_org_fcm_wake_only(
                push_db,
                org_id,
                title,
                body,
                url,
                source="einsatz_wake",
                channel_id="einsatz_alarm",
            )
            write_audit(
                push_db,
                "push.einsatz_wake_sent",
                org_id=org_id,
                user_id=triggered_by_user_id,
                incident_id=incident_id,
                payload={
                    "sent_count": sent_count,
                    "recipient_count": recipient_count,
                    "channel_id": "einsatz_alarm",
                },
            )
            push_db.commit()
            if sent_count == 0 and recipient_count > 0:
                logger.warning(
                    "Kein stiller FCM-Wake zugestellt (Einsatz %s, Empfaenger=%s)",
                    incident_id,
                    recipient_count,
                )
        except Exception:
            push_db.rollback()
            logger.exception("Stiller FCM-Wake fehlgeschlagen (Einsatz %s)", incident_id)
        finally:
            push_db.close()

    await asyncio.to_thread(_run)


def _combined_address(incident: Incident) -> str:
    """Baut den `{adresse}`-Platzhalter-String — gleiches Format wie bisher in api_v1.py."""
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


async def notify_incident_created(
    db: Session,
    incident: Incident,
    *,
    org_id: int | None,
    triggered_by_user_id: int | None = None,
    push_url: str | None = None,
    base_url: str | None = None,
    background_tasks=None,
    source: str | None = None,
) -> None:
    """Loest SMS-Einsatzinfo + Web-Push + Teams-Alarmierung fuer einen neu angelegten
    Einsatz aus.

    MUSS erst nach dem Commit des Einsatzes aufgerufen werden.

    - `background_tasks` gesetzt (API-/UI-Request): Versand laeuft wie bisher als
      FastAPI-`BackgroundTask` nach der Response.
    - `background_tasks=None` (LIS-Hintergrund-Loop, kein Request-Kontext vorhanden):
      Versand laeuft sofort — SMS/Teams sind bereits async und werden direkt ge-awaitet;
      Push ist synchron und laeuft ueber `asyncio.to_thread`, damit der Poll-Loop nicht
      blockiert. Fehler werden in diesem Zweig nur geloggt, nie weitergeworfen (best
      effort, wie die bisherigen BackgroundTasks es implizit auch schon waren).
    - `base_url` wird nur für die Teams-Alarmierung gebraucht (absolute Links/Kartenbild-URL
      in der Karte). Ohne `base_url` wird der Teams-Versand übersprungen.
    """
    from app.services.exercise_guard import darf_extern
    from app.services.sms_dispatch_service import dispatch_einsatzinfo
    from app.services.teams_alarm_service import post_incident_card

    logger.info(
        "Alarmierung gestartet (Einsatz %s, Quelle %s)", incident.id, source or "unbekannt"
    )
    try:
        from app.core.audit import write_audit

        write_audit(
            db,
            "incident.alarm_started",
            org_id=org_id,
            user_id=triggered_by_user_id,
            incident_id=incident.id,
            payload={"source": source},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Alarmierungsstart konnte nicht protokolliert werden (Einsatz %s)", incident.id)

    address = _combined_address(incident)
    exercise_prefix = "[ÜBUNG] " if incident.is_exercise else ""
    push_title = f"{exercise_prefix}🚒 Einsatz: {incident.alarm_type_code}"
    push_body = address or incident.report_text or "Kein Ort angegeben"
    resolved_push_url = push_url or f"/einsatz/{incident.id}"

    live_extra = None
    try:
        from app.services.einsatz_live_service import build_incident_live_payload
        from app.services.incident_live_notify import _live_extra

        live_payload = build_incident_live_payload(db, incident)
        live_extra = _live_extra(live_payload, alert=True, kind="einsatz_live")
        incident.live_push_phase = live_payload["phase_index"]
        incident.live_push_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    except Exception:
        # Eigenstaendig best effort: weder ein Test-/Alt-DB-Problem noch Live-Metadaten
        # duerfen die bestehende Alarmierung ueber Web-Push/FCM verhindern.
        logger.exception("Initialer Live-Status fehlgeschlagen (Einsatz %s)", incident.id)

    if org_id:
        # Öffentlicher Einsatzinfo-Link (No-Login) für die SMS; request-loser Kontext →
        # settings.effective_public_base_url statt request.base_url.
        from app.config import settings
        info_link = (
            f"{settings.effective_public_base_url.rstrip('/')}/alarm/{incident.alarm_token}"
            if incident.alarm_token else ""
        )
        sms_args = (
            org_id, incident.alarm_type_code, address, incident.address_city,
            incident.report_text, incident.reason, incident.is_exercise,
            triggered_by_user_id, info_link, incident.lis_operation_number, incident.id,
        )
    else:
        sms_args = None  # Einsatzinfo-SMS ist org-gebunden — ohne Org kein Versand

    teams_args = (db, incident) if base_url else None

    async def _sms_senden():
        if sms_args is None:
            reason = "keine Organisation zugeordnet"
            logger.warning(
                "Einsatzinfo-SMS uebersprungen: %s (Einsatz %s, stichwort=%s, is_exercise=%s)",
                reason, incident.id, incident.alarm_type_code, incident.is_exercise,
            )
            return False, reason, 0
        if not darf_extern(
            "sms", is_exercise=incident.is_exercise, org_id=org_id, db=db
        ):
            reason = "Uebung unterdrueckt"
            logger.info(
                "Einsatzinfo-SMS uebersprungen: %s (Einsatz %s, stichwort=%s, is_exercise=%s)",
                reason, incident.id, incident.alarm_type_code, incident.is_exercise,
            )
            return False, reason, 0
        try:
            result = await dispatch_einsatzinfo(*sms_args)
            if result is None:  # Kompatibilität mit bestehenden Erweiterungen/Tests.
                return True, None, 0
            return result.sent, result.reason, result.recipient_count
        except Exception:
            logger.exception("Einsatzinfo-SMS fehlgeschlagen (Einsatz %s)", incident.id)
            return False, "Versandfehler", 0

    async def _push_senden() -> tuple[bool, str | None]:
        if not darf_extern(
            "push", is_exercise=incident.is_exercise, org_id=org_id, db=db
        ):
            if incident.is_exercise:
                try:
                    await _send_incident_wake_only(
                        incident.id,
                        org_id,
                        push_title,
                        push_body,
                        resolved_push_url,
                        triggered_by_user_id,
                    )
                except Exception:
                    logger.exception(
                        "Stiller FCM-Wake fehlgeschlagen (Einsatz %s)", incident.id
                    )
            return True, "Uebung unterdrueckt"
        try:
            await _send_incident_push(
                incident.id,
                org_id,
                push_title,
                push_body,
                resolved_push_url,
                live_extra,
                triggered_by_user_id,
            )
            return True, None
        except Exception:
            logger.exception("Push-Benachrichtigung fehlgeschlagen (Einsatz %s)", incident.id)
            return False, "Versandfehler"

    async def _teams_senden():
        if teams_args is None:
            reason = "keine Base-URL verfügbar"
            logger.warning(
                "Teams-Alarmierung uebersprungen: %s (Einsatz %s, stichwort=%s, is_exercise=%s)",
                reason, incident.id, incident.alarm_type_code, incident.is_exercise,
            )
            return False, reason
        if not darf_extern(
            "teams", is_exercise=incident.is_exercise, org_id=org_id, db=db
        ):
            reason = "Uebung unterdrueckt"
            logger.info(
                "Teams-Alarmierung uebersprungen: %s (Einsatz %s, stichwort=%s, is_exercise=%s)",
                reason, incident.id, incident.alarm_type_code, incident.is_exercise,
            )
            return False, reason
        assert base_url is not None  # teams_args ist nur mit base_url gesetzt
        try:
            result = await post_incident_card(*teams_args, base_url=base_url)
            if result is None:  # Kompatibilität mit bestehenden Erweiterungen/Tests.
                return True, None
            return result.sent, result.reason
        except Exception:
            logger.exception("Teams-Alarmierung fehlgeschlagen (Einsatz %s)", incident.id)
            return False, "Versandfehler"

    async def _notify_fanout() -> None:
        results = await asyncio.gather(
            _sms_senden(),
            _push_senden(),
            _teams_senden(),
            return_exceptions=True,
        )
        sms_result, push_result, teams_result = results
        if not isinstance(sms_result, BaseException):
            sms_sent, sms_reason, sms_recipients = sms_result
            sms_text = (
                f"Alarmierung SMS: gesendet an {sms_recipients}"
                if sms_sent else f"Alarmierung SMS übersprungen: {sms_reason}"
            )
            db.add(IncidentLog(
                incident_id=incident.id,
                author_name="System",
                level="info" if sms_sent else "warning",
                text=sms_text,
            ))
        if not isinstance(teams_result, BaseException):
            teams_sent, teams_reason = teams_result
            teams_text = "Alarmierung Teams: gesendet" if teams_sent else (
                f"Alarmierung Teams übersprungen: {teams_reason}"
            )
            db.add(IncidentLog(
                incident_id=incident.id,
                author_name="System",
                level="info" if teams_sent else "warning",
                text=teams_text,
            ))
        try:
            from app.core.audit import write_audit

            sms_payload = _alarm_result_payload(sms_result)
            push_payload = _alarm_result_payload(push_result)
            teams_payload = _alarm_result_payload(teams_result)
            write_audit(
                db,
                "incident.alarm_finished",
                org_id=org_id,
                user_id=triggered_by_user_id,
                incident_id=incident.id,
                payload={"sms": sms_payload, "push": push_payload, "teams": teams_payload},
            )
            db.commit()
            logger.info(
                "Alarmierung abgeschlossen (Einsatz %s): sms=%s, push=%s, teams=%s",
                incident.id, sms_payload, push_payload, teams_payload,
            )
        except Exception:
            db.rollback()
            logger.exception("Alarmierungsprotokoll fehlgeschlagen (Einsatz %s)", incident.id)

    if background_tasks is not None:
        background_tasks.add_task(_notify_fanout)
        return

    # Kein Request-Kontext (LIS-Poll-Loop) — direkt ausfuehren statt background_tasks
    await _notify_fanout()


def _alarm_result_payload(result) -> dict:
    """Normalisiert Fanout-Ergebnisse für den Audit-Eintrag."""
    if isinstance(result, BaseException):
        return {"ok": False, "reason": type(result).__name__}
    if result is None:
        return {"ok": True}
    if isinstance(result, tuple):
        payload = {"ok": bool(result[0])}
        if len(result) > 1 and result[1]:
            payload["reason"] = result[1]
        if len(result) > 2:
            payload["recipient_count"] = result[2]
        return payload
    return {"ok": True}
