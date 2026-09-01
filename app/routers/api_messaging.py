"""Externe Nachrichten-API für persistenten SMS- und E-Mail-Versand."""
from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.dependencies import require_scope
from app.core.rate_limit import get_api_key_identifier
from app.core.rate_limit import limiter as _limiter
from app.db import get_db
from app.models.api_message import ApiMessage
from app.models.user import ApiKey
from app.services import api_message_service, mail_service
from app.services.api_message_dispatch_loop import send_message_now
from app.services.sms_service import sms_available

router = APIRouter(prefix="/api/v1", tags=["Nachrichten"])


class SmsRecipients(BaseModel):
    nummern: list[str] = Field(default_factory=list, description="Freie E.164-Rufnummern.")
    gruppen_ids: list[int] = Field(default_factory=list, description="IDs von SMS-Gruppen.")
    mitglieder_ids: list[int] = Field(default_factory=list, description="IDs von Mitgliedern.")


class MailRecipients(BaseModel):
    adressen: list[str] = Field(default_factory=list, description="Freie E-Mail-Adressen.")
    listen_ids: list[int] = Field(default_factory=list, description="IDs von Mailinglisten.")


class SmsMessageRequest(BaseModel):
    Key: str = Field(min_length=1, max_length=200, description="Idempotenz-Token je Kanal.")
    text: str = Field(min_length=1, description="Zu versendender SMS-Text.")
    empfaenger: SmsRecipients

    @model_validator(mode="after")
    def validate_body(self):
        if len(self.text) > settings.API_MESSAGE_MAX_BODY_CHARS:
            raise ValueError("Nachrichtentext ist zu lang")
        return self


class MailMessageRequest(BaseModel):
    Key: str = Field(min_length=1, max_length=200, description="Idempotenz-Token je Kanal.")
    betreff: str = Field(min_length=1, max_length=500, description="Betreff der E-Mail.")
    text: str | None = Field(default=None, description="Text-Version der E-Mail.")
    html: str | None = Field(default=None, description="Optionale HTML-Version der E-Mail.")
    empfaenger: MailRecipients

    @model_validator(mode="after")
    def validate_body(self):
        if not (self.text or self.html):
            raise ValueError("Mindestens text oder html ist erforderlich")
        if len(self.text or "") + len(self.html or "") > settings.API_MESSAGE_MAX_BODY_CHARS:
            raise ValueError("Nachrichteninhalt ist zu lang")
        return self


class RejectedResponse(BaseModel):
    wert: str
    grund: str


class MessageAcceptedResponse(BaseModel):
    id: int
    kanal: Literal["sms", "mail"]
    status: str
    empfaenger_anzahl: int
    abgelehnt: list[RejectedResponse]
    idempotent_hit: bool


class GatewaySendRequest(BaseModel):
    to: str = Field(min_length=1, description="Komma-separierte E.164-Rufnummern.")
    body: str = Field(min_length=1, description="Zu versendender SMS-Text.")

    @model_validator(mode="after")
    def validate_body(self):
        if not self.body.strip():
            raise ValueError("Nachrichtentext darf nicht leer sein")
        if len(self.body) > settings.API_MESSAGE_MAX_BODY_CHARS:
            raise ValueError("Nachrichtentext ist zu lang")
        return self


class GatewaySendResponse(BaseModel):
    status: Literal["sent", "failed"]
    id: int
    gesendet: int
    fehlgeschlagen: int
    error: str | None = None


def _accepted(message: ApiMessage, rejected: list[dict], hit: bool) -> dict:
    return {
        "id": message.id, "kanal": message.kanal, "status": message.status,
        "empfaenger_anzahl": message.recipient_count, "abgelehnt": rejected,
        "idempotent_hit": hit,
    }


def _existing(db: Session, api_key: ApiKey, kanal: str, key: str) -> ApiMessage | None:
    return db.query(ApiMessage).filter(
        ApiMessage.org_id == api_key.org_id,
        ApiMessage.kanal == kanal,
        ApiMessage.external_key == key,
    ).first()


@router.post(
    "/sms", status_code=202, response_model=MessageAcceptedResponse,
    summary="SMS-Versand annehmen", description="Legt einen persistenten SMS-Versandauftrag an.",
    responses={401: {"description": "API-Key ungültig."}, 403: {"description": "Scope fehlt."},
               409: {"description": "Kein SMS-Versandweg."}, 429: {"description": "Limit überschritten."}},
)
@(_limiter.limit(settings.API_MESSAGE_RATELIMIT, key_func=get_api_key_identifier)
  if _limiter else lambda f: f)
async def send_sms_message(
    request: Request, payload: SmsMessageRequest, db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_scope("sms:send")),
):
    existing = _existing(db, api_key, "sms", payload.Key)
    if existing:
        return _accepted(existing, [], True)
    if not sms_available(api_key.org_id, db):
        raise HTTPException(409, "Kein SMS-Versandweg konfiguriert")
    recipients, rejected = api_message_service.resolve_sms_recipients(
        db, api_key.org_id, payload.empfaenger
    )
    if not recipients:
        raise HTTPException(422, "Keine gültigen Empfänger")
    api_message_service.enforce_recipient_limits(db, api_key.org_id, "sms", len(recipients))
    message, hit = api_message_service.create_message(
        db, api_key, "sms", payload.Key, recipients, body_text=payload.text
    )
    write_audit(
        db, "api.sms.send", org_id=api_key.org_id, api_key_id=api_key.id,
        entity_type="api_message", entity_id=message.id,
        payload={"empfaenger": len(recipients), "abgelehnt": len(rejected)},
    )
    db.commit()
    return _accepted(message, rejected, hit)


@router.post(
    "/sms/send", response_model=GatewaySendResponse, response_model_exclude_none=True,
    summary="SMS synchron versenden",
    description=(
        "Uptime-Kuma-kompatibler Gateway-Endpunkt. Versandfehler werden bewusst mit HTTP 200 "
        "und status=failed gemeldet. Der Endpunkt arbeitet ohne Idempotenzschutz."
    ),
    responses={401: {"description": "API-Key ungültig."}, 403: {"description": "Scope fehlt."},
               409: {"description": "Kein SMS-Versandweg."}, 422: {"description": "Ungültige Eingabe."},
               429: {"description": "Limit überschritten."}},
)
@(_limiter.limit(settings.API_MESSAGE_RATELIMIT, key_func=get_api_key_identifier)
  if _limiter else lambda f: f)
async def send_sms_gateway(
    request: Request, payload: GatewaySendRequest, db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_scope("sms:send")),
):
    if not sms_available(api_key.org_id, db):
        raise HTTPException(409, "Kein SMS-Versandweg konfiguriert")
    recipients, _rejected = api_message_service.parse_to_field(payload.to)
    if not recipients:
        raise HTTPException(422, "Keine gültige Rufnummer in 'to'")
    if len(recipients) > settings.API_SMS_SYNC_MAX_RECIPIENTS:
        raise HTTPException(
            422,
            f"Zu viele Empfänger für synchronen Versand; bitte POST /api/v1/sms verwenden "
            f"(maximal {settings.API_SMS_SYNC_MAX_RECIPIENTS})",
        )
    api_message_service.enforce_recipient_limits(db, api_key.org_id, "sms", len(recipients))
    message, _ = api_message_service.create_message(
        db, api_key, "sms", f"uptime-kuma:{uuid4().hex}", recipients,
        body_text=payload.body, initial_status="sending",
    )
    await send_message_now(db, message)
    status = "sent" if message.status == "sent" else "failed"
    response = {
        "status": status,
        "id": message.id,
        "gesendet": message.success_count,
        "fehlgeschlagen": message.failed_count,
    }
    if status == "failed":
        response["error"] = "SMS-Versand fehlgeschlagen"
    write_audit(
        db, "api.sms.gateway_send", org_id=api_key.org_id, api_key_id=api_key.id,
        entity_type="api_message", entity_id=message.id,
        payload={"empfaenger": len(recipients), "status": status},
    )
    db.commit()
    return response


@router.post(
    "/mail", status_code=202, response_model=MessageAcceptedResponse,
    summary="E-Mail-Versand annehmen", description="Legt einen persistenten E-Mail-Versandauftrag an.",
    responses={401: {"description": "API-Key ungültig."}, 403: {"description": "Scope fehlt."},
               409: {"description": "Kein Mail-Versandweg."}, 429: {"description": "Limit überschritten."}},
)
@(_limiter.limit(settings.API_MESSAGE_RATELIMIT, key_func=get_api_key_identifier)
  if _limiter else lambda f: f)
async def send_mail_message(
    request: Request, payload: MailMessageRequest, db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_scope("mail:send")),
):
    existing = _existing(db, api_key, "mail", payload.Key)
    if existing:
        return _accepted(existing, [], True)
    if not mail_service.mail_available(db, api_key.org_id):
        raise HTTPException(409, "Kein Mail-Versandweg konfiguriert")
    recipients, rejected = api_message_service.resolve_mail_recipients(
        db, api_key.org_id, payload.empfaenger
    )
    if not recipients:
        raise HTTPException(422, "Keine gültigen Empfänger")
    api_message_service.enforce_recipient_limits(db, api_key.org_id, "mail", len(recipients))
    message, hit = api_message_service.create_message(
        db, api_key, "mail", payload.Key, recipients, betreff=payload.betreff,
        body_text=payload.text, body_html=payload.html,
    )
    write_audit(
        db, "api.mail.send", org_id=api_key.org_id, api_key_id=api_key.id,
        entity_type="api_message", entity_id=message.id,
        payload={"empfaenger": len(recipients), "abgelehnt": len(rejected)},
    )
    db.commit()
    return _accepted(message, rejected, hit)


@router.get(
    "/nachricht/{message_id}", summary="Nachrichtenstatus abrufen",
    description="Liefert Job- und Empfängerstatus eines Versandauftrags.",
    responses={401: {"description": "API-Key ungültig."}, 403: {"description": "Scope fehlt."},
               404: {"description": "Auftrag nicht gefunden."}},
)
def get_message_status(
    message_id: int, db: Session = Depends(get_db),
    api_key: ApiKey = Depends(require_scope("sms:send", "mail:send")),
):
    message = db.query(ApiMessage).filter(
        ApiMessage.id == message_id, ApiMessage.org_id == api_key.org_id
    ).first()
    if message is None:
        raise HTTPException(404, "Nachricht nicht gefunden")
    return api_message_service.serialize_message(message)
