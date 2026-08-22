"""Resend/Svix webhook verification and idempotent event processing."""
import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

from app.models.mailing import MailingQueueItem, MailingSuppressionEntry, MailingWebhookEvent

def verify_resend_webhook_signature(secret_b64: str, body: bytes, svix_id: str, svix_timestamp: str,
                                    svix_signature: str, *, tolerance_seconds: int = 300) -> bool:
    try:
        timestamp = int(svix_timestamp)
        if abs(int(time.time()) - timestamp) > tolerance_seconds:
            return False
        encoded = secret_b64.removeprefix("whsec_")
        secret = base64.b64decode(encoded, validate=True)
        signed = f"{svix_id}.{svix_timestamp}.{body.decode()}".encode()
        expected = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode()
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return False
    return any(
        part.startswith("v1,") and hmac.compare_digest(expected, part[3:])
        for part in svix_signature.split()
    )

def _suppress(db, org_id: int, email: str, reason: str, event_id: int):
    email = email.strip().lower()
    hit = (db.query(MailingSuppressionEntry).execution_options(include_all_tenants=True)
           .filter(MailingSuppressionEntry.org_id == org_id, MailingSuppressionEntry.email == email).first())
    now = datetime.now(UTC).replace(tzinfo=None)
    if hit:
        hit.reason, hit.source_event_id, hit.created_at = reason, event_id, now
    else:
        db.add(MailingSuppressionEntry(org_id=org_id, email=email, reason=reason,
                                       source_event_id=event_id, created_at=now))

def process_resend_webhook_event(db, org_id: int, event_type: str, payload: dict,
                                 svix_id: str) -> MailingWebhookEvent:
    existing = (db.query(MailingWebhookEvent).execution_options(include_all_tenants=True)
                .filter(MailingWebhookEvent.svix_id == svix_id).first())
    if existing:
        return existing
    data = payload.get("data") or {}
    message_id = data.get("email_id")
    item = None
    if message_id:
        item = (db.query(MailingQueueItem).execution_options(include_all_tenants=True)
                .filter(MailingQueueItem.org_id == org_id,
                        MailingQueueItem.resend_message_id == message_id).first())
    now = datetime.now(UTC).replace(tzinfo=None)
    event = MailingWebhookEvent(org_id=org_id, queue_item_id=item.id if item else None,
        campaign_id=item.campaign_id if item else None, event_type=event_type,
        resend_message_id=message_id, svix_id=svix_id,
        payload_json=json.dumps(payload, ensure_ascii=False), received_at=now)
    db.add(event); db.flush()
    if item and event_type == "email.delivered":
        item.status = "delivered"
    elif item and event_type == "email.bounced":
        item.status = "bounced"
        bounce = data.get("bounce") or {}
        bounce_type = bounce.get("type")
        soft = {"soft", "transient", "temporary"}
        if bounce_type is None or str(bounce_type).lower() not in soft:
            _suppress(db, org_id, item.email, "hard_bounce", event.id)
    elif item and event_type == "email.complained":
        _suppress(db, org_id, item.email, "complaint", event.id)
    event.processed_at = now
    db.commit()
    return event
