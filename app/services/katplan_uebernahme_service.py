"""Planmassnahmen mit eigenstaendigen Medien in normale Einsaetze uebernehmen."""

import json
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.permissions import has_role
from app.core.queries import visible_incidents_q
from app.models.incident import Incident, Message, Task
from app.models.katplan import (
    KatplanAnhang,
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.objekt import ObjektDokument
from app.models.user import User
from app.services import incident_service, katplan_anhang_service, katplan_quellbild_service, media_service
from app.services.katplan_quelle_service import absolute_pfad, parse_seiten
from app.services.katplan_service import _HTMLText
from app.services.katplan_zugriff import darf_lesen, sieht_arbeitsstand, sieht_vertraulich

Element = KatplanElement | KatplanVersionElement
Art = Literal["auftrag", "meldung"]


@dataclass
class UebernahmeErgebnis:
    art: Art
    incident: Incident
    incident_id: int
    task_id: int | None = None
    message_id: int | None = None
    column_id: int | None = None
    uid: int | None = None
    vehicle_uid: int | None = None
    message: Message | None = None
    fehler: list[str] = field(default_factory=list)
    medien_anzahl: int = 0


def kandidaten_einsaetze(db: Session, user: User) -> list[Incident]:
    if not has_role(user, "incident_leader", "admin", "recorder"):
        return []
    return (visible_incidents_q(db, user).filter(Incident.status == "active")
            .order_by(Incident.started_at.desc()).limit(50).all())


def _json(roh: str | None, fallback):
    try:
        return json.loads(roh or "null") or fallback
    except (ValueError, TypeError):
        return fallback


def _klartext(roh: str | None) -> str:
    parser = _HTMLText()
    parser.feed(roh or "")
    parser.close()
    return " ".join("".join(parser.teile).split())


def _lesedaten(db: Session, user: User, plan: KatplanPlan, element: Element,
               leseversion: KatplanVersion | None) -> tuple[KatplanVersion | None, list[dict]]:
    if not darf_lesen(user):
        raise HTTPException(403, "Keine Plan-Leseberechtigung.")
    if user.org_id != plan.org_id or element.org_id != plan.org_id or plan.status == "archiviert":
        raise HTTPException(404, "Planmassnahme nicht gefunden.")
    version = None
    if isinstance(element, KatplanVersionElement):
        version = db.query(KatplanVersion).filter_by(
            id=element.version_id, plan_id=plan.id, org_id=plan.org_id,
        ).one_or_none()
        if version is None or (leseversion is not None and leseversion.id != version.id):
            raise HTTPException(404, "Version nicht gefunden.")
        ids = {a.get("uuid") for a in _json(element.anhaenge_json, []) if isinstance(a, dict)}
        anhaenge = [a for a in katplan_anhang_service.anhaenge_aus_version(db, plan, version.id)
                    if a.get("uuid") in ids]
    else:
        if element.plan_id != plan.id or leseversion is not None:
            raise HTTPException(404, "Planmassnahme nicht gefunden.")
        if not sieht_arbeitsstand(user):
            raise HTTPException(403, "Bitte die freigegebene Version verwenden.")
        anhaenge = [katplan_anhang_service.anhang_snapshot(a) for a in
                    katplan_anhang_service.sichtbare_anhaenge(db, plan, user, element_id=element.id)]
    if element.vertraulich and not sieht_vertraulich(user):
        raise HTTPException(403, "Vertrauliche Planmassnahme.")
    return version, [a for a in anhaenge if not a.get("vertraulich") or sieht_vertraulich(user)]


def _quellseiten(db: Session, plan: KatplanPlan, element: Element) -> list[dict]:
    dokument = db.query(KatplanDokument).filter_by(
        id=element.quelle_dokument_id, plan_id=plan.id, org_id=plan.org_id,
    ).one_or_none() if element.quelle_dokument_id else None
    if dokument is None:
        return []
    try:
        nummern = parse_seiten(element.quelle_seiten or "", dokument.seitenzahl)
    except ValueError:
        return []
    seiten = db.query(KatplanDokumentSeite).filter_by(
        dokument_id=dokument.id, plan_id=plan.id, org_id=plan.org_id,
    ).filter(KatplanDokumentSeite.seiten_nr.in_(nummern)).order_by(KatplanDokumentSeite.seiten_nr).all()
    return [{"dokument_uuid": dokument.uuid, "nr": s.seiten_nr,
             "name": f"{dokument.titel}, S. {s.seiten_nr}"} for s in seiten]


def _anhang_name(db: Session, plan: KatplanPlan, anhang: dict) -> str:
    if anhang.get("beschriftung") or anhang.get("dateiname"):
        return anhang.get("beschriftung") or anhang["dateiname"]
    if anhang.get("art") == "objekt_dokument":
        dokument = db.query(ObjektDokument).filter_by(
            id=anhang.get("objekt_dokument_id"), org_id=plan.org_id,
        ).one_or_none()
        return dokument.dateiname_original if dokument else "Objektdokument"
    return anhang.get("url") or "Anhang"


def vorschlag(db: Session, user: User, plan: KatplanPlan, element: Element,
              leseversion: KatplanVersion | None = None) -> dict:
    version, anhaenge = _lesedaten(db, user, plan, element, leseversion)
    teile = [_klartext(element.beschreibung_html)]
    daten = _json(element.daten_json, {})
    for feld, label in (("schritte", "Schritte"), ("bedarf", "Bedarf")):
        werte = daten.get(feld) if isinstance(daten, dict) else None
        if not isinstance(werte, list):
            werte = [werte] if werte else []
        zeilen = []
        for wert in werte:
            if isinstance(wert, dict):
                keys = ("text", "erledigt_hinweis") if feld == "schritte" else ("bezeichnung", "menge", "einheit")
                wert = " ".join(str(wert[k]) for k in keys if isinstance(wert.get(k), (str, int, float)))
            if isinstance(wert, (str, int, float)) and wert:
                zeilen.append("- " + _klartext(str(wert)))
        if zeilen:
            teile.append(label + ":\n" + "\n".join(zeilen))
    vnummer = version.versionsnummer if version else plan.version
    plantitel = _json(version.plan_json, {}).get("titel", plan.titel) if version else plan.titel
    teile.append(f"Quelle: {plantitel}, {element.kennung or ''}, Stand v{vnummer}")
    auswahl = []
    for a in anhaenge:
        name = _anhang_name(db, plan, a)
        datei = db.query(KatplanAnhang).filter_by(org_id=plan.org_id, plan_id=plan.id, uuid=a["uuid"]).first()
        auswahl.append({"uuid": a["uuid"], "art": a["art"], "name": name,
                        "groesse": datei.groesse_bytes if datei else 0})
        if a["art"] == "link":
            teile.append(f"Link: {name} - {a.get('url') or ''}")
        elif a["art"] == "objekt_dokument":
            teile.append(f"Dokument: {name}")
    return {"titel": " ".join(filter(None, (element.kennung, element.titel)))[:500],
            "text": "\n\n".join(t for t in teile if t), "anhaenge": auswahl,
            "quellseiten": _quellseiten(db, plan, element)}


def uebernehmen(db: Session, user: User, plan: KatplanPlan, element: Element, incident_id: int, *,
                art: Art, titel: str, text: str, anhang_uuids: list[str], quellseiten_nrs: list[int],
                meldung_status: str = "meldung", leseversion: KatplanVersion | None = None) -> UebernahmeErgebnis:
    if not has_role(user, "incident_leader", "admin", "recorder"):
        raise HTTPException(403, "Keine Einsatzrolle.")
    incident = visible_incidents_q(db, user).filter(Incident.id == incident_id).one_or_none()
    if incident is None:
        raise HTTPException(404, "Einsatz nicht gefunden.")
    if incident.status != "active":
        raise HTTPException(409, "Einsatz ist geschlossen.")
    if art not in ("auftrag", "meldung") or not titel.strip() or len(titel.strip()) > 500:
        raise HTTPException(409, "Art oder Titel ungültig (1 bis 500 Zeichen).")
    v = vorschlag(db, user, plan, element, leseversion)
    anhaenge = {a["uuid"]: a for a in v["anhaenge"]}
    seiten = {s["nr"]: s for s in v["quellseiten"]}
    if (not set(anhang_uuids) <= anhaenge.keys() or not set(quellseiten_nrs) <= seiten.keys()
            or any(type(nr) is not int for nr in quellseiten_nrs)):
        raise HTTPException(403, "Anhang oder Quellseite nicht sichtbar.")
    karte: Task | Message
    if art == "auftrag":
        karte = incident_service.add_task(db, incident, titel.strip(), text or None, user_id=user.id)
        karte.source = "katplan"
        incident_service.prepend_card(db, karte.column_id, "task", karte.id)
        ergebnis = UebernahmeErgebnis(art, incident, incident.id, task_id=karte.id)
    else:
        karte = incident_service.add_message(
            db, incident, title=titel.strip(), detail=text or None, status=meldung_status,
            source="katplan", author_name=user.display_name or user.username, user_id=user.id,
        )
        ergebnis = UebernahmeErgebnis(art, incident, incident.id, message_id=karte.id, message=karte)
    ergebnis.column_id, ergebnis.uid, ergebnis.vehicle_uid = karte.column_id, karte.id, karte.vehicle_id

    def speichern(raw: bytes, name: str | None, mime: str | None) -> None:
        if art == "auftrag":
            run_async(media_service.store_bytes_for_task, raw, name, mime, karte, db,
                      user.org_id, user=user, allow_datei=True)
        else:
            run_async(media_service.store_bytes_for_message, raw, name, mime, karte, db,
                      user.org_id, user=user, allow_datei=True)
        ergebnis.medien_anzahl += 1

    for uuid in dict.fromkeys(anhang_uuids):
        if anhaenge[uuid]["art"] not in ("foto", "datei"):
            continue
        a = db.query(KatplanAnhang).filter_by(org_id=plan.org_id, plan_id=plan.id, uuid=uuid).one_or_none()
        try:
            if a is None or not a.pfad:
                raise OSError("Anhangdatei fehlt.")
            speichern(absolute_pfad(a.pfad).read_bytes(), a.dateiname, a.mime)
        except (HTTPException, OSError, ValueError) as exc:
            ergebnis.fehler.append(f"{anhaenge[uuid]['name']}: {exc.detail if isinstance(exc, HTTPException) else exc}")
    for nr in dict.fromkeys(quellseiten_nrs):
        s = db.query(KatplanDokumentSeite).join(KatplanDokument).filter(
            KatplanDokument.uuid == seiten[nr]["dokument_uuid"], KatplanDokument.org_id == plan.org_id,
            KatplanDokument.plan_id == plan.id, KatplanDokumentSeite.org_id == plan.org_id,
            KatplanDokumentSeite.plan_id == plan.id, KatplanDokumentSeite.seiten_nr == nr,
        ).one()
        try:
            if s.bild_pfad:
                raw = katplan_quellbild_service.ausschnitt(s, [0, 0, 1, 1])
                speichern(raw, seiten[nr]["name"] + ".jpg", "image/jpeg")
            else:
                speichern(absolute_pfad(s.einzel_pdf_pfad).read_bytes(), seiten[nr]["name"] + ".pdf", "application/pdf")
        except (HTTPException, OSError, ValueError) as exc:
            ergebnis.fehler.append(f"{seiten[nr]['name']}: {exc.detail if isinstance(exc, HTTPException) else exc}")
    version, _ = _lesedaten(db, user, plan, element, leseversion)
    element_uuid = element.element_uuid if isinstance(element, KatplanVersionElement) else element.uuid
    write_audit(db, "einsatz.katplan_uebernommen", org_id=user.org_id, user_id=user.id,
                incident_id=incident.id, entity_type="task" if art == "auftrag" else "message",
                entity_id=karte.id, payload={"plan_id": plan.id,
                    "element_uuid": element_uuid,
                    "version": version.versionsnummer if version else plan.version})
    db.commit()
    # Alle fuer den Broadcast benoetigten Attribute vor Verlassen der Session laden.
    db.refresh(incident)
    if ergebnis.message is not None:
        db.refresh(ergebnis.message)
    return ergebnis


async def broadcast_uebernahme(ergebnis: UebernahmeErgebnis) -> None:
    from app.services.broadcast import manager

    if ergebnis.message is not None:
        await incident_service.broadcast_message_created(ergebnis.incident_id, ergebnis.message)
    else:
        await manager.broadcast(ergebnis.incident_id, {
            "type": "task_created", "task_id": ergebnis.task_id,
            "column_id": ergebnis.column_id, "vehicle_uid": ergebnis.vehicle_uid,
        })
        if ergebnis.medien_anzahl:
            await manager.broadcast(ergebnis.incident_id, {
                "type": "task_updated", "task_id": ergebnis.task_id,
                "kind": "task", "uid": ergebnis.uid, "column_id": ergebnis.column_id,
            })
