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
from app.services.loop_thread_io import ALARM_POOL, im_db_thread
from app.services.mail_service import (
    _build_message,
    _org_smtp_cfg,
    deliver,
    get_smtp_cfg,
    prepare_delivery,
)
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
        if kontakt.kontakt_id is None or kontakt.zentraler_kontakt is None:
            logger.warning("Objektkontakt %s ohne zentralen Kontakt ignoriert", kontakt.id)
            continue
        for freigabe in kontakt.freigaben:
            if freigabe.aktiv:
                ziele.append((kontakt, freigabe.kanal, freigabe.ziel_wert))
    return ziele


def _kontakt_anzeigename(kontakt: ObjektKontakt) -> str:
    return kontakt.zentraler_kontakt.anzeigename if kontakt.zentraler_kontakt else ""


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
        "kontakt": _kontakt_anzeigename(kontakt),
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
    try:
        objekte, uebersprungen = await im_db_thread(
            _prepare_objekt_einsatzinfo_sync, incident_id, objekt_ids, force, pool=ALARM_POOL,
        )
        ergebnis["uebersprungen"] += uebersprungen
        for objekt_id, ziele in objekte:
            zaehler = {"mail": 0, "sms": 0, "fehler": 0}
            for ziel in ziele:
                vorbereitet = await im_db_thread(
                    _prepare_kontakt_versand_sync, incident_id, ziel, triggered_by_user_id, pool=ALARM_POOL,
                )
                if vorbereitet is None:
                    ergebnis["uebersprungen"] += 1
                    continue
                protokoll_id, msg, config, vorbereitungsfehler = vorbereitet
                fehler = None
                try:
                    if vorbereitungsfehler is not None:
                        raise RuntimeError(vorbereitungsfehler)
                    if ziel["kanal"] == "mail":
                        await deliver(config, ziel["org_id"], msg, config.smtp)
                    else:
                        result = await send_sms(ziel["org_id"], ziel["empfaenger"], ziel["versandtext"],
                                                ctx=ziel["sms_ctx"])
                        if not result:
                            raise RuntimeError("SMS-Versand fehlgeschlagen")
                    ergebnis["gesendet"] += 1
                    zaehler[ziel["kanal"]] += 1
                except Exception as exc:
                    fehler = str(exc)[:500]
                    ergebnis["fehler"] += 1
                    zaehler["fehler"] += 1
                    logger.warning("Objekt-Einsatzinfo fehlgeschlagen: %s", exc)
                await im_db_thread(_finish_kontakt_versand_sync, protokoll_id, fehler, pool=ALARM_POOL)
            if any(zaehler.values()):
                await im_db_thread(_kontakt_audit_sync, incident_id, objekt_id, ziele[0]["org_id"],
                                   triggered_by_user_id, zaehler, force, pool=ALARM_POOL)
    except Exception:
        logger.exception("Objekt-Einsatzinfo fuer Einsatz %d fehlgeschlagen", incident_id)
    return ergebnis


def _prepare_objekt_einsatzinfo_sync(incident_id: int, objekt_ids: list[int] | None, force: bool) -> tuple[list, int]:
    ergebnis = {"uebersprungen": 0}
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if not incident or not incident.primary_org_id:
            return [], ergebnis["uebersprungen"]
        org_id = incident.primary_org_id

        from app.services.objekt_service import objekt_effective_enabled
        if not objekt_effective_enabled(org_id, db):
            return [], ergebnis["uebersprungen"]

        query = db.query(ObjektEinsatz).options(
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte).selectinload(
                ObjektKontakt.freigaben
            ),
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte).selectinload(
                ObjektKontakt.zentraler_kontakt
            ),
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
            return [], ergebnis["uebersprungen"]
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        from app.services.exercise_guard import darf_extern
        if not darf_extern(
            "objekt_kontakt",
            is_exercise=incident.is_exercise,
            org_id=org_id,
            db=db,
            planspiel_run_id=incident.planspiel_run_id,
        ):
            return [], ergebnis["uebersprungen"]
        sms_geprueft = False
        sms_verfuegbar = False
        sms_ctx = None

        objekte = []
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

            ziele = []
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

                kontext = baue_kontext(db, incident, objekt, kontakt, org)
                text = render_template(loese_template(objekt, org_settings), kontext).strip()
                betreff = render_template(loese_betreff(objekt, org_settings), kontext).strip()
                if incident.is_exercise:
                    betreff = "[ÜBUNG] " + betreff
                    sms_text = "[UEBUNG] " + text
                else:
                    sms_text = text
                versandtext = sms_text if kanal == "sms" else text
                ziele.append({
                    "org_id": org_id, "objekt_id": objekt.id, "kontakt_id": kontakt.id,
                    "kontakt_name": _kontakt_anzeigename(kontakt), "kanal": kanal, "empfaenger": empfaenger,
                    "versandtext": versandtext, "text": text, "betreff": betreff, "sms_ctx": sms_ctx,
                })
            objekte.append((objekt.id, ziele))
        return objekte, ergebnis["uebersprungen"]
    finally:
        db.close()


def _prepare_kontakt_versand_sync(incident_id: int, ziel: dict, user_id: int | None) -> tuple | None:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        protokoll = db.query(ObjektKontaktBenachrichtigung).filter_by(
            org_id=ziel["org_id"], incident_id=incident_id, objekt_kontakt_id=ziel["kontakt_id"],
            kanal=ziel["kanal"], empfaenger=ziel["empfaenger"],
        ).first()
        if protokoll and protokoll.status == OBJEKT_INFO_GESENDET:
            return None
        if not protokoll:
            savepoint = db.begin_nested()
            try:
                protokoll = ObjektKontaktBenachrichtigung(
                    org_id=ziel["org_id"], incident_id=incident_id, objekt_id=ziel["objekt_id"],
                    objekt_kontakt_id=ziel["kontakt_id"], kanal=ziel["kanal"],
                    kontakt_name=ziel["kontakt_name"], empfaenger=ziel["empfaenger"],
                    # Erst das Transportergebnis darf den Versuch als gesendet markieren.
                    status=OBJEKT_INFO_FEHLER,
                )
                db.add(protokoll)
                db.flush()
                savepoint.commit()
            except IntegrityError:
                savepoint.rollback()
                return None
        protokoll.kontakt_name = ziel["kontakt_name"]
        protokoll.empfaenger = ziel["empfaenger"]
        protokoll.text = ziel["versandtext"]
        protokoll.ausgeloest_von_id = user_id
        protokoll.gesendet_am = datetime.now(UTC).replace(tzinfo=None)
        msg = config = None
        fehler = None
        try:
            if ziel["kanal"] == "mail":
                smtp_cfg = _org_smtp_cfg(db, ziel["org_id"]) or get_smtp_cfg(db)
                msg = _build_message(to=ziel["empfaenger"], subject=ziel["betreff"], body_txt=ziel["text"],
                                     body_html=f"<pre>{html.escape(ziel['text'])}</pre>", smtp_cfg=smtp_cfg)
                config = prepare_delivery(db, ziel["org_id"], msg, smtp_cfg)
        except Exception as exc:
            fehler = str(exc)[:500]
        db.commit()
        return protokoll.id, msg, config, fehler


def _finish_kontakt_versand_sync(protokoll_id: int, fehler: str | None) -> None:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        protokoll = db.get(ObjektKontaktBenachrichtigung, protokoll_id)
        assert protokoll is not None
        protokoll.status = OBJEKT_INFO_FEHLER if fehler is not None else OBJEKT_INFO_GESENDET
        protokoll.fehlertext = fehler
        db.commit()


def _kontakt_audit_sync(incident_id: int, objekt_id: int, org_id: int, user_id: int | None,
                        zaehler: dict, force: bool) -> None:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        write_audit(db, "objekt.kontakt_info_gesendet", org_id=org_id, user_id=user_id,
                    incident_id=incident_id, entity_type="objekt", entity_id=objekt_id,
                    payload={**zaehler, "force": force})
        db.commit()
