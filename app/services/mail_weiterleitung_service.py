"""Systemweite Mail-Weiterleitung (Sysadmin-Regeln) ohne Inhalte im Protokoll.

Ablauf: Der systemweite Webhook legt je Mail und Regel einen Protokolleintrag mit Status ``wartend`` an
(idempotent über ``resend_email_id``). Der Inhalt wird erst bei der Verarbeitung über die Receiving-API
geholt, weitergesendet und nie gespeichert. Fehlversuche werden bis zu ``MAX_VERSUCHE``-mal wiederholt.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr

import httpx
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mail_weiterleitung import MailWeiterleitung, MailWeiterleitungLog
from app.services.mail_service import get_resend_cfg
from app.services.org_mail_adresse import org_aus_adresse
from app.services.resend_inbound_service import (
    RESEND_RECEIVING_URL,
    clean_text,
    get_resend_attachment,
    resend_api_key_fuer_org,  # noqa: F401  (Re-Export fuer Tests)
    safe_attachment_url,
)
from app.services.resend_mail_service import send_via_resend

logger = logging.getLogger("einsatzleiter.mail_weiterleitung")

MAX_BYTES = 35 * 1024 * 1024
MAX_VERSUCHE = 3
CLAIM = "in_bearbeitung"
CLAIM_LEASE = timedelta(minutes=10)
_MAIL = re.compile(r"^[^\s@<>\r\n,;\"]+@[^\s@<>\r\n,;\"]+\.[^\s@<>\r\n,;\"]+$")
_BOUNCE_LOCALS = {"mailer-daemon", "postmaster"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def validiere_ziele(values: list[str]) -> list[str]:
    result = [item.strip().lower() for item in values if isinstance(item, str) and _MAIL.fullmatch(item.strip())]
    if not 1 <= len(result) <= 10 or len(set(result)) != len(result) or len(result) != len(
        [v for v in values if isinstance(v, str) and v.strip()]
    ):
        raise ValueError("Ein bis zehn unterschiedliche gültige Zieladressen erforderlich")
    return result


def normalisiere_adresse(value: str, domain: str, *, catch_all_bestaetigt: bool = False) -> str:
    value = value.strip().lower()
    if value == "*":
        if not catch_all_bestaetigt:
            raise ValueError("Catch-all muss ausdrücklich bestätigt werden")
        return value
    if "@" not in value:
        value = f"{value}@{domain}"
    if not _MAIL.fullmatch(value) or not value.endswith("@" + domain):
        raise ValueError("Adresse gehört nicht zur Empfangsdomain")
    if "." in value.split("@", 1)[0]:
        raise ValueError("Organisationsadressen (mit Punkt) können nicht weitergeleitet werden")
    return value


def _voll(row: MailWeiterleitung, domain: str) -> str:
    return row.adresse if "@" in row.adresse else f"{row.adresse}@{domain}"


def finde_regel(db: Session, empfaenger: list[str], domain: str) -> MailWeiterleitung | None:
    """Passende aktive Regel; Catch-all nie für Organisationsadressen."""
    addresses = {a.strip().lower() for a in empfaenger if isinstance(a, str)}
    rows = db.query(MailWeiterleitung).filter(MailWeiterleitung.aktiv.is_(True)).all()
    for row in rows:
        if row.adresse != "*" and _voll(row, domain).lower() in addresses:
            return row
    catch_all = next((r for r in rows if r.adresse == "*"), None)
    if catch_all is None:
        return None
    eigene = [a for a in addresses if a.endswith("@" + domain)]
    if not eigene or any(org_aus_adresse(db, a, domain) is not None for a in eigene):
        return None
    return catch_all


def _sender(value: object) -> str:
    addr = parseaddr(str(value or ""))[1].strip().lower()
    return addr if _MAIL.fullmatch(addr) else ""


def _log_row(rule: MailWeiterleitung, data: dict, status: str, code: str | None = None) -> MailWeiterleitungLog:
    sender = _sender(data.get("from"))
    return MailWeiterleitungLog(
        weiterleitung_id=rule.id, status=status, fehlercode=code, versuche=0,
        absenderdomain=sender.rsplit("@", 1)[1][:253] if sender else None,
        betreff=clean_text(data.get("subject"), 255) or None,
        resend_email_id=clean_text(data.get("email_id"), 64) or None,
    )


def stelle_in_warteschlange(db: Session, rule: MailWeiterleitung, data: dict) -> int | None:
    """Idempotent: je Regel und Mail genau ein Eintrag. Liefert die Log-ID oder ``None`` bei Duplikat."""
    email_id = clean_text(data.get("email_id"), 64)
    if not email_id:
        return None
    vorhanden = db.query(MailWeiterleitungLog.id).filter(
        MailWeiterleitungLog.weiterleitung_id == rule.id, MailWeiterleitungLog.resend_email_id == email_id,
    ).first()
    if vorhanden:
        return None
    sender = _sender(data.get("from"))
    local = sender.split("@", 1)[0] if sender else ""
    if not sender or local in _BOUNCE_LOCALS:
        db.add(_log_row(rule, data, "uebersprungen", "bounce"))
        db.commit()
        return None
    row = _log_row(rule, data, "wartend")
    db.add(row)
    db.commit()
    return row.id


def beanspruche(db: Session, log_id: int | None = None, limit: int = 10) -> list[int]:
    """Reserviert fällige Einträge atomar (SKIP LOCKED auf MariaDB, bedingtes UPDATE sonst)."""
    jetzt = _now()
    faellig = ((MailWeiterleitungLog.status == "wartend")
               & (MailWeiterleitungLog.updated_at <= jetzt - _backoff_func()))
    abgelaufen = (MailWeiterleitungLog.status == CLAIM) & (MailWeiterleitungLog.updated_at < jetzt - CLAIM_LEASE)
    query = db.query(MailWeiterleitungLog.id).filter(faellig | abgelaufen)
    if log_id is not None:
        query = db.query(MailWeiterleitungLog.id).filter(MailWeiterleitungLog.id == log_id,
                                                         (MailWeiterleitungLog.status == "wartend") | abgelaufen)
    ids = [i for (i,) in query.order_by(MailWeiterleitungLog.id).with_for_update(skip_locked=True).limit(limit)]
    gesichert: list[int] = []
    for i in ids:
        res = db.execute(update(MailWeiterleitungLog).where(
            MailWeiterleitungLog.id == i, (MailWeiterleitungLog.status == "wartend") | abgelaufen,
        ).values(status=CLAIM, updated_at=jetzt))
        if getattr(res, "rowcount", 0):
            gesichert.append(i)
    db.commit()
    return gesichert


def _backoff_func() -> timedelta:
    return timedelta(seconds=max(0, settings.MAIL_WEITERLEITUNG_RETRY_WAIT_S))


def _headers(payload: dict) -> dict[str, str]:
    raw = payload.get("headers") or {}
    if isinstance(raw, dict):
        return {str(k).lower(): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in raw if isinstance(h, dict)}
    return {}


def _automatisch(headers: dict[str, str]) -> bool:
    if headers.get("x-einsatzcockpit-weitergeleitet"):
        return True
    if headers.get("auto-submitted", "no").strip().lower() not in {"", "no"}:
        return True
    return headers.get("precedence", "").strip().lower() in {"bulk", "junk", "list"}


async def _download(url: str, limit: int) -> bytes:
    if not safe_attachment_url(url, from_resend_api=True):
        raise ValueError("download_url")
    async with httpx.AsyncClient(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
    if not resp.is_success or len(resp.content) > limit:
        raise ValueError("download")
    return resp.content


async def _hole_mail(api_key: str, email_id: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
        resp = await client.get(RESEND_RECEIVING_URL + email_id, headers={"Authorization": f"Bearer {api_key}"})
    if not resp.is_success:
        raise RuntimeError("abruf")
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def _ergebnis(db: Session, log: MailWeiterleitungLog, status: str, code: str | None = None) -> None:
    log.status, log.fehlercode, log.updated_at = status, code, _now()
    db.commit()


async def verarbeite_log(db: Session, log_id: int) -> str:
    """Verarbeitet einen reservierten Eintrag und liefert den neuen Status."""
    log = db.get(MailWeiterleitungLog, log_id)
    if log is None or log.status != CLAIM:
        return "ignoriert"
    rule = db.get(MailWeiterleitung, log.weiterleitung_id)
    if rule is None or not rule.aktiv:
        _ergebnis(db, log, "uebersprungen", "regel_inaktiv")
        return "uebersprungen"
    log.versuche += 1
    db.commit()
    try:
        cfg = get_resend_cfg(db)
        if not cfg:
            raise RuntimeError("konfiguration")
        key, domain = cfg["api_key"], cfg["from_domain"]
        targets = validiere_ziele(json.loads(rule.ziele))
        payload = await _hole_mail(key, log.resend_email_id or "")
        sender = _sender(payload.get("from"))
        if _automatisch(_headers(payload)):
            _ergebnis(db, log, "uebersprungen", "schleife")
            return "uebersprungen"
        if sender in targets:
            _ergebnis(db, log, "uebersprungen", "schleife")
            return "uebersprungen"
        gesendet = db.query(func.count(MailWeiterleitungLog.id)).filter(
            MailWeiterleitungLog.weiterleitung_id == rule.id, MailWeiterleitungLog.status == "gesendet",
            MailWeiterleitungLog.updated_at >= _now() - timedelta(hours=1),
        ).scalar() or 0
        if gesendet >= max(1, rule.max_pro_stunde):
            _ergebnis(db, log, "uebersprungen", "rate_limit")
            return "uebersprungen"
        message = EmailMessage()
        message["To"] = ", ".join(targets)
        message["Subject"] = clean_text(payload.get("subject"), 500) or "Weitergeleitete E-Mail"
        if sender:
            message["Reply-To"] = sender
        message["X-Einsatzcockpit-Weitergeleitet"] = "1"
        anhaenge: list[dict] = []
        gesamt = 0
        if rule.modus == "eml":
            raw = payload.get("raw")
            url = raw.get("download_url") if isinstance(raw, dict) else None
            if not isinstance(url, str):
                raise RuntimeError("raw")
            data = await _download(url, MAX_BYTES)
            message.set_content(f"Weitergeleitete Originalnachricht von {sender or 'unbekannt'} (siehe Anhang).")
            anhaenge.append({"filename": "original.eml", "content_type": "message/rfc822",
                             "content": base64.b64encode(data).decode()})
        else:
            text = str(payload.get("text") or "")
            html = str(payload.get("html") or "")
            kopf = f"Weitergeleitet von {sender or 'unbekannt'}\n\n"
            message.set_content(kopf + text if text or not html else kopf)
            if html:
                message.add_alternative(html, subtype="html")
            for item in payload.get("attachments") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                meta = await get_resend_attachment(key, log.resend_email_id or "", clean_text(item["id"], 200))
                if not meta or not isinstance(meta.get("download_url"), str):
                    raise RuntimeError("anhang")
                blob = await _download(meta["download_url"], MAX_BYTES - gesamt)
                gesamt += len(blob)
                ctype = clean_text(meta.get("content_type"), 100) or "application/octet-stream"
                anhaenge.append({"filename": clean_text(meta.get("filename"), 200) or "anhang",
                                 "content_type": ctype, "content": base64.b64encode(blob).decode()})
        if gesamt > MAX_BYTES:
            _ergebnis(db, log, "uebersprungen", "zu_gross")
            return "uebersprungen"
        await send_via_resend(message, key, f"weiterleitung@{domain}", anhaenge)
        rule.zaehler += 1
        rule.letzte_weiterleitung_at = _now()
        _ergebnis(db, log, "gesendet")
        return "gesendet"
    except Exception as exc:  # kein Mailinhalt / Providerdetails speichern
        db.rollback()
        log = db.get(MailWeiterleitungLog, log_id)
        if log is None:
            return "ignoriert"
        logger.warning("Weiterleitung fehlgeschlagen (log=%s, %s)", log_id, type(exc).__name__)
        final = log.versuche >= MAX_VERSUCHE
        _ergebnis(db, log, "fehler" if final else "wartend", "versand")
        return "fehler" if final else "wartend"


async def verarbeite_faellige(db: Session, log_id: int | None = None) -> int:
    ids = beanspruche(db, log_id)
    for i in ids:
        await verarbeite_log(db, i)
    return len(ids)


def bereinige_logs(db: Session, tage: int = 30) -> int:
    n = db.query(MailWeiterleitungLog).filter(MailWeiterleitungLog.zeit < _now() - timedelta(days=tage)).delete()
    db.commit()
    return int(n or 0)
