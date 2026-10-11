"""Öffentlicher, Svix-signierter Resend-Posteingang."""
import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.core.rate_limit import limiter as _limiter
from app.core.security import unsign_mail_inbound_webhook_org
from app.db import get_db
from app.models.master import OrgSettings, SystemSettings
from app.models.org_mail import OrgMailEingang, OrgResendConfig
from app.services.mailing_webhook_service import verify_resend_webhook_signature
from app.services.org_mail_adresse import inbound_domain, mail_eingang_aktiv, org_aus_adresse
from app.services.resend_inbound_service import create_inbound_metadata, fetch_inbound_background

router = APIRouter(tags=["mail-inbound-webhook"])
logger = logging.getLogger("einsatzleiter.mail.inbound")
_MAX_PAYLOAD_BYTES = 256 * 1024


async def _verified_payload(request: Request, secret_enc: str):
    svix_id = request.headers.get("svix-id")
    timestamp = request.headers.get("svix-timestamp")
    signature = request.headers.get("svix-signature")
    if not svix_id or not timestamp or not signature:
        return None, JSONResponse({"detail": "Svix-Header fehlen"}, status_code=400)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_PAYLOAD_BYTES:
                return None, JSONResponse({"detail": "Payload zu groß"}, status_code=413)
        except ValueError:
            return None, JSONResponse({"detail": "Ungültige Content-Length"}, status_code=400)
    body = await request.body()
    if len(body) > _MAX_PAYLOAD_BYTES:
        return None, JSONResponse({"detail": "Payload zu groß"}, status_code=413)
    try:
        secret = decrypt_secret(secret_enc)
    except Exception:
        return None, JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
    if not verify_resend_webhook_signature(secret, body, svix_id, timestamp, signature):
        return None, JSONResponse({"detail": "Ungültige Signatur"}, status_code=401)
    try:
        return json.loads(body), None
    except (ValueError, UnicodeDecodeError):
        return {}, None


def _empfaenger(data: dict) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in ("received_for", "to", "cc"):
        values = data.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            address = value.strip().lower()[:500]
            if address and address not in seen:
                seen.add(address)
                result.append(address)
                if len(result) == 20:
                    return result
    return result


def _unter_stundenlimit(db: Session, org_id: int) -> bool:
    setting = (db.query(OrgSettings).execution_options(include_all_tenants=True)
               .filter(OrgSettings.org_id == org_id).first())
    limit = int(setting.mail_eingang_max_pro_stunde) if setting else 30
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    count = (db.query(func.count(OrgMailEingang.id)).execution_options(include_all_tenants=True)
             .filter(OrgMailEingang.org_id == org_id, OrgMailEingang.empfangen_at >= since).scalar() or 0)
    return count < max(1, limit)


@router.post("/mail/webhook/resend-inbound")
@(_limiter.limit("600/minute") if _limiter else lambda f: f)
async def resend_system_inbound_webhook(
    request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    secret_row = db.get(SystemSettings, "resend_inbound_webhook_secret")
    if not secret_row or not secret_row.value:
        return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
    payload, error = await _verified_payload(request, secret_row.value)
    if error:
        return error
    if not isinstance(payload, dict) or payload.get("type") != "email.received":
        return {"ok": True}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {"ok": True}
    # Systemweite Weiterleitungen sind absichtlich vor jeder Org-Aufloesung:
    # eine konsumierte Nachricht darf keinen Tenant-Datensatz erzeugen.
    from app.services.mail_weiterleitung_loop import run_mail_weiterleitung_once
    from app.services.mail_weiterleitung_service import finde_regel, stelle_in_warteschlange
    domain = inbound_domain(db)
    regel = finde_regel(db, _empfaenger(data), domain)
    if regel is not None:
        log_id = stelle_in_warteschlange(db, regel, data)
        if log_id is not None:
            background_tasks.add_task(run_mail_weiterleitung_once, log_id)
        return {"ok": True}
    org_ids: set[int] = set()
    for adresse in _empfaenger(data):
        org = org_aus_adresse(db, adresse, domain)
        if org and org.id not in org_ids and mail_eingang_aktiv(db, org.id):
            org_ids.add(org.id)
    rows = []
    for org_id in org_ids:
        if not _unter_stundenlimit(db, org_id):
            logger.warning("Resend-Posteingang Stundenlimit erreicht (org=%s)", org_id)
            continue
        try:
            with db.begin_nested():
                row = create_inbound_metadata(db, org_id, data)
        except IntegrityError:
            # UniqueConstraint (Org, Resend-ID): paralleler Retry ist harmlos.
            row = None
        if row:
            rows.append((org_id, row.id))
    db.commit()
    for org_id, row_id in rows:
        background_tasks.add_task(fetch_inbound_background, org_id, row_id)
    return {"ok": True}


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
