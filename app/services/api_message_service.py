"""Annahme, Empfaengeraufloesung und Serialisierung der Nachrichten-API."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func

from app.config import settings
from app.core.telefon import telefon_e164
from app.models.api_message import ApiMessage, ApiMessageRecipient
from app.models.mailing import (
    MailingRecipientList,
    MailingRecipientListEntry,
    MailingSuppressionEntry,
)
from app.models.master import Member
from app.models.sms import SmsGroup, SmsGroupMember, SmsLog
from app.models.user import ApiKey
from app.services.mail_service import _looks_like_email


@dataclass(frozen=True)
class Recipient:
    ziel: str
    name: str | None = None
    member_id: int | None = None
    status: str = "queued"


def _missing_ids(requested: list[int], found: set[int]) -> None:
    if set(requested) != found:
        raise HTTPException(404, "Empfängergruppe oder -liste nicht gefunden")


def resolve_sms_recipients(db, org_id: int, empfaenger) -> tuple[list[Recipient], list[dict]]:
    result: dict[str, Recipient] = {}
    rejected: list[dict] = []
    for raw in empfaenger.nummern:
        nummer = telefon_e164(raw)
        if nummer:
            result.setdefault(nummer, Recipient(nummer))
        else:
            rejected.append({"wert": raw, "grund": "ungueltige_nummer"})

    gruppen = []
    if empfaenger.gruppen_ids:
        gruppen = db.query(SmsGroup).filter(
            SmsGroup.org_id == org_id, SmsGroup.id.in_(empfaenger.gruppen_ids)
        ).all()
        _missing_ids(empfaenger.gruppen_ids, {gruppe.id for gruppe in gruppen})
    member_ids = set(empfaenger.mitglieder_ids)
    for gruppe in gruppen:
        member_ids.update(
            member_id for (member_id,) in db.query(SmsGroupMember.member_id).filter(
                SmsGroupMember.sms_group_id == gruppe.id
            ).all()
        )
    members = []
    if member_ids:
        members = db.query(Member).filter(
            Member.org_id == org_id, Member.id.in_(member_ids)
        ).all()
        if empfaenger.mitglieder_ids:
            _missing_ids(empfaenger.mitglieder_ids, {m.id for m in members})
    for member in members:
        nummer = telefon_e164(member.phone)
        if nummer and member.active:
            result.setdefault(nummer, Recipient(nummer, member.full_name, member.id))
        elif member.id in empfaenger.mitglieder_ids:
            rejected.append({"wert": str(member.id), "grund": "ungueltige_nummer"})
    return list(result.values()), rejected


def resolve_mail_recipients(db, org_id: int, empfaenger) -> tuple[list[Recipient], list[dict]]:
    result: dict[str, Recipient] = {}
    rejected: list[dict] = []
    for raw in empfaenger.adressen:
        email = raw.strip().lower()
        if _looks_like_email(email):
            result.setdefault(email, Recipient(email))
        else:
            rejected.append({"wert": raw, "grund": "ungueltige_adresse"})
    listen = []
    if empfaenger.listen_ids:
        listen = db.query(MailingRecipientList).filter(
            MailingRecipientList.org_id == org_id,
            MailingRecipientList.id.in_(empfaenger.listen_ids),
        ).all()
        _missing_ids(empfaenger.listen_ids, {liste.id for liste in listen})
        entries = db.query(MailingRecipientListEntry).filter(
            MailingRecipientListEntry.org_id == org_id,
            MailingRecipientListEntry.list_id.in_([liste.id for liste in listen]),
        ).all()
        for entry in entries:
            email = entry.email.strip().lower()
            if _looks_like_email(email):
                result.setdefault(email, Recipient(email, entry.display_name))
    if result:
        suppressed = {
            email for (email,) in db.query(MailingSuppressionEntry.email).filter(
                MailingSuppressionEntry.org_id == org_id,
                MailingSuppressionEntry.email.in_(result),
            ).all()
        }
        for email in suppressed:
            old = result[email]
            result[email] = Recipient(old.ziel, old.name, status="suppressed")
    return list(result.values()), rejected


def enforce_recipient_limits(db, org_id: int, kanal: str, count: int) -> None:
    if count > settings.API_MESSAGE_MAX_RECIPIENTS:
        raise HTTPException(422, "Zu viele Empfänger")
    limit = settings.API_SMS_DAILY_LIMIT if kanal == "sms" else settings.API_MAIL_DAILY_LIMIT
    if not limit:
        return
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    used = db.query(func.count(ApiMessageRecipient.id)).join(ApiMessage).filter(
        ApiMessage.org_id == org_id,
        ApiMessage.kanal == kanal,
        ApiMessage.created_at >= since,
    ).scalar() or 0
    if used + count > limit:
        raise HTTPException(429, "Tages-Empfängerlimit überschritten")


def create_message(
    db, api_key: ApiKey, kanal: str, external_key: str, recipients: list[Recipient],
    *, betreff: str | None = None, body_text: str | None = None, body_html: str | None = None,
) -> tuple[ApiMessage, bool]:
    existing = db.query(ApiMessage).filter(
        ApiMessage.org_id == api_key.org_id,
        ApiMessage.kanal == kanal,
        ApiMessage.external_key == external_key,
    ).first()
    if existing:
        return existing, True
    sms_log = None
    if kanal == "sms":
        sms_log = SmsLog(
            org_id=api_key.org_id, source="api", text=body_text or "",
            recipient_count=len(recipients), success_count=0,
        )
        db.add(sms_log)
        db.flush()
    message = ApiMessage(
        org_id=api_key.org_id, api_key_id=api_key.id, external_key=external_key,
        kanal=kanal, betreff=betreff, body_text=body_text, body_html=body_html,
        recipient_count=len(recipients), sms_log_id=sms_log.id if sms_log else None,
    )
    if recipients and all(recipient.status == "suppressed" for recipient in recipients):
        message.status = "sent"
        message.completed_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(message)
    db.flush()
    for recipient in recipients:
        db.add(ApiMessageRecipient(
            org_id=api_key.org_id, message_id=message.id, ziel=recipient.ziel,
            name=recipient.name, member_id=recipient.member_id, status=recipient.status,
            max_attempts=1 if kanal == "sms" else 3,
        ))
    db.flush()
    return message, False


def _timestamp(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def serialize_message(message: ApiMessage) -> dict:
    return {
        "id": message.id,
        "kanal": message.kanal,
        "status": message.status,
        "erstellt_am": _timestamp(message.created_at),
        "abgeschlossen_am": _timestamp(message.completed_at),
        "empfaenger_anzahl": message.recipient_count,
        "erfolg_anzahl": message.success_count,
        "fehler_anzahl": message.failed_count,
        "empfaenger": [{
            "ziel": item.ziel, "name": item.name, "status": item.status,
            "provider": item.provider, "gesendet_am": _timestamp(item.sent_at),
            "fehler": item.error_message,
        } for item in message.recipients],
    }
