"""Unauthenticated, HMAC-authenticated Resend webhook endpoint (SEC-11)."""
import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.core.rate_limit import limiter as _limiter
from app.core.security import unsign_mailing_webhook_org
from app.db import get_db
from app.models.mailing import MailingConfig
from app.services.mailing_service import mailing_webhook_secret
from app.services.mailing_webhook_service import process_resend_webhook_event, verify_resend_webhook_signature

router = APIRouter(tags=["mailing-webhook"])

@router.post("/mailing/webhook/resend/{org_token}")
@(_limiter.limit("120/minute") if _limiter else lambda f: f)
async def resend_webhook(org_token: str, request: Request, db: Session = Depends(get_db)):
    org_id = unsign_mailing_webhook_org(org_token)
    if org_id is None:
        return JSONResponse({"detail":"Nicht gefunden"}, status_code=404)
    cfg = (db.query(MailingConfig).execution_options(include_all_tenants=True)
           .filter(MailingConfig.org_id == org_id).first())
    secret = mailing_webhook_secret(cfg)
    if not cfg or not secret:
        return JSONResponse({"detail":"Nicht gefunden"}, status_code=404)
    svix_id=request.headers.get("svix-id"); timestamp=request.headers.get("svix-timestamp")
    signature=request.headers.get("svix-signature")
    if not all((svix_id, timestamp, signature)):
        return JSONResponse({"detail":"Svix-Header fehlen"}, status_code=400)
    body = await request.body()
    if not verify_resend_webhook_signature(secret, body, svix_id, timestamp, signature):
        return JSONResponse({"detail":"Ungültige Signatur"}, status_code=401)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"ok": True}
    process_resend_webhook_event(db, org_id, payload.get("type", ""), payload, svix_id)
    return {"ok": True}
