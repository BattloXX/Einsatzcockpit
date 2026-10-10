"""Öffentlicher, Svix-signierter Resend-Posteingang."""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.core.rate_limit import limiter as _limiter
from app.core.security import unsign_mail_inbound_webhook_org
from app.db import get_db
from app.models.org_mail import OrgResendConfig
from app.services.mailing_webhook_service import verify_resend_webhook_signature
from app.services.resend_inbound_service import create_inbound_metadata, fetch_inbound_background

router = APIRouter(tags=["mail-inbound-webhook"])


@router.post("/mail/webhook/resend-inbound/{org_token}")
@(_limiter.limit("120/minute") if _limiter else lambda f: f)
async def resend_inbound_webhook(
    org_token: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    org_id = unsign_mail_inbound_webhook_org(org_token)
    if org_id is None:
        return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
    cfg = (db.query(OrgResendConfig).execution_options(include_all_tenants=True)
           .filter(OrgResendConfig.org_id == org_id).first())
    if not cfg or not cfg.inbound_enabled or not cfg.inbound_webhook_secret_enc:
        return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
    svix_id = request.headers.get("svix-id")
    timestamp = request.headers.get("svix-timestamp")
    signature = request.headers.get("svix-signature")
    if not svix_id or not timestamp or not signature:
        return JSONResponse({"detail": "Svix-Header fehlen"}, status_code=400)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 256 * 1024:
                return JSONResponse({"detail": "Payload zu groß"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Ungültige Content-Length"}, status_code=400)
    body = await request.body()
    if len(body) > 256 * 1024:
        return JSONResponse({"detail": "Payload zu groß"}, status_code=413)
    try:
        secret = decrypt_secret(cfg.inbound_webhook_secret_enc)
    except Exception:
        return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
    if not verify_resend_webhook_signature(secret, body, svix_id, timestamp, signature):
        return JSONResponse({"detail": "Ungültige Signatur"}, status_code=401)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"ok": True}
    if payload.get("type") != "email.received":
        return {"ok": True}
    row = create_inbound_metadata(db, org_id, payload.get("data") or {})
    db.commit()
    if row:
        background_tasks.add_task(fetch_inbound_background, org_id, row.id)
    return {"ok": True}
