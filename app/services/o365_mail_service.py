"""Office 365 / Microsoft Graph Mailversand (App-only, Client-Credentials-OAuth).

Reine Graph-Mechanik: Token-Erwerb (client_credentials) + `POST /users/{sender}/
sendMail`. Provider-agnostischer Aufrufer ist mail_service.py::deliver() — diese
Datei kennt weder SMTP noch Fallback-Logik.

Kein Lese-/Polling-Code enthalten: das Abholen eingehender Mails aus demselben
Postfach ist in app/models/org_mail.py (OrgO365MailConfig.read_enabled) nur als
Konfigurationsfeld vorbereitet, hier bewusst noch nicht implementiert.

Betriebsvoraussetzung (Azure, kein Code): App-Registrierung mit Application
permission "Mail.Send" (admin-consented) auf dem angegebenen tenant_id/client_id.
"""
from __future__ import annotations

import logging
import re
import time
from email.message import EmailMessage
from typing import TYPE_CHECKING

import httpx

from app.config import settings

if TYPE_CHECKING:
    from app.models.org_mail import OrgO365MailConfig

logger = logging.getLogger("einsatzleiter.mail.o365")

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class O365MailError(RuntimeError):
    """O365/Graph-Mailversand fehlgeschlagen (Token-Erwerb oder sendMail)."""


# ── In-memory Token-Cache (kein Redis/DB nötig, gleiches Muster wie
#    sso_service._jwks_cache) — keyed by org_id, da Client-Credentials-Token pro
#    Org/App-Registrierung unterschiedlich sind. ──────────────────────────────
_token_cache: dict[int, tuple[str, float]] = {}

_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _validate_ids(tenant_id: str, client_id: str) -> None:
    """tenant_id/client_id landen in einer URL, die wir POSTen — vor jedem Request
    prüfen (SSRF-/Injection-Schutz, analog sso_service.validate_authority_base())."""
    if not _ID_RE.match(tenant_id or ""):
        raise O365MailError(f"Ungültige tenant_id: {tenant_id!r}")
    if not _ID_RE.match(client_id or ""):
        raise O365MailError(f"Ungültige client_id: {client_id!r}")


async def _acquire_token(
    *, org_id: int, tenant_id: str, client_id: str, client_secret: str, force: bool = False,
) -> str:
    """Client-Credentials-Token für Microsoft Graph, gecached mit Sicherheitsmarge
    vor Ablauf (O365_MAIL_TOKEN_MARGIN_S)."""
    now = time.time()
    if not force:
        cached = _token_cache.get(org_id)
        if cached and (cached[1] - now) > settings.O365_MAIL_TOKEN_MARGIN_S:
            return cached[0]

    token_url = f"{settings.MS_LOGIN_BASE_URL.rstrip('/')}/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=settings.O365_MAIL_HTTP_TIMEOUT) as client:
        resp = await client.post(token_url, data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
        })
    if resp.status_code != 200:
        try:
            err_body = resp.json()
            err_desc = err_body.get("error_description") or err_body.get("error") or resp.text[:400]
        except Exception:
            err_desc = resp.text[:400]
        logger.error("O365-Token-Erwerb fehlgeschlagen (Org %s): HTTP %s | %s",
                     org_id, resp.status_code, err_desc)
        raise O365MailError(f"Token-Erwerb fehlgeschlagen: HTTP {resp.status_code}: {err_desc}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise O365MailError("Token-Antwort ohne access_token")
    expires_in = float(data.get("expires_in", 3600))
    _token_cache[org_id] = (token, now + expires_in)
    return token


def _graph_payload(msg: EmailMessage) -> dict:
    """Konvertiert die providerneutrale EmailMessage in das Graph sendMail-Schema.

    Graph kennt nur EIN body.contentType (Text ODER HTML, kein multipart/
    alternative wie EmailMessage) — HTML wird bevorzugt, wenn vorhanden. Bewusst
    KEIN "from"/"sender" im Payload: Graph versendet immer als das Postfach aus
    der URL (/users/{sender_address}/sendMail); ein abweichendes "from" würde
    abgelehnt.
    """
    body_part = msg.get_body(preferencelist=("html", "plain"))
    if body_part is not None:
        content_type = "HTML" if body_part.get_content_type() == "text/html" else "Text"
        content = body_part.get_content()
    else:
        content_type = "Text"
        content = ""

    to_raw = msg["To"] or ""
    recipients = [addr.strip() for addr in re.split(r"[,;]", to_raw) if addr.strip()]

    return {
        "message": {
            "subject": msg["Subject"] or "",
            "body": {"contentType": content_type, "content": content},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in recipients],
        },
        "saveToSentItems": "false",
    }


async def send_via_graph(msg: EmailMessage, cfg: OrgO365MailConfig) -> None:
    """Versendet eine bereits gebaute EmailMessage über Microsoft Graph. Wirft
    O365MailError bei jedem Fehlschlag (Konfiguration, Token, Versand) — der
    Aufrufer (mail_service.deliver()) fängt das ab und fällt auf SMTP zurück."""
    from app.core.crypto import decrypt_secret

    tenant_id = (cfg.tenant_id or "").strip()
    client_id = (cfg.client_id or "").strip()
    sender_address = (cfg.sender_address or "").strip()
    if not (tenant_id and client_id and cfg.client_secret_enc and sender_address):
        raise O365MailError("O365-Konfiguration unvollständig")
    _validate_ids(tenant_id, client_id)

    try:
        client_secret = decrypt_secret(cfg.client_secret_enc)
    except Exception as exc:
        raise O365MailError(f"client_secret konnte nicht entschlüsselt werden: {exc}") from exc

    payload = _graph_payload(msg)
    send_url = f"{GRAPH_BASE_URL}/users/{sender_address}/sendMail"

    async def _attempt(token: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=settings.O365_MAIL_HTTP_TIMEOUT) as client:
            return await client.post(
                send_url, headers={"Authorization": f"Bearer {token}"}, json=payload,
            )

    token = await _acquire_token(
        org_id=cfg.org_id, tenant_id=tenant_id, client_id=client_id, client_secret=client_secret,
    )
    resp = await _attempt(token)
    if resp.status_code in (401, 403):
        # Möglicherweise abgelaufener/rotierter Secret — einmal erzwungen neu holen und retry.
        token = await _acquire_token(
            org_id=cfg.org_id, tenant_id=tenant_id, client_id=client_id,
            client_secret=client_secret, force=True,
        )
        resp = await _attempt(token)

    if resp.status_code != 202:
        try:
            err_body = resp.json()
            err_desc = (err_body.get("error") or {}).get("message") or resp.text[:400]
        except Exception:
            err_desc = resp.text[:400]
        logger.error("O365-Mailversand fehlgeschlagen (Org %s): HTTP %s | %s",
                     cfg.org_id, resp.status_code, err_desc)
        raise O365MailError(f"HTTP {resp.status_code}: {err_desc}")
