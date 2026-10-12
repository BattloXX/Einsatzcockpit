"""Versand von Einsatzinformationen per E-Mail."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from urllib.parse import quote_plus

from sqlalchemy import or_

from app.config import settings
from app.core.tenant import set_tenant_context
from app.core.timezones import to_org_tz
from app.db import SessionLocal
from app.models.einsatzinfo_mail import (
    MailEinsatzinfoLog,
    MailEinsatzinfoLogRecipient,
    MailEinsatzinfoRecipient,
)
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept, OrgSettings
from app.services.sms_dispatch_service import render_template

logger = logging.getLogger("einsatzleiter.einsatzinfo_mail")

DEFAULT_MAIL_SUBJECT = "Einsatz {stichwort} {adresse}"
DEFAULT_MAIL_TEMPLATE = """{stichwort} {adresse}
{meldung}
Alarmierung: {datum} {zeit} Uhr
Leitstellennummer: {leitstellennummer}

Einsatzinfo: {link}
Karte: {kartenlink}"""
_EMPTY_LABEL = re.compile(r"^[^:\n]+:\s*$")
_URL = re.compile(r"(https?://[^\s<]+)")


@dataclass(frozen=True)
class EinsatzinfoMailResult:
    sent: bool
    reason: str | None = None
    recipient_count: int = 0
    handed_off: bool = False


def default_einsatzinfo_mail_subject() -> str:
    """Gibt den systemweiten Standard-Betreff zurueck."""
    return DEFAULT_MAIL_SUBJECT


def default_einsatzinfo_mail_template() -> str:
    """Gibt die systemweite Standardvorlage zurueck."""
    return DEFAULT_MAIL_TEMPLATE


def _render_mail_template(template: str, context: dict) -> str:
    """Rendert eine Vorlage und entfernt leere Beschriftungszeilen."""
    lines = [line.rstrip() for line in render_template(template, context).splitlines()]
    lines = [line for line in lines if not _EMPTY_LABEL.match(line)]
    result: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        result.append(line)
        previous_blank = blank
    return "\n".join(result).strip()


def _incident_address(incident: Incident) -> str:
    street = " ".join(filter(None, [(incident.address_street or "").strip(), (incident.address_no or "").strip()]))
    return ", ".join(filter(None, [street, (incident.address_city or "").strip()]))


def build_einsatzinfo_mail_context(incident: Incident, org: FireDept, now_utc: datetime) -> dict:
    """Baut alle Platzhalter fuer Betreff und Text einer Einsatzinfo-Mail."""
    address = _incident_address(incident)
    if incident.lat is not None and incident.lng is not None:
        map_link = f"https://www.google.com/maps/search/?api=1&query={incident.lat},{incident.lng}"
    elif address:
        map_link = f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"
    else:
        map_link = ""
    local_now = to_org_tz(now_utc, org) or now_utc
    link = ""
    if incident.alarm_token:
        link = f"{settings.effective_public_base_url.rstrip('/')}/alarm/{incident.alarm_token}"
    return {
        "stichwort": incident.alarm_type_code or "",
        "adresse": address,
        "ort": incident.address_city or "",
        "meldung": incident.report_text or "",
        "einsatzgrund": incident.reason or "",
        "datum": local_now.strftime("%d.%m.%Y"),
        "zeit": local_now.strftime("%H:%M"),
        "link": link,
        "leitstellennummer": incident.lis_operation_number or "",
        "einsatznummer": incident.id,
        "kartenlink": map_link,
    }


def collect_einsatzinfo_mail_recipients(
    db, org_id: int, alarm_type_id: int | None
) -> dict[str, tuple[object | None, str | None]]:
    """Sammelt Mitglieder, Gruppen und externe Mail-Adressen fuer den Verteiler."""
    from app.services.mail_service import _looks_like_email

    result: dict[str, tuple[object | None, str | None]] = {}
    entries = db.query(MailEinsatzinfoRecipient).filter(
        MailEinsatzinfoRecipient.org_id == org_id,
        or_(
            MailEinsatzinfoRecipient.alarm_type_id.is_(None),
            MailEinsatzinfoRecipient.alarm_type_id == alarm_type_id,
        ),
    ).all()
    for entry in entries:
        members = []
        if entry.group_id and entry.group:
            members = [gm.member for gm in entry.group.members if gm.member]
        elif entry.member_id and entry.member:
            members = [entry.member]
        elif entry.email:
            email = entry.email.strip()
            if _looks_like_email(email):
                result.setdefault(email.lower(), (None, None))
        for member in members:
            email = (member.email or "").strip()
            if member.active and _looks_like_email(email):
                result.setdefault(email.lower(), (member, getattr(member, "full_name", None)))
    return result


def _html_body(body: str) -> str:
    """Erzeugt eine schlichte HTML-Alternative mit klickbaren Links."""
    escaped = html.escape(body)
    return _URL.sub(lambda match: f'<a href="{match.group(1)}">{match.group(1)}</a>', escaped).replace("\n", "<br>\n")


async def dispatch_einsatzinfo_mail(
    org_id: int,
    incident_id: int,
    triggered_by_user_id: int | None = None,
) -> EinsatzinfoMailResult:
    """Versendet Einsatzinfo-Mails und protokolliert jeden Bcc-Block persistent."""
    from app.services import mail_service
    from app.services.exercise_guard import darf_extern

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        # org_id kommt aus dem Alarmauftrag (wie bei der Einsatzinfo-SMS)
        incident = db.get(Incident, incident_id)
        if incident is None:
            logger.info("Einsatzinfo-Mail uebersprungen: Einsatz %s nicht gefunden", incident_id)
            return EinsatzinfoMailResult(False, "Einsatz nicht gefunden")
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not org_settings or not org_settings.einsatzinfo_mail_enabled:
            logger.info("Einsatzinfo-Mail uebersprungen: deaktiviert (Einsatz %s)", incident_id)
            return EinsatzinfoMailResult(False, "deaktiviert")
        if not darf_extern("mail", is_exercise=incident.is_exercise, org_id=org_id, db=db):
            logger.info("Einsatzinfo-Mail uebersprungen: Uebung unterdrueckt (Einsatz %s)", incident_id)
            return EinsatzinfoMailResult(False, "Uebung unterdrueckt")
        if not mail_service.mail_available(db, org_id):
            logger.info("Einsatzinfo-Mail uebersprungen: kein Mailversand konfiguriert (Einsatz %s)", incident_id)
            return EinsatzinfoMailResult(False, "kein Mailversand konfiguriert")

        alarm_type = db.query(AlarmType).filter(
            AlarmType.org_id == org_id, AlarmType.code == incident.alarm_type_code
        ).first()
        recipients = collect_einsatzinfo_mail_recipients(db, org_id, alarm_type.id if alarm_type else None)
        if not recipients:
            logger.info("Einsatzinfo-Mail uebersprungen: keine Empfaenger konfiguriert (Einsatz %s)", incident_id)
            return EinsatzinfoMailResult(False, "keine Empfaenger konfiguriert")

        log_entry = db.query(MailEinsatzinfoLog).filter(
            MailEinsatzinfoLog.org_id == org_id, MailEinsatzinfoLog.incident_id == incident_id
        ).first()
        if log_entry is None:
            org = db.query(FireDept).filter(FireDept.id == org_id).first()
            if org is None:
                return EinsatzinfoMailResult(False, "Organisation nicht gefunden")
            now = datetime.now(UTC)
            context = build_einsatzinfo_mail_context(incident, org, now)
            subject_template = (
                (alarm_type.einsatzinfo_mail_subject_template if alarm_type else None)
                or org_settings.einsatzinfo_mail_subject_template
                or default_einsatzinfo_mail_subject()
            )
            body_template = (
                (alarm_type.einsatzinfo_mail_template if alarm_type else None)
                or org_settings.einsatzinfo_mail_template
                or default_einsatzinfo_mail_template()
            )
            # Header muessen einzeilig sein (z.B. {meldung} mit Zeilenumbruch im Betreff)
            subject = " ".join(_render_mail_template(subject_template, context).split())[:280]
            if incident.is_exercise:
                subject = f"[ÜBUNG] {subject}"
            body = _render_mail_template(body_template, context)
            log_entry = MailEinsatzinfoLog(
                org_id=org_id, incident_id=incident_id, alarm_type_code=incident.alarm_type_code,
                subject=subject, body=body, recipient_count=len(recipients), success_count=0,
                triggered_by_user_id=triggered_by_user_id, sent_at=now.replace(tzinfo=None),
            )
            db.add(log_entry)
            db.flush()
            for index, (email, (member, name)) in enumerate(recipients.items()):
                db.add(MailEinsatzinfoLogRecipient(
                    log_id=log_entry.id, member_id=getattr(member, "id", None), email=email,
                    name=name, chunk_no=index // 50, success=False,
                ))
            db.commit()

        pending = db.query(MailEinsatzinfoLogRecipient).filter(
            MailEinsatzinfoLogRecipient.log_id == log_entry.id,
            MailEinsatzinfoLogRecipient.success.is_(False),
        ).order_by(MailEinsatzinfoLogRecipient.chunk_no, MailEinsatzinfoLogRecipient.id).all()
        if not pending:
            return EinsatzinfoMailResult(True, "bereits versendet", log_entry.recipient_count, True)

        sender = parseaddr(mail_service.versandweg(db, org_id).get("absender") or "")[1]
        smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
        chunks: dict[int, list[MailEinsatzinfoLogRecipient]] = {}
        for recipient in pending:
            chunks.setdefault(recipient.chunk_no, []).append(recipient)
        for chunk in chunks.values():
            emails = [recipient.email for recipient in chunk]
            to = sender or emails[0]
            bcc = emails if sender else emails[1:]
            msg = mail_service._build_message(
                to=to, subject=log_entry.subject, body_txt=log_entry.body,
                body_html=_html_body(log_entry.body), smtp_cfg=smtp_cfg,
            )
            if bcc:
                msg["Bcc"] = ", ".join(bcc)
            try:
                await mail_service.deliver(db, org_id, msg, smtp_cfg)
            except Exception as exc:
                log_entry.error = str(exc)[:2000]
                db.commit()
                raise
            sent_at = datetime.now(UTC).replace(tzinfo=None)
            for recipient in chunk:
                recipient.success = True
                recipient.sent_at = sent_at
            log_entry.success_count += len(chunk)
            db.commit()
        log_entry.completed_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
        return EinsatzinfoMailResult(True, recipient_count=log_entry.success_count)
    finally:
        db.close()
