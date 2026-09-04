"""Versand der Einsatzinfo an Kontakte bestaetigt verknuepfter Objekte.

Der Einstiegspunkt wird immer erst nach dem Commit der Verknuepfung aufgerufen,
ist ueber das Versandprotokoll idempotent und arbeitet best effort (wirft nie).
"""
from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_INFO_FEHLER,
    OBJEKT_INFO_GESENDET,
    Objekt,
    ObjektEinsatz,
    ObjektKontakt,
    ObjektKontaktBenachrichtigung,
)
from app.services.mail_service import (
    _build_message,
    _looks_like_email,
    _org_smtp_cfg,
    deliver,
    get_smtp_cfg,
)
from app.services.objekt_service import telefon_normalisiert
from app.services.sms_dispatch_service import render_template
from app.services.sms_service import resolve_sms_config, send_sms, sms_available

logger = logging.getLogger("einsatzleiter.objekt_kontakt_notify")

_DEFAULT_BETREFF = "Einsatz der {feuerwehr} bei {objekt}"
_DEFAULT_TEMPLATE = (
    "Am {datum} um {zeit} Uhr wurde die {feuerwehr} zu einem Einsatz "
    "({stichwort}) bei {objekt}, {adresse}, alarmiert. "
    "Bitte wenden Sie sich vor Ort an die Einsatzleitung."
)


def default_kontakt_info_betreff() -> str:
    return _DEFAULT_BETREFF


def default_kontakt_info_template() -> str:
    return _DEFAULT_TEMPLATE


def loese_betreff(objekt: Objekt, org_settings: OrgSettings | None) -> str:
    return (
        objekt.kontakt_info_betreff
        or (org_settings.objekt_kontakt_info_betreff if org_settings else None)
        or _DEFAULT_BETREFF
    )


def loese_template(objekt: Objekt, org_settings: OrgSettings | None) -> str:
    return (
        objekt.kontakt_info_template
        or (org_settings.objekt_kontakt_info_template if org_settings else None)
        or _DEFAULT_TEMPLATE
    )


def stichwort_erlaubt(objekt: Objekt, alarm_type_code: str | None) -> bool:
    if not objekt.kontakt_info_stichworte:
        return True
    erlaubt = {
        wert.strip().casefold()
        for wert in objekt.kontakt_info_stichworte.split(",")
        if wert.strip()
    }
    return (alarm_type_code or "").strip().casefold() in erlaubt


def sammle_ziele(objekt: Objekt) -> list[tuple[ObjektKontakt, str, str]]:
    ziele: list[tuple[ObjektKontakt, str, str]] = []
    for kontakt in objekt.kontakte:
        email = (kontakt.email or "").strip()
        if kontakt.benachrichtigung_mail and _looks_like_email(email):
            ziele.append((kontakt, "mail", email.casefold()))
        for nummer in kontakt.sms_nummern:
            normalisiert = telefon_normalisiert(nummer)
            if normalisiert:
                ziele.append((kontakt, "sms", normalisiert))
    return ziele


def baue_kontext(db, incident: Incident, objekt: Objekt, kontakt: ObjektKontakt, org: FireDept) -> dict:
    zeitpunkt = incident.started_at or datetime.now(UTC).replace(tzinfo=None)
    if zeitpunkt.tzinfo is None:
        zeitpunkt = zeitpunkt.replace(tzinfo=UTC)
    try:
        lokal = zeitpunkt.astimezone(ZoneInfo(org.timezone or "Europe/Vienna"))
    except Exception:
        lokal = zeitpunkt.astimezone(ZoneInfo("Europe/Vienna"))
    objekt_adresse = " ".join(filter(None, [objekt.strasse, objekt.hausnummer])).strip()
    adresse = ", ".join(filter(None, [objekt_adresse, objekt.ort])).strip()
    return {
        "objekt": objekt.name or "",
        "objektnummer": objekt.nummer or "",
        "vulgoname": objekt.vulgoname or "",
        "stichwort": incident.alarm_type_code or "",
        "adresse": adresse,
        "ort": objekt.ort or incident.address_city or "",
        "meldung": incident.report_text or "",
        "einsatzgrund": incident.reason or "",
        "datum": lokal.strftime("%d.%m.%Y"),
        "zeit": lokal.strftime("%H:%M"),
        "feuerwehr": org.name or "",
        "kontakt": kontakt.name or "",
        "leitstellennummer": incident.lis_operation_number or "",
    }


async def dispatch_objekt_einsatzinfo(
    incident_id: int,
    *,
    objekt_ids: list[int] | None = None,
    force: bool = False,
    triggered_by_user_id: int | None = None,
) -> dict:
    ergebnis = {"gesendet": 0, "fehler": 0, "uebersprungen": 0}
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if not incident or not incident.primary_org_id:
            return ergebnis
        org_id = incident.primary_org_id

        from app.services.objekt_service import objekt_effective_enabled
        if not objekt_effective_enabled(org_id, db):
            return ergebnis

        query = db.query(ObjektEinsatz).options(
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte)
        ).filter(
            ObjektEinsatz.incident_id == incident_id,
            ObjektEinsatz.org_id == org_id,
            ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
        )
        if objekt_ids is not None:
            query = query.filter(ObjektEinsatz.objekt_id.in_(objekt_ids))
        verknuepfungen = query.all()
        org = db.query(FireDept).filter(FireDept.id == org_id).first()
        if not org:
            return ergebnis
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        from app.services.exercise_guard import darf_extern
        if not darf_extern(
            "objekt_kontakt",
            is_exercise=incident.is_exercise,
            org_id=org_id,
            db=db,
        ):
            return ergebnis
        sms_geprueft = False
        sms_verfuegbar = False
        sms_ctx = None

        for verknuepfung in verknuepfungen:
            objekt = verknuepfung.objekt
            if not objekt or objekt.org_id != org_id:
                ergebnis["uebersprungen"] += 1
                continue
            if not force and (
                (incident.is_exercise and not objekt.kontakt_info_uebung)
                or not stichwort_erlaubt(objekt, incident.alarm_type_code)
            ):
                ergebnis["uebersprungen"] += 1
                continue

            zaehler = {"mail": 0, "sms": 0, "fehler": 0}
            for kontakt, kanal, empfaenger in sammle_ziele(objekt):
                if kontakt.org_id != org_id:
                    ergebnis["uebersprungen"] += 1
                    continue
                if kanal == "sms":
                    if not sms_geprueft:
                        sms_verfuegbar = sms_available(org_id, db)
                        sms_ctx = resolve_sms_config(org_id, db) if sms_verfuegbar else None
                        sms_geprueft = True
                        if not sms_verfuegbar:
                            logger.debug("Kein SMS-Provider fuer Objekt-Einsatzinfo (org_id=%d)", org_id)
                    if not sms_verfuegbar:
                        ergebnis["uebersprungen"] += 1
                        continue

                protokoll = db.query(ObjektKontaktBenachrichtigung).filter(
                    ObjektKontaktBenachrichtigung.org_id == org_id,
                    ObjektKontaktBenachrichtigung.incident_id == incident_id,
                    ObjektKontaktBenachrichtigung.objekt_kontakt_id == kontakt.id,
                    ObjektKontaktBenachrichtigung.kanal == kanal,
                    ObjektKontaktBenachrichtigung.empfaenger == empfaenger,
                ).first()
                if protokoll and protokoll.status == OBJEKT_INFO_GESENDET:
                    ergebnis["uebersprungen"] += 1
                    continue

                kontext = baue_kontext(db, incident, objekt, kontakt, org)
                text = render_template(loese_template(objekt, org_settings), kontext).strip()
                betreff = render_template(loese_betreff(objekt, org_settings), kontext).strip()
                if incident.is_exercise:
                    betreff = "[ÜBUNG] " + betreff
                    sms_text = "[UEBUNG] " + text
                else:
                    sms_text = text
                versandtext = sms_text if kanal == "sms" else text
                if not protokoll:
                    savepoint = db.begin_nested()
                    try:
                        protokoll = ObjektKontaktBenachrichtigung(
                            org_id=org_id, incident_id=incident_id, objekt_id=objekt.id,
                            objekt_kontakt_id=kontakt.id, kanal=kanal,
                            kontakt_name=kontakt.name, empfaenger=empfaenger,
                        )
                        db.add(protokoll)
                        db.flush()
                        savepoint.commit()
                    except IntegrityError:
                        savepoint.rollback()
                        ergebnis["uebersprungen"] += 1
                        continue
                protokoll.kontakt_name = kontakt.name
                protokoll.empfaenger = empfaenger
                protokoll.text = versandtext
                protokoll.ausgeloest_von_id = triggered_by_user_id
                protokoll.gesendet_am = datetime.now(UTC).replace(tzinfo=None)
                try:
                    if kanal == "mail":
                        smtp_cfg = _org_smtp_cfg(db, org_id) or get_smtp_cfg(db)
                        msg = _build_message(
                            to=empfaenger,
                            subject=betreff,
                            body_txt=text,
                            body_html=f"<pre>{html.escape(text)}</pre>",
                            smtp_cfg=smtp_cfg,
                        )
                        await deliver(db, org_id, msg, smtp_cfg)
                    else:
                        result = await send_sms(org_id, empfaenger, sms_text, ctx=sms_ctx)
                        if not result:
                            raise RuntimeError("SMS-Versand fehlgeschlagen")
                    protokoll.status = OBJEKT_INFO_GESENDET
                    protokoll.fehlertext = None
                    ergebnis["gesendet"] += 1
                    zaehler[kanal] += 1
                except Exception as exc:
                    protokoll.status = OBJEKT_INFO_FEHLER
                    protokoll.fehlertext = str(exc)[:500]
                    ergebnis["fehler"] += 1
                    zaehler["fehler"] += 1
                    logger.warning("Objekt-Einsatzinfo fehlgeschlagen: %s", exc)
                db.commit()

            if any(zaehler.values()):
                write_audit(
                    db,
                    "objekt.kontakt_info_gesendet",
                    org_id=org_id,
                    user_id=triggered_by_user_id,
                    incident_id=incident_id,
                    entity_type="objekt",
                    entity_id=objekt.id,
                    payload={**zaehler, "force": force},
                )
                db.commit()
        return ergebnis
    except Exception:
        db.rollback()
        logger.exception("Objekt-Einsatzinfo fuer Einsatz %d fehlgeschlagen", incident_id)
        return ergebnis
    finally:
        db.close()
