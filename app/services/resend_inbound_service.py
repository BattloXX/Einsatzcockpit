"""Sicherer Abruf und Aufbewahrung des Resend-Posteingangs."""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import decrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.org_mail import OrgMailEingang, OrgResendConfig

logger = logging.getLogger("einsatzleiter.mail.inbound")
RESEND_RECEIVING_URL = "https://api.resend.com/emails/receiving/"
RESEND_ATTACHMENTS_URL = "https://api.resend.com/emails/receiving/{email_id}/attachments"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: object, maximum: int) -> str:
    return _CONTROL.sub("", str(value or "").replace("\r", " ").replace("\n", " "))[:maximum]


def _json(value: object, maximum: int = 100_000) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)[:maximum]


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.now(UTC).replace(tzinfo=None)


def create_inbound_metadata(db: Session, org_id: int, data: dict) -> OrgMailEingang | None:
    email_id = clean_text(data.get("email_id"), 64)
    if not email_id:
        return None
    exists = (db.query(OrgMailEingang).execution_options(include_all_tenants=True)
              .filter(OrgMailEingang.org_id == org_id, OrgMailEingang.resend_email_id == email_id).first())
    if exists:
        return None
    row = OrgMailEingang(org_id=org_id, resend_email_id=email_id,
        message_id=clean_text(data.get("message_id"), 500) or None,
        absender=clean_text(data.get("from"), 500), empfaenger=_json(data.get("to")),
        cc=_json(data.get("cc")) if data.get("cc") else None,
        betreff=clean_text(data.get("subject"), 998), empfangen_at=_utc(data.get("created_at")),
        anhang_json=_json(_attachment_metadata(data.get("attachments") or [])), status="neu")
    db.add(row)
    db.flush()
    return row


def _attachment_metadata(items: object) -> list[dict]:
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            keys = ("id", "filename", "content_type", "size", "content_disposition", "content_id")
            out.append({key: item.get(key) for key in keys})
    return out


async def fetch_inbound_message(db: Session, org_id: int, message_id: int) -> None:
    """Lädt Mailinhalt nach dem Commit; Fehler werden bewusst nur generisch persistiert."""
    row = (db.query(OrgMailEingang).execution_options(include_all_tenants=True)
           .filter(OrgMailEingang.id == message_id, OrgMailEingang.org_id == org_id).first())
    cfg = (db.query(OrgResendConfig).execution_options(include_all_tenants=True)
           .filter(OrgResendConfig.org_id == org_id).first())
    if not row or not cfg or not cfg.api_key_enc:
        return
    try:
        api_key = decrypt_secret(cfg.api_key_enc)
        async with httpx.AsyncClient(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
            response = await client.get(RESEND_RECEIVING_URL + row.resend_email_id,
                headers={"Authorization": f"Bearer {api_key}"})
        if not response.is_success:
            raise RuntimeError("provider status")
        payload = response.json()
        row.message_id = clean_text(payload.get("message_id"), 500) or row.message_id
        row.absender = clean_text(payload.get("from"), 500)
        row.empfaenger = _json(payload.get("to"))
        row.cc = _json(payload.get("cc")) if payload.get("cc") else None
        row.betreff = clean_text(payload.get("subject"), 998)
        row.text_body = str(payload.get("text") or "")[:1_000_000]
        row.html_body = str(payload.get("html") or "")[:2_000_000]
        row.header_json = _json(payload.get("headers"), 200_000) if payload.get("headers") else None
        for name in ("spf", "dkim", "dmarc"):
            result = payload.get(name)
            setattr(row, name, clean_text(result.get("status") if isinstance(result, dict) else result, 20) or None)
        row.anhang_json = _json(_attachment_metadata(payload.get("attachments") or []))
        row.abgerufen_at, row.status, row.fehler = datetime.now(UTC).replace(tzinfo=None), "neu", None
    except Exception:  # Providerdetails und Schlüssel niemals loggen/persistieren.
        row.status, row.fehler = "abruf_fehler", "Inhalt konnte nicht von Resend abgerufen werden."
        logger.warning("Resend-Posteingang konnte nicht abgerufen werden (org=%s, mail=%s)", org_id, message_id)
    db.commit()


async def fetch_inbound_background(org_id: int, message_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        await fetch_inbound_message(db, org_id, message_id)
    finally:
        db.close()


def loesche_alte_eingaenge(db: Session) -> int:
    """Entfernt Einträge objektweise, damit Tenant-Scoping bei Mutationen greift."""
    now = datetime.now(UTC).replace(tzinfo=None)
    configs = db.query(OrgResendConfig).execution_options(include_all_tenants=True).all()
    count = 0
    for cfg in configs:
        days = max(1, min(int(cfg.inbound_retention_days or 90), 365))
        rows = (db.query(OrgMailEingang).execution_options(include_all_tenants=True)
                .filter(OrgMailEingang.org_id == cfg.org_id)
                .filter(OrgMailEingang.empfangen_at < now - timedelta(days=days)).all())
        for row in rows:
            db.delete(row)
            count += 1
    db.commit()
    return count


async def resend_inbound_retention_loop() -> None:
    import asyncio
    while True:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            loesche_alte_eingaenge(db)
        except Exception:
            logger.exception("Aufbewahrung Resend-Posteingang fehlgeschlagen")
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(24 * 60 * 60)


def safe_attachment_url(url: str, *, from_resend_api: bool = False) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme != "https" or hostname is None or parsed.username or parsed.password:
        return False
    hostname = hostname.lower().rstrip(".")
    return (
        hostname == "resend.com"
        or hostname.endswith(".resend.com")
        or hostname.endswith(".resend.app")
        or (from_resend_api and hostname.endswith(".amazonaws.com"))
    )


def safe_attachment_filename(value: object) -> str:
    """Ein Dateiname, nie ein Pfad oder ein Header-Fragment."""
    name = _CONTROL.sub("", str(value or "")).replace("/", "_").replace("\\", "_")
    name = name.replace('"', "_").replace("'", "_").strip(" .")
    return name[:180] or "anhang"


def attachment_content_type(value: object) -> str:
    allowed = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}
    content_type = str(value or "").lower().split(";", 1)[0].strip()
    return content_type if content_type in allowed else "application/octet-stream"


async def get_resend_attachment(api_key: str, email_id: str, attachment_id: str) -> dict | None:
    """Findet einen Anhang ausschließlich in Resends paginierter API-Antwort."""
    after: str | None = None
    async with httpx.AsyncClient(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
        for _ in range(100):  # harte Schranke gegen fehlerhafte Pagination
            params: dict[str, str | int] = {"limit": 100}
            if after:
                params["after"] = after
            response = await client.get(
                RESEND_ATTACHMENTS_URL.format(email_id=email_id),
                params=params,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if not response.is_success:
                return None
            payload = response.json()
            items = payload.get("data", payload.get("attachments", [])) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                return None
            for item in items:
                if isinstance(item, dict) and clean_text(item.get("id"), 200) == attachment_id:
                    url = item.get("download_url")
                    if isinstance(url, str) and safe_attachment_url(url, from_resend_api=True):
                        return item
                    return None
            after = payload.get("next_after") or payload.get("after") if isinstance(payload, dict) else None
            if not after or len(items) < 100:
                return None
    return None
