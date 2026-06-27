"""SMS-Einsatzinfo-Dienst: automatischer Versand bei Alarmeingang.

Baut auf sms_service.send_sms (Gateway-WebSocket) auf.
Wird als BackgroundTask nach dem Einsatz-Commit aufgerufen.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

# Sicher auf Modul-Ebene importierbar (keine Kreisabhaengigkeiten)
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.sms import SmsEinsatzinfoRecipient, SmsLog

# app.routers.ws muss lazy importiert bleiben (Kreisabhaengigkeit)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("einsatzleiter.sms_dispatch")

# Standardvorlage fuer Einsatzinfo-SMS (Platzhalter in geschweiften Klammern)
_DEFAULT_TEMPLATE = "Einsatz {stichwort}: {adresse}. {meldung}"

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
    db: "Session",
    org_id: int,
    alarm_type_id: int | None,
) -> dict[str, object]:
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

    result: dict[str, object] = {}

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


async def send_bulk(org_id: int, jobs: list[tuple[str, str]]) -> tuple[int, int]:
    """Sendet SMS an mehrere Empfaenger sequentiell.

    jobs: Liste von (telefonnummer, text)-Tupeln.
    Rueckgabe: (anzahl_gesamt, anzahl_erfolgreich).
    """
    from app.services.sms_service import send_sms

    total = len(jobs)
    success = 0
    for to, text in jobs:
        try:
            ok = await send_sms(org_id, to, text)
            if ok:
                success += 1
        except Exception as exc:
            logger.warning("SMS-Versand fehlgeschlagen an %s: %s", to[-4:] + "****", exc)
    return total, success


async def dispatch_einsatzinfo(
    org_id: int,
    alarm_type_code: str,
    address: str,
    ort: str | None,
    meldung: str | None,
    einsatzgrund: str | None,
    is_exercise: bool,
    triggered_by_user_id: int | None = None,
) -> None:
    """Versendet automatische Einsatzinfo-SMS nach Alarmeingang.

    Wird als BackgroundTask nach dem Einsatz-Commit aufgerufen.
    Oeffnet eine eigene DB-Session (unabhaengig vom Request-Lifecycle).

    Gates (kein Versand wenn):
      - einsatzinfo_sms_enabled == False
      - is_exercise == True UND einsatzinfo_sms_send_exercise == False
      - Kein SMS-Gateway verbunden
      - Keine Empfaenger konfiguriert
    """
    from app.routers.ws import is_sms_gateway_connected  # lazy: Kreisabhaengigkeit

    if not is_sms_gateway_connected(org_id):
        logger.debug("Kein SMS-Gateway verbunden (org_id=%d) — Einsatzinfo-SMS uebersprungen", org_id)
        return

    db = SessionLocal()
    set_tenant_context(db, None)  # system-level: alle Orgs sichtbar fuer Subqueries
    try:
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
        }
        text = exercise_prefix + render_template(template, ctx)

        # Empfaenger sammeln (inkl. Gruppen-Expansion, Dedup, Telefonnummer-Filter)
        recipients = collect_einsatzinfo_recipients(db, org_id, alarm_type_id)
        if not recipients:
            logger.debug(
                "Keine SMS-Empfaenger konfiguriert (org_id=%d, stichwort=%s)",
                org_id, alarm_type_code,
            )
            return

        # Versenden
        jobs = [(phone, text) for phone in recipients]
        total, success = await send_bulk(org_id, jobs)

        # Protokollieren
        log_entry = SmsLog(
            org_id=org_id,
            sent_at=now,
            source="alarm",
            alarm_type_code=alarm_type_code,
            text=text,
            recipient_count=total,
            success_count=success,
            triggered_by_user_id=triggered_by_user_id,
        )
        db.add(log_entry)
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
        except Exception:
            pass
    finally:
        db.close()
