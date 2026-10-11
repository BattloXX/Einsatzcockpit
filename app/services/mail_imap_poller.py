"""Lesender IMAP-Abruf für den organisationsbezogenen Mail-Posteingang."""
from __future__ import annotations

import asyncio
import imaplib
import logging
import ssl
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any

from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.mail_imap_poller")
_TICK_INTERVAL_S = 15
_ORG_INTERVAL_S = 60
_MAX_PER_RUN = 50
_last_started_at: dict[int, float] = {}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _imap_date(value: datetime) -> str:
    return value.strftime("%d-%b-%Y")


def _decode_values(data: Any) -> list[bytes]:
    if not isinstance(data, (list, tuple)):
        return []
    return [item for item in data if isinstance(item, bytes)]


def _uids(data: Any) -> list[int]:
    values = _decode_values(data)
    result: list[int] = []
    for value in values:
        for item in value.split():
            try:
                result.append(int(item))
            except ValueError:
                continue
    return result


def _internaldate(data: Any) -> datetime | None:
    for item in data if isinstance(data, (list, tuple)) else []:
        if not isinstance(item, tuple) or not item:
            continue
        text = item[0].decode("ascii", "ignore") if isinstance(item[0], bytes) else str(item[0])
        marker = 'INTERNALDATE "'
        if marker not in text:
            continue
        raw = text.split(marker, 1)[1].split('"', 1)[0]
        try:
            value = parsedate_to_datetime(raw)
            return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
        except (TypeError, ValueError, IndexError):
            continue
    return None


def _size(data: Any) -> int | None:
    import re

    for item in data if isinstance(data, (list, tuple)) else []:
        if not isinstance(item, tuple) or not item:
            continue
        text = item[0].decode("ascii", "ignore") if isinstance(item[0], bytes) else str(item[0])
        match = re.search(r"RFC822\.SIZE (\d+)", text)
        if match:
            return int(match.group(1))
    return None


def _raw(data: Any) -> bytes:
    for item in data if isinstance(data, (list, tuple)) else []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _uidvalidity(client: Any) -> int:
    response = client.response("UIDVALIDITY")
    values = _decode_values(response[1] if isinstance(response, tuple) and len(response) > 1 else response)
    if not values:
        values = _decode_values(getattr(client, "untagged_responses", {}).get("UIDVALIDITY"))
    for value in values:
        try:
            return int(value)
        except ValueError:
            continue
    raise RuntimeError("UIDVALIDITY fehlt")


def _fetch_messages(config: dict[str, Any], since: datetime, initial: bool) -> tuple[int, list[dict[str, Any]]]:
    """Führt nur lesende IMAP-Kommandos aus und gibt rohe Mails zurück."""
    from app.config import settings
    from app.services.mail_eingang_anhang import MAX_MAIL_BYTES

    client: Any = None
    try:
        if config["imap_use_ssl"]:
            client = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"], timeout=settings.MAIL_IMAP_TIMEOUT_S)
        else:
            client = imaplib.IMAP4(config["imap_host"], config["imap_port"], timeout=settings.MAIL_IMAP_TIMEOUT_S)
            client.starttls(ssl_context=ssl.create_default_context())
        client.login(config["user"], config["password"])
        status, _ = client.select(config["imap_ordner"], readonly=True)
        if status != "OK":
            raise RuntimeError("Postfach konnte nicht geöffnet werden")
        uidvalidity = _uidvalidity(client)
        changed = config["imap_uidvalidity"] != uidvalidity
        use_initial = initial or changed
        if use_initial:
            status, data = client.uid("SEARCH", None, "SINCE", _imap_date(since))
        else:
            start = int(config["imap_letzte_uid"]) + 1
            status, data = client.uid("SEARCH", None, "UID", f"{start}:*")
        if status != "OK":
            raise RuntimeError("Suche im Postfach fehlgeschlagen")
        message_uids = sorted(set(_uids(data)))
        if not use_initial:
            message_uids = [uid for uid in message_uids if uid > int(config["imap_letzte_uid"])]
        result: list[dict[str, Any]] = []
        for uid in message_uids:
            if len(result) == _MAX_PER_RUN:
                break
            status, meta = client.uid("FETCH", str(uid), "(RFC822.SIZE INTERNALDATE)")
            if status != "OK":
                raise RuntimeError("Mail-Metadaten konnten nicht gelesen werden")
            received = _internaldate(meta)
            if use_initial and (received is None or received < since):
                continue
            size = _size(meta) or 0
            command = "(BODY.PEEK[HEADER])" if size > MAX_MAIL_BYTES else "(BODY.PEEK[])"
            status, content = client.uid("FETCH", str(uid), command)
            if status != "OK":
                raise RuntimeError("Mail-Inhalt konnte nicht gelesen werden")
            result.append({
                "uid": uid, "size": size, "received": received, "raw": _raw(content),
                "too_large": size > MAX_MAIL_BYTES,
            })
        return uidvalidity, result
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def _error_text(exc: Exception) -> str:
    imap_error = getattr(imaplib.IMAP4, "error", ())
    if isinstance(exc, imap_error):
        return "Anmeldung fehlgeschlagen"
    if isinstance(exc, TimeoutError):
        return "Zeitüberschreitung"
    if isinstance(exc, (OSError, ssl.SSLError)):
        return "Server nicht erreichbar"
    return "IMAP-Abruf fehlgeschlagen"


def _config(org_id: int) -> dict[str, Any] | None:
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgSmtpConfig

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == org_id).first()
        if not row or not (row.imap_enabled and row.imap_host and row.user and row.password_enc):
            return None
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if org_settings is None or org_settings.mail_eingang_modus == "aus":
            return None
        return {
            "imap_host": row.imap_host, "imap_port": row.imap_port, "imap_use_ssl": row.imap_use_ssl,
            "imap_ordner": row.imap_ordner or "INBOX", "imap_uidvalidity": row.imap_uidvalidity,
            "imap_letzte_uid": row.imap_letzte_uid, "imap_aktiv_seit": row.imap_aktiv_seit,
            "user": row.user, "password": decrypt_secret(row.password_enc),
        }
    finally:
        db.close()


async def _save_one(org_id: int, uidvalidity: int, item: dict[str, Any]) -> bool:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_mail import OrgMailEingang, OrgSmtpConfig
    from app.services.mail_eingang_anhang import speichere_lokal
    from app.services.mail_eingang_service import ereignis, normalisiere_mime, speichere_eingang
    from app.services.mail_zuordnung_service import ordne_zu

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == org_id).first()
        if cfg is None:
            return False
        row: OrgMailEingang | None = None
        try:
            parsed = normalisiere_mime(item["raw"])
            auth = parsed["auth"]
            row = speichere_eingang(
                db, org_id, quelle="imap", extern_id=f"{uidvalidity}:{item['uid']}",
                absender=parsed["absender"], betreff=parsed["betreff"], empfaenger=parsed["empfaenger"],
                cc=parsed["cc"], message_id=parsed["message_id"],
                empfangen_at=parsed["datum"] or item["received"] or _now(), text_body=parsed["text"],
                html_body=parsed["html"], header_json=parsed["header"], spf=auth["spf"], dkim=auth["dkim"],
                dmarc=auth["dmarc"], groesse_bytes=item["size"], abgerufen=not item["too_large"],
            )
            if row is not None:
                if item["too_large"]:
                    row.status, row.zuordnung_grund = "posteingang", "Mail größer als 35 MB, Inhalt nicht übernommen"
                    ereignis(db, row, "zu_gross", row.zuordnung_grund)
                else:
                    speichere_lokal(db, row, parsed["anhaenge"])
                    ereignis(db, row, "abgerufen", "Inhalt per IMAP abgerufen")
                db.commit()
                if not item["too_large"]:
                    await ordne_zu(db, row.id)
                    db.refresh(row)
                    if row.status != "abruf_fehler":
                        from app.services.print_dispatcher import autoprint_mail_eingang_background

                        try:
                            await autoprint_mail_eingang_background(row.id)
                        except Exception:
                            logger.exception("IMAP-Autodruck fehlgeschlagen (org=%s, mail=%s)", org_id, row.id)
            cfg.imap_uidvalidity, cfg.imap_letzte_uid = uidvalidity, item["uid"]
            cfg.imap_letzter_abruf_at, cfg.imap_letzter_fehler = _now(), None
            db.commit()
            return row is not None
        except Exception:
            db.rollback()
            cfg = db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == org_id).first()
            if cfg is not None:
                failed = OrgMailEingang(
                    org_id=org_id, quelle="imap", extern_id=f"{uidvalidity}:{item['uid']}", absender="",
                    empfaenger="[]", betreff="", empfangen_at=item["received"] or _now(), status="abruf_fehler",
                    groesse_bytes=item["size"],
                )
                db.add(failed)
                db.flush()
                ereignis(db, failed, "abruf_fehler", "Mail konnte nicht verarbeitet werden")
                cfg.imap_uidvalidity, cfg.imap_letzte_uid = uidvalidity, item["uid"]
                cfg.imap_letzter_abruf_at = _now()
                db.commit()
            return False
    finally:
        db.close()


async def abrufen_fuer_org(org_id: int) -> int:
    """Holt höchstens 50 neue Mails einer Organisation und gibt neue Einträge zurück."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgSmtpConfig
    from app.services.mail_eingang_service import unter_stundenlimit

    config = await asyncio.to_thread(_config, org_id)
    if config is None:
        return 0
    now = _now()
    since = (config["imap_aktiv_seit"] or now) - timedelta(hours=24)
    initial = config["imap_letzte_uid"] is None
    try:
        uidvalidity, items = await asyncio.to_thread(_fetch_messages, config, since, initial)
    except Exception as exc:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            cfg = db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == org_id).first()
            if cfg is not None:
                cfg.imap_letzter_fehler = _error_text(exc)
                db.commit()
        finally:
            db.close()
        logger.warning("IMAP-Abruf fehlgeschlagen (org=%s): %s", org_id, _error_text(exc))
        return 0
    saved = 0
    for item in items:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
            if settings_row is None or settings_row.mail_eingang_modus == "aus" or not unter_stundenlimit(db, org_id):
                break
        finally:
            db.close()
        saved += await _save_one(org_id, uidvalidity, item)
    return saved


def _eligible_org_ids() -> list[int]:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgSmtpConfig

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(OrgSmtpConfig.org_id).join(OrgSettings, OrgSettings.org_id == OrgSmtpConfig.org_id).filter(
            OrgSmtpConfig.imap_enabled.is_(True), OrgSmtpConfig.imap_host.is_not(None),
            OrgSmtpConfig.user.is_not(None), OrgSmtpConfig.password_enc.is_not(None),
            OrgSettings.mail_eingang_modus != "aus",
        ).all()
        return [org_id for (org_id,) in rows]
    finally:
        db.close()


async def mail_imap_poll_loop() -> None:
    from app.config import settings

    if not settings.MAIL_IMAP_POLL_ENABLED:
        return
    while True:
        started = monotonic()
        try:
            with iteration_watch(logger, "mail_imap_poll_loop", _TICK_INTERVAL_S):
                for org_id in await asyncio.to_thread(_eligible_org_ids):
                    if monotonic() - _last_started_at.get(org_id, float("-inf")) < _ORG_INTERVAL_S:
                        continue
                    _last_started_at[org_id] = monotonic()
                    try:
                        await abrufen_fuer_org(org_id)
                    except Exception:
                        logger.exception("IMAP-Poller: Org %s fehlgeschlagen", org_id)
        except Exception:
            logger.exception("IMAP-Poller: Iteration fehlgeschlagen")
        await asyncio.sleep(max(0.0, _TICK_INTERVAL_S - (monotonic() - started)))


def verbindung_testen(cfg_dict: dict[str, Any]) -> tuple[bool, str]:
    """Prüft Login und lesenden Postfachzugriff ohne Daten zu speichern."""
    config = dict(cfg_dict)
    try:
        client: Any
        if config.get("imap_use_ssl", True):
            client = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"], timeout=config.get("timeout", 20))
        else:
            client = imaplib.IMAP4(config["imap_host"], config["imap_port"], timeout=config.get("timeout", 20))
            client.starttls(ssl_context=ssl.create_default_context())
        try:
            client.login(config["user"], config["password"])
            status, data = client.select(config.get("imap_ordner") or "INBOX", readonly=True)
            if status != "OK":
                return False, "Postfach konnte nicht geöffnet werden"
            first = data[0] if data else b""
            text = first.decode("ascii", "ignore") if isinstance(first, bytes) else str(first or "")
            count = int(text) if text.strip().isdigit() else 0
            return True, f"Verbindung erfolgreich, {count} Nachrichten"
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except Exception as exc:
        return False, _error_text(exc)
