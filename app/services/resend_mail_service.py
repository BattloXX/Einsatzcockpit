"""Mailversand ueber die Resend-HTTP-API."""

from __future__ import annotations

import logging
import re
from email.message import EmailMessage

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.mail.resend")
RESEND_EMAILS_URL = "https://api.resend.com/emails"


class ResendMailError(RuntimeError):
    """Resend-Mailversand fehlgeschlagen."""


def _mark_test_system_from_addr(from_addr: str) -> str:
    """Markiert die Absenderadresse auf dem Testsystem (TEST_SYSTEM=true) deutlich
    als Test, damit Empfänger echte und Testsystem-Mails nicht verwechseln."""
    if not settings.TEST_SYSTEM or "@" not in from_addr:
        return from_addr
    local, domain = from_addr.split("@", 1)
    return f"Einsatzcockpit (Testsystem) <test-{local}@{domain}>"


def _resend_payload(msg: EmailMessage, from_addr: str, attachments: list[dict] | None = None) -> dict:
    """Konvertiert eine EmailMessage in das Resend-Schema."""
    to_raw = msg["To"] or ""
    recipients = [addr.strip() for addr in re.split(r"[,;]", to_raw) if addr.strip()]
    payload: dict = {
        "from": from_addr,
        "to": recipients,
        "subject": msg["Subject"] or "",
    }
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    if plain is not None and plain.get_content_type() == "text/plain":
        payload["text"] = plain.get_content()
    if html is not None and html.get_content_type() == "text/html":
        payload["html"] = html.get_content()
    if "text" not in payload and "html" not in payload:
        payload["text"] = ""
    if attachments:
        payload["attachments"] = attachments
    headers = {name: str(msg[name]) for name in ("List-Unsubscribe", "List-Unsubscribe-Post") if msg[name]}
    if headers:
        payload["headers"] = headers
    return payload


async def send_via_resend(
    msg: EmailMessage, api_key: str, from_addr: str, attachments: list[dict] | None = None
) -> str | None:
    """Versendet eine Mail ueber Resend und wirft bei Fehlern ResendMailError."""
    api_key = (api_key or "").strip()
    from_addr = (from_addr or "").strip()
    if not api_key or not from_addr:
        raise ResendMailError("Resend-Konfiguration unvollstaendig")
    from_addr = _mark_test_system_from_addr(from_addr)
    async with httpx.AsyncClient(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
        resp = await client.post(
            RESEND_EMAILS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_resend_payload(msg, from_addr, attachments),
        )
    if not 200 <= resp.status_code < 300:
        try:
            err_body = resp.json()
            err_desc = err_body.get("message") or resp.text[:400]
        except Exception:
            err_desc = resp.text[:400]
        logger.error("Resend-Mailversand fehlgeschlagen: HTTP %s | %s", resp.status_code, err_desc)
        raise ResendMailError(f"HTTP {resp.status_code}: {err_desc}")
    try:
        value = resp.json().get("id")
        return str(value) if value is not None else None
    except Exception:
        return None
