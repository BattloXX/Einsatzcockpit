"""Kontrollierte Uebernahme von Resend-Posteingang in Einsatzmeldungen."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import httpx
from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang
from app.services.incident_service import add_message, broadcast_message_created
from app.services.mail_eingang_service import ereignis, normalisiere_absender
from app.services.media_service import store_bytes_for_message
from app.services.resend_inbound_service import (
    MAX_ATTACHMENT_BYTES,
    get_resend_attachment,
    resend_api_key_fuer_org,
    safe_attachment_url,
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_DETAIL = 200_000


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean(value: object, maximum: int) -> str:
    return _CONTROL.sub("", str(value or "").replace("\r", " ").replace("\n", " ")).strip()[:maximum]


def _html_text(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value)
    except Exception:
        pass
    return " ".join(" ".join(parser.parts).split())


def _allowed(row: OrgMailEingang, settings: OrgSettings) -> tuple[bool, str]:
    sender = normalisiere_absender(row.absender)
    if settings.mail_eingang_modus == "freigegeben":
        allowed = {line.strip().lower() for line in (settings.mail_eingang_absender or "").splitlines() if line.strip()}
        domain = "@" + sender.rsplit("@", 1)[1] if "@" in sender else ""
        return (bool(sender and (sender in allowed or domain in allowed)), "Absender ist nicht freigegeben")
    if settings.mail_eingang_modus == "alle":
        results = [str(getattr(row, name) or "").lower() for name in ("spf", "dkim", "dmarc")]
        if not any(results):
            return False, "Authentifizierung des Absenders ist nicht bestimmbar"
        return all(result == "pass" for result in results), "SPF, DKIM oder DMARC nicht bestanden"
    return False, "Automatische Uebernahme ist deaktiviert"


def aktive_einsaetze(db: Session, org_id: int) -> list[Incident]:
    return (db.query(Incident).filter(Incident.primary_org_id == org_id, Incident.status == "active",
                                      Incident.is_exercise == False)  # noqa: E712
            .order_by(Incident.started_at.desc(), Incident.id.desc()).all())


def _detail(row: OrgMailEingang) -> str:
    body = _CONTROL.sub("", row.text_body or _html_text(row.html_body or ""))
    stamp = row.empfangen_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"Von: {_clean(row.absender, 500)} · {stamp}\n\n{body[:_MAX_DETAIL]}"[:_MAX_DETAIL]


async def _attachments(db: Session, row: OrgMailEingang, message) -> list[str]:
    if row.org_id is None:
        return ["Anhänge konnten nicht abgerufen werden."]
    api_key = resend_api_key_fuer_org(db, row.org_id)
    if not api_key:
        return ["Anhänge konnten nicht abgerufen werden."]
    try:
        metadata = json.loads(row.anhang_json or "[]")
    except (TypeError, ValueError):
        metadata = []
    failures: list[str] = []
    for item in metadata if isinstance(metadata, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        try:
            attachment = await get_resend_attachment(api_key, row.extern_id, str(item["id"])[:200])
            if not attachment:
                raise ValueError("Anhang nicht verfügbar")
            url = attachment.get("download_url") if attachment else None
            if not isinstance(url, str) or not safe_attachment_url(url, from_resend_api=True):
                raise ValueError("ungueltige Download-Adresse")
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
            if not response.is_success or len(response.content) > MAX_ATTACHMENT_BYTES:
                raise ValueError("Anhang nicht verfuegbar oder zu gross")
            await store_bytes_for_message(response.content, attachment.get("filename"), attachment.get("content_type"),
                                          message, db, row.org_id, user=None, allow_datei=True)
        except Exception:
            failures.append(_clean(item.get("filename") or "Anhang", 80))
    return failures


async def _assign(db: Session, row: OrgMailEingang, incident: Incident, kind: str, user_id: int | None = None) -> bool:
    if row.message_id_ref:
        return False
    # The incident was loaded with the input org predicate; preserve that guard
    # immediately before the mutation as this service may be called by a route.
    if incident.primary_org_id != row.org_id or incident.status != "active" or incident.is_exercise:
        return False
    title = _clean(row.betreff, 500) or f"E-Mail von {_clean(row.absender, 400)}"
    message = add_message(
        db, incident, title=title, detail=_detail(row), author_name=f"E-Mail ({_clean(row.absender, 450)})",
        user_id=None, source="mail_eingang",
    )
    failures = await _attachments(db, row, message)
    row.incident_id, row.message_id_ref = incident.id, message.id
    row.zuordnung, row.zugeordnet_at, row.status = kind, datetime.now(UTC).replace(tzinfo=None), "zugeordnet"
    row.zuordnung_grund = ("Anhänge nicht übernommen: " + ", ".join(failures))[:255] if failures else None
    art = "automatisch" if kind == "auto" else "manuell"
    ereignis(db, row, "zugeordnet", f"Einsatz #{incident.id} ({art}), Meldung #{message.id}", user_id)
    for filename in failures:
        ereignis(db, row, "anhang_fehler", filename, user_id)
    db.commit()
    await broadcast_message_created(incident.id, message)
    from app.services.print_dispatcher import autoprint_mail_eingang_background
    await autoprint_mail_eingang_background(row.id)
    return True


def _warte_text(db: Session, row: OrgMailEingang, settings: OrgSettings) -> str:
    from app.core.timezones import format_local_time
    from app.models.master import FireDept

    minuten = max(5, min(int(settings.mail_eingang_rueckblick_min or 30), 240))
    org = db.query(FireDept).filter(FireDept.id == row.org_id).first()
    return f"wartet auf Einsatz bis {format_local_time(row.empfangen_at + timedelta(minutes=minuten), org)}"


async def ordne_zu(db: Session, eingang_id: int, incident_ids: set[int] | None = None) -> bool:
    """Ordnet eine bereits vollständig abgerufene Mail höchstens einmal zu."""
    row = db.query(OrgMailEingang).filter(OrgMailEingang.id == eingang_id).with_for_update().first()
    if not row or row.message_id_ref or not row.abgerufen_at:
        return False
    if row.org_id is None:
        return False
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == row.org_id).first()
    if not settings or settings.mail_eingang_modus == "aus":
        return False
    allowed, reason = _allowed(row, settings)
    if not allowed:
        row.status = "posteingang"
        row.zuordnung = "mehrdeutig" if "nicht bestimmbar" in reason else "abgelehnt"
        row.zuordnung_grund = reason
        ereignis(db, row, "mehrdeutig" if "nicht bestimmbar" in reason else "absender_abgelehnt", reason)
        db.commit()
        return False
    incidents = aktive_einsaetze(db, row.org_id)
    if incident_ids is not None:
        incidents = [incident for incident in incidents if incident.id in incident_ids]
    if not incidents:
        schon_wartend = row.status == "wartend"
        row.status, row.zuordnung, row.zuordnung_grund = "wartend", "wartend", "Kein aktiver Einsatz vorhanden"
        # Der Warte-Loop ruft ordne_zu alle 30 s erneut auf; das Ereignis nur beim Eintritt schreiben.
        if not schon_wartend:
            ereignis(db, row, "wartend", _warte_text(db, row, settings))
        db.commit()
        return False
    if len(incidents) > 1 and settings.mail_eingang_mehrere != "neuester":
        row.status, row.zuordnung = "posteingang", "mehrdeutig"
        row.zuordnung_grund = "Mehrere aktive Einsätze vorhanden"
        ereignis(db, row, "mehrdeutig", "Mehrere aktive Einsätze")
        db.commit()
        return False
    return await _assign(db, row, incidents[0], "auto")


async def uebernehme_in_einsatz(db: Session, eingang_id: int, incident_id: int, user_id: int | None = None) -> bool:
    row = db.query(OrgMailEingang).filter(OrgMailEingang.id == eingang_id).with_for_update().first()
    if not row or row.message_id_ref:
        return False
    incident = db.query(Incident).filter(Incident.id == incident_id, Incident.primary_org_id == row.org_id,
                                         Incident.status == "active", Incident.is_exercise == False).first()  # noqa: E712
    return bool(incident and await _assign(db, row, incident, "manuell", user_id))
