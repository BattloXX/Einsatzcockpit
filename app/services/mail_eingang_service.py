"""Quellenneutrale Speicherung und MIME-Normalisierung eingehender Mails."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.org_mail import OrgMailEingang, OrgMailEingangEreignis

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_AUTH = re.compile(r"\b(spf|dkim|dmarc)=(\w+)", re.IGNORECASE)


def _clean(value: object, maximum: int) -> str:
    return _CONTROL.sub("", str(value or "").replace("\r", " ").replace("\n", " ")).strip()[:maximum]


def _json(value: object, maximum: int = 100_000) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)[:maximum]


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def normalisiere_absender(roh: str) -> str:
    name, address = parseaddr(str(roh or ""))
    if address and "@" in address and not any(char.isspace() for char in address):
        return address.strip().lower()[:500]
    return _clean(roh, 500)


def ereignis(
    db: Session, row: OrgMailEingang, typ: str, text: str | None = None, user_id: int | None = None,
) -> OrgMailEingangEreignis:
    event = OrgMailEingangEreignis(
        org_id=row.org_id, eingang_id=row.id, ts=datetime.now(UTC).replace(tzinfo=None),
        typ=_clean(typ, 32), text=_clean(text, 500) or None, user_id=user_id,
    )
    db.add(event)
    return event


def speichere_eingang(
    db: Session, org_id: int, *, quelle: str, extern_id: str, absender: str, betreff: str,
    empfaenger: list[str], cc: list[str] | None, message_id: str | None, empfangen_at: datetime,
    text_body: str | None = None, html_body: str | None = None, header_json: object = None,
    spf: str | None = None, dkim: str | None = None, dmarc: str | None = None, anhang_json: object = None,
    groesse_bytes: int | None = None, abgerufen: bool = False,
) -> OrgMailEingang | None:
    quelle = _clean(quelle, 10)
    extern_id = _clean(extern_id, 255)
    if not quelle or not extern_id:
        return None
    exists = db.query(OrgMailEingang).execution_options(include_all_tenants=True).filter(
        OrgMailEingang.org_id == org_id, OrgMailEingang.quelle == quelle, OrgMailEingang.extern_id == extern_id
    ).first()
    if exists:
        return None
    row = OrgMailEingang(
        org_id=org_id, quelle=quelle, extern_id=extern_id, message_id=_clean(message_id, 500) or None,
        absender=normalisiere_absender(absender), empfaenger=_json(empfaenger),
        cc=_json(cc) if cc else None, betreff=_clean(betreff, 998),
        empfangen_at=_naive_utc(empfangen_at) or datetime.now(UTC).replace(tzinfo=None),
        text_body=str(text_body or "")[:1_000_000] or None, html_body=str(html_body or "")[:2_000_000] or None,
        header_json=_json(header_json, 200_000) if header_json else None,
        spf=_clean(spf, 20) or None, dkim=_clean(dkim, 20) or None, dmarc=_clean(dmarc, 20) or None,
        anhang_json=_json(anhang_json), groesse_bytes=groesse_bytes, status="neu",
        abgerufen_at=datetime.now(UTC).replace(tzinfo=None) if abgerufen else None,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    labels = {"resend": "Resend", "imap": "IMAP", "o365": "Office 365"}
    ereignis(db, row, "empfangen", "über " + labels.get(quelle, quelle))
    db.flush()
    return row


def parse_authentication_results(werte: list[str]) -> tuple[str | None, str | None, str | None]:
    result: dict[str, str | None] = {"spf": None, "dkim": None, "dmarc": None}
    if werte:
        for name, value in _AUTH.findall(werte[0]):
            result[name.lower()] = value.lower()
    return result["spf"], result["dkim"], result["dmarc"]


def _addresses(value: object) -> list[str]:
    from email.utils import getaddresses
    return [address.strip().lower() for _, address in getaddresses([str(value or "")]) if address.strip()]


def normalisiere_mime(raw: bytes) -> dict:
    if not raw:
        raise ValueError("leere Mail")
    message: Any
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
    headers: dict[str, list[str]] = {}
    for name, value in message.items():
        headers.setdefault(str(name), []).append(_clean(value, 10_000))
    while len(json.dumps(headers, ensure_ascii=False)) > 200_000 and headers:
        headers.pop(next(reversed(headers)))
    date_value = None
    try:
        date_value = _naive_utc(parsedate_to_datetime(str(message.get("Date") or "")))
    except (TypeError, ValueError, IndexError):
        pass
    def body(kind: str) -> str | None:
        try:
            part = message.get_body(preferencelist=(kind,))
            return part.get_content()[:2_000_000] if part else None
        except Exception:
            return None
    attachments: list[dict] = []
    for part in message.walk():
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        is_rfc822 = part.get_content_type() == "message/rfc822"
        if disposition not in {"attachment", "inline"} and not is_rfc822:
            continue
        if disposition == "inline" and not filename:
            continue
        try:
            data = part.as_bytes(policy=policy.default) if is_rfc822 else part.get_payload(decode=True) or b""
        except Exception:
            data = b""
        standard = "nachricht.eml" if is_rfc822 else "anhang"
        attachments.append({
            "filename": _clean(filename or standard, 500), "content_type": part.get_content_type(), "data": data,
        })
    auth = parse_authentication_results(headers.get("Authentication-Results", []))
    return {
        "betreff": _clean(message.get("Subject"), 998),
        "absender": normalisiere_absender(str(message.get("From") or "")),
        "empfaenger": _addresses(message.get("To")), "cc": _addresses(message.get("Cc")),
        "message_id": _clean(message.get("Message-ID"), 500) or None, "datum": date_value,
        "text": body("plain"), "html": body("html"), "header": headers,
        "auth": {"spf": auth[0], "dkim": auth[1], "dmarc": auth[2]}, "anhaenge": attachments,
    }
