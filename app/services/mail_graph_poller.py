"""Lesender Microsoft-Graph-Abruf für den organisationsbezogenen Mail-Posteingang."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx

from app.services.loop_utils import iteration_watch
from app.services.o365_mail_service import GRAPH_BASE_URL, _acquire_token

logger = logging.getLogger("einsatzleiter.mail_graph_poller")
_TICK_INTERVAL_S = 15
_ORG_INTERVAL_S = 60
_MAX_PER_RUN = 50
_last_started_at: dict[int, float] = {}


class _GraphError(RuntimeError):
    def __init__(self, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(str(status_code or "network"))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _received(value: str | None) -> datetime:
    if not value:
        return _now()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _error_text(exc: Exception) -> str:
    status = exc.status_code if isinstance(exc, _GraphError) else None
    if status == 401:
        return "Anmeldung bei Microsoft fehlgeschlagen"
    if status == 403:
        return "Zugriff verweigert: Application-Permission Mail.Read fehlt oder per ApplicationAccessPolicy gesperrt"
    if status == 404:
        return "Postfach nicht gefunden"
    if isinstance(exc, (httpx.HTTPError, OSError, TimeoutError)):
        return "Microsoft Graph nicht erreichbar"
    return "Abruf über Office 365 fehlgeschlagen"


def _config(org_id: int) -> dict[str, Any] | None:
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgO365MailConfig
    from app.services.mail_service import _looks_like_email

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        postfach = ((cfg.read_postfach or cfg.sender_address or "").strip() if cfg else "")
        if not cfg or not cfg.read_enabled or not cfg.tenant_id or not cfg.client_id or not cfg.client_secret_enc:
            return None
        if org_settings is None or org_settings.mail_eingang_modus == "aus" or not _looks_like_email(postfach):
            return None
        return {
            "tenant_id": cfg.tenant_id.strip(), "client_id": cfg.client_id.strip(),
            "client_secret": decrypt_secret(cfg.client_secret_enc), "postfach": postfach,
            "read_seit": cfg.read_seit, "read_aktiv_seit": cfg.read_aktiv_seit,
        }
    finally:
        db.close()


async def _request(client: httpx.AsyncClient, method: str, url: str, token_args: dict[str, Any], **kwargs: Any) -> Any:
    token = await _acquire_token(**token_args)
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.request(method, url, headers=headers, **kwargs)
    if response.status_code in (401, 403):
        token = await _acquire_token(**token_args, force=True)
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.request(method, url, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise _GraphError(response.status_code)
    return response


async def _mime(client: httpx.AsyncClient, url: str, token_args: dict[str, Any]) -> tuple[bytes, bool]:
    from app.services.mail_eingang_anhang import MAX_MAIL_BYTES

    token = await _acquire_token(**token_args)
    for retry in range(2):
        headers = {"Authorization": f"Bearer {token}"}
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code in (401, 403) and retry == 0:
                token = await _acquire_token(**token_args, force=True)
                continue
            if response.status_code >= 400:
                raise _GraphError(response.status_code)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_MAIL_BYTES:
                    return b"", True
                chunks.append(chunk)
            return b"".join(chunks), False
    raise _GraphError(401)


async def _save_one(org_id: int, item: dict[str, Any], raw: bytes, too_large: bool) -> bool:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_mail import OrgMailEingang, OrgO365MailConfig
    from app.services.mail_eingang_anhang import speichere_lokal
    from app.services.mail_eingang_service import ereignis, normalisiere_mime, speichere_eingang
    from app.services.mail_zuordnung_service import ordne_zu

    db = SessionLocal()
    set_tenant_context(db, None)
    received = _received(item.get("receivedDateTime"))
    external_id = str(item.get("id") or "")[:255]
    try:
        cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
        if cfg is None:
            return False
        try:
            if too_large:
                sender = ((item.get("from") or {}).get("emailAddress") or {}).get("address") or ""
                row = speichere_eingang(
                    db, org_id, quelle="o365", extern_id=external_id, absender=sender,
                    betreff=item.get("subject") or "", empfaenger=[], cc=None,
                    message_id=item.get("internetMessageId"), empfangen_at=received,
                    groesse_bytes=None, abgerufen=False,
                )
                if row is not None:
                    row.status, row.zuordnung_grund = "posteingang", "Mail größer als 35 MB, Inhalt nicht übernommen"
                    ereignis(db, row, "zu_gross", row.zuordnung_grund)
            else:
                parsed = normalisiere_mime(raw)
                auth = parsed["auth"]
                row = speichere_eingang(
                    db, org_id, quelle="o365", extern_id=external_id, absender=parsed["absender"],
                    betreff=parsed["betreff"], empfaenger=parsed["empfaenger"], cc=parsed["cc"],
                    message_id=parsed["message_id"] or item.get("internetMessageId"),
                    empfangen_at=parsed["datum"] or received, text_body=parsed["text"], html_body=parsed["html"],
                    header_json=parsed["header"], spf=auth["spf"], dkim=auth["dkim"], dmarc=auth["dmarc"],
                    abgerufen=True,
                )
                if row is not None:
                    speichere_lokal(db, row, parsed["anhaenge"])
                    ereignis(db, row, "abgerufen", "Inhalt über Office 365 abgerufen")
            if row is not None:
                db.commit()
                if not too_large:
                    await ordne_zu(db, row.id)
                    db.refresh(row)
                    if row.status != "abruf_fehler":
                        from app.services.print_dispatcher import autoprint_mail_eingang_background

                        try:
                            await autoprint_mail_eingang_background(row.id)
                        except Exception:
                            logger.exception("Graph-Autodruck fehlgeschlagen (org=%s, mail=%s)", org_id, row.id)
            cfg.read_seit, cfg.read_letzter_abruf_at, cfg.read_letzter_fehler = received, _now(), None
            db.commit()
            return row is not None
        except Exception:
            db.rollback()
            cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
            if cfg is not None:
                failed = OrgMailEingang(
                    org_id=org_id, quelle="o365", extern_id=external_id, absender="", empfaenger="[]",
                    betreff=item.get("subject") or "", empfangen_at=received, status="abruf_fehler",
                )
                db.add(failed)
                db.flush()
                ereignis(db, failed, "abruf_fehler", "Mail konnte nicht verarbeitet werden")
                cfg.read_seit, cfg.read_letzter_abruf_at = received, _now()
                db.commit()
            return False
    finally:
        db.close()


def _voruebergehend(exc: Exception) -> bool:
    if isinstance(exc, _GraphError):
        return exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, (httpx.HTTPError, OSError, TimeoutError))


def _bekannt(org_id: int, extern_id: str) -> bool:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_mail import OrgMailEingang

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(OrgMailEingang.id).filter(
            OrgMailEingang.org_id == org_id, OrgMailEingang.quelle == "o365", OrgMailEingang.extern_id == extern_id,
        ).first() is not None
    finally:
        db.close()


async def abrufen_fuer_org(org_id: int) -> int:
    from app.config import settings
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgO365MailConfig
    from app.services.mail_eingang_service import unter_stundenlimit

    if not settings.O365_MAIL_ENABLED or not settings.MAIL_GRAPH_POLL_ENABLED:
        return 0
    config = await asyncio.to_thread(_config, org_id)
    if config is None:
        return 0
    since = config["read_seit"] or (config["read_aktiv_seit"] or _now()) - timedelta(hours=24)
    token_args: dict[str, Any] = {
        key: config[key] for key in ("org_id", "tenant_id", "client_id", "client_secret") if key in config
    }
    token_args["org_id"] = org_id
    inbox_url = f"{GRAPH_BASE_URL}/users/{quote(config['postfach'], safe='')}/mailFolders/inbox/messages"
    params = {
        "$filter": f"receivedDateTime ge {_iso(since)}", "$orderby": "receivedDateTime asc", "$top": 25,
        "$select": "id,internetMessageId,receivedDateTime,subject,from",
    }
    saved = 0
    processed = 0
    try:
        async with httpx.AsyncClient(timeout=settings.O365_MAIL_HTTP_TIMEOUT) as client:
            next_url: str | None = inbox_url
            next_params: dict[str, Any] | None = params
            while next_url and processed < _MAX_PER_RUN:
                response = await _request(client, "GET", next_url, token_args, params=next_params)
                data = response.json()
                for item in data.get("value", []):
                    if processed >= _MAX_PER_RUN:
                        break
                    db = SessionLocal()
                    set_tenant_context(db, None)
                    try:
                        setting = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
                        if setting is None or setting.mail_eingang_modus == "aus" or not unter_stundenlimit(db, org_id):
                            return saved
                    finally:
                        db.close()
                    processed += 1
                    # Der Filter "ge <Marke>" liefert die zuletzt verarbeitete Mail erneut; bekannte Mails
                    # nicht noch einmal herunterladen (bis zu 35 MB je Lauf).
                    if await asyncio.to_thread(_bekannt, org_id, str(item.get("id") or "")[:255]):
                        continue
                    user_url = f"{GRAPH_BASE_URL}/users/{quote(config['postfach'], safe='')}"
                    message_id = quote(str(item.get("id") or ""), safe="")
                    message_url = f"{user_url}/messages/{message_id}/$value"
                    try:
                        raw, too_large = await _mime(client, message_url, token_args)
                    except Exception as exc:
                        if _voruebergehend(exc):
                            # Lauf abbrechen, Marke bleibt stehen: der nächste Lauf versucht es erneut.
                            raise
                        await _save_one(org_id, item, b"", False)
                    else:
                        saved += await _save_one(org_id, item, raw, too_large)
                candidate = data.get("@odata.nextLink")
                next_url = candidate if isinstance(candidate, str) and candidate.startswith(GRAPH_BASE_URL) else None
                next_params = None
    except Exception as exc:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
            if cfg is not None:
                cfg.read_letzter_fehler = _error_text(exc)
                db.commit()
        finally:
            db.close()
        logger.warning("Graph-Abruf fehlgeschlagen (org=%s): %s", org_id, _error_text(exc))
    return saved


def _eligible_org_ids() -> list[int]:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgO365MailConfig
    from app.services.mail_service import _looks_like_email

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(OrgO365MailConfig, OrgSettings).join(
            OrgSettings, OrgSettings.org_id == OrgO365MailConfig.org_id,
        ).filter(
            OrgO365MailConfig.read_enabled.is_(True), OrgO365MailConfig.tenant_id.is_not(None),
            OrgO365MailConfig.client_id.is_not(None), OrgO365MailConfig.client_secret_enc.is_not(None),
            OrgSettings.mail_eingang_modus != "aus",
        ).all()
        return [
            cfg.org_id for cfg, _ in rows
            if _looks_like_email((cfg.read_postfach or cfg.sender_address or "").strip())
        ]
    finally:
        db.close()


async def mail_graph_poll_loop() -> None:
    from app.config import settings

    if not settings.O365_MAIL_ENABLED or not settings.MAIL_GRAPH_POLL_ENABLED:
        return
    while True:
        started = monotonic()
        try:
            with iteration_watch(logger, "mail_graph_poll_loop", _TICK_INTERVAL_S):
                for org_id in await asyncio.to_thread(_eligible_org_ids):
                    if monotonic() - _last_started_at.get(org_id, float("-inf")) < _ORG_INTERVAL_S:
                        continue
                    _last_started_at[org_id] = monotonic()
                    try:
                        await abrufen_fuer_org(org_id)
                    except Exception:
                        logger.exception("Graph-Poller: Org %s fehlgeschlagen", org_id)
        except Exception:
            logger.exception("Graph-Poller: Iteration fehlgeschlagen")
        await asyncio.sleep(max(0.0, _TICK_INTERVAL_S - (monotonic() - started)))


async def verbindung_testen(cfg: Any) -> tuple[bool, str]:
    from app.config import settings
    from app.core.crypto import decrypt_secret

    tenant_id, client_id = (cfg.tenant_id or "").strip(), (cfg.client_id or "").strip()
    postfach = (cfg.read_postfach or cfg.sender_address or "").strip()
    try:
        secret = decrypt_secret(cfg.client_secret_enc)
        token_args = {"org_id": cfg.org_id, "tenant_id": tenant_id, "client_id": client_id, "client_secret": secret}
        url = f"{GRAPH_BASE_URL}/users/{quote(postfach, safe='')}/mailFolders/inbox"
        async with httpx.AsyncClient(timeout=settings.O365_MAIL_HTTP_TIMEOUT) as client:
            response = await _request(client, "GET", url, token_args, params={"$select": "totalItemCount"})
        count = int(response.json().get("totalItemCount", 0))
        return True, f"Verbindung erfolgreich, {count} Nachrichten im Posteingang"
    except Exception as exc:
        return False, _error_text(exc)
