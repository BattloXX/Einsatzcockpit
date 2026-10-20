"""Orggebundene Stammdatenlinks, Beziehungen und zentrale Leseregeln."""

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.katplan import KatplanElement, KatplanLink, KatplanPlan, KatplanRelation, _utcnow
from app.models.kontakt import Kontakt
from app.models.user import User
from app.services.katplan_service import aktualisiere_element_suchtext, markiere_ueberarbeitung, protokolliere
from app.services.katplan_ziel_service import _ZIELE, _anzeige, ziel_laden, ziel_verfuegbar
from app.services.katplan_ziel_service import ziel_suchen as ziel_suchen
from app.services.kontakt_access_service import contact_summary

KATPLAN_LINK_ROLLEN = {
    "zu_verstaendigen": "Zu verständigen", "verantwortlich": "Verantwortlich",
    "zustaendig": "Zuständig", "ausfuehrend": "Ausführend", "info": "Zur Information",
    "sonstiges": "Sonstiges",
}
KATPLAN_RELATION_ARTEN = {
    "siehe_auch": "Siehe auch", "folgt_auf": "Folgt auf",
    "abhaengig_von": "Abhängig von", "eskaliert_zu": "Eskaliert zu",
}


def _pruefe_bindung(plan: KatplanPlan, element: KatplanElement | None = None) -> None:
    if plan.org_id is None or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    if element is not None and (element.org_id != plan.org_id or element.plan_id != plan.id):
        raise ValueError("Element gehört nicht zu diesem Plan.")


def _geaendert(db: Session, plan: KatplanPlan, user: User, element: KatplanElement | None,
               *, quelle: str = "ui") -> None:
    markiere_ueberarbeitung(db, plan, user, quelle=quelle)
    plan.version += 1
    plan.aktualisiert_am = _utcnow()
    plan.aktualisiert_von_id = user.id
    if element is not None:
        element.version += 1
        element.aktualisiert_von_id = user.id
        aktualisiere_element_suchtext(element)


def link_anlegen(db: Session, plan: KatplanPlan, user: User, *, element: KatplanElement | None = None,
                 ziel_typ: str, ziel_id: int, rolle: str, hinweis: str = "", quelle: str = "ui") -> KatplanLink:
    _pruefe_bindung(plan, element)
    if ziel_typ not in _ZIELE or rolle not in KATPLAN_LINK_ROLLEN:
        raise ValueError("Unbekannter Zieltyp oder unbekannte Rolle.")
    if len(hinweis) > 500:
        raise ValueError("Der Hinweis darf höchstens 500 Zeichen enthalten.")
    ziel = ziel_laden(db, plan.org_id, ziel_typ, ziel_id)
    if ziel is None or not ziel_verfuegbar(db, plan.org_id, ziel_typ):
        raise ValueError("Ziel nicht verfügbar.")
    element_id = element.id if element else None
    if db.query(KatplanLink).filter_by(org_id=plan.org_id, plan_id=plan.id, element_id=element_id,
                                       ziel_typ=ziel_typ, ziel_id=ziel_id, rolle=rolle).first():
        raise ValueError("Diese Verknüpfung existiert bereits.")
    link = KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=element_id,
                       ziel_typ=ziel_typ, ziel_id=ziel_id, rolle=rolle, hinweis=hinweis,
                       erstellt_von_id=user.id)
    db.add(link)
    db.flush()
    protokolliere(db, plan, "link", "verknuepft", user, feld=ziel_typ,
                  neu=_anzeige(ziel)[0], element_id=element_id, quelle=quelle)
    _geaendert(db, plan, user, element, quelle=quelle)
    return link


def link_entfernen(db: Session, plan: KatplanPlan, user: User, link: KatplanLink, *, quelle: str = "ui") -> None:
    _pruefe_bindung(plan)
    if link.org_id != plan.org_id or link.plan_id != plan.id:
        raise ValueError("Verknüpfung gehört nicht zu diesem Plan.")
    element = db.query(KatplanElement).filter_by(id=link.element_id, org_id=plan.org_id, plan_id=plan.id).first()
    titel = _anzeige(ziel_laden(db, plan.org_id, link.ziel_typ, link.ziel_id))[0]
    protokolliere(db, plan, "link", "entknuepft", user, feld=link.ziel_typ, alt=titel,
                  element_id=link.element_id, quelle=quelle)
    db.delete(link)
    db.flush()
    _geaendert(db, plan, user, element, quelle=quelle)


def relation_anlegen(db: Session, plan: KatplanPlan, user: User, von_element: KatplanElement,
                     zu_element_uuid: str, art: str, hinweis: str = "", *, quelle: str = "ui") -> KatplanRelation:
    _pruefe_bindung(plan, von_element)
    if art not in KATPLAN_RELATION_ARTEN or len(hinweis) > 300:
        raise ValueError("Ungültige Beziehungsart oder zu langer Hinweis.")
    ziel = db.query(KatplanElement).filter_by(org_id=plan.org_id, uuid=zu_element_uuid).one_or_none()
    if ziel is None or ziel.id == von_element.id:
        raise ValueError("Zielelement fehlt oder Selbstbezug.")
    if db.query(KatplanRelation).filter_by(org_id=plan.org_id, von_element_id=von_element.id,
                                          zu_element_uuid=zu_element_uuid, art=art).first():
        raise ValueError("Diese Beziehung existiert bereits.")
    relation = KatplanRelation(org_id=plan.org_id, plan_id=plan.id, von_element_id=von_element.id,
                               zu_element_uuid=zu_element_uuid, art=art, hinweis=hinweis)
    db.add(relation)
    db.flush()
    protokolliere(db, plan, "relation", "verknuepft", user, feld=art, neu=ziel.titel,
                  element_id=von_element.id, quelle=quelle)
    _geaendert(db, plan, user, von_element, quelle=quelle)
    return relation


def relation_entfernen(db: Session, plan: KatplanPlan, user: User, relation: KatplanRelation,
                       *, quelle: str = "ui") -> None:
    _pruefe_bindung(plan)
    if relation.org_id != plan.org_id or relation.plan_id != plan.id:
        raise ValueError("Beziehung gehört nicht zu diesem Plan.")
    element = db.query(KatplanElement).filter_by(id=relation.von_element_id, org_id=plan.org_id,
                                               plan_id=plan.id).one()
    protokolliere(db, plan, "relation", "entknuepft", user, feld=relation.art,
                  alt=relation.zu_element_uuid, element_id=element.id, quelle=quelle)
    db.delete(relation)
    db.flush()
    _geaendert(db, plan, user, element, quelle=quelle)


def link_ansicht(db: Session, link: KatplanLink, *, voll: bool,
                 user_rechte: dict[str, bool] | None = None) -> dict:
    """user_rechte enthaelt je Zieltyp das gepruefte Oeffnungsrecht; ohne Recht keine URL.

    voll kommt vom Aufrufer ueber can_view_kontakt_directory(user).
    Die Telefonfreigabe fuer eingeschraenkte Leser wird ausschliesslich hier entschieden.
    """
    ziel = ziel_laden(db, link.org_id, link.ziel_typ, link.ziel_id)
    titel, untertitel = _anzeige(ziel)
    telefone = []
    if isinstance(ziel, Kontakt) and (voll or ziel.einsatzrelevant):
        telefone = contact_summary(ziel, full=voll)["telefone"]
    url = None
    if ziel is not None and (user_rechte or {}).get(link.ziel_typ, False) and ziel_verfuegbar(
        db, link.org_id, link.ziel_typ,
    ):
        if link.ziel_typ == "kontakt" and voll:
            url = f"/kontakte/{ziel.id}"
        elif link.ziel_typ == "objekt":
            url = f"/objekte/{ziel.id}"
        # Fuer die uebrigen Ziele gibt es keine eigenstaendige Lesedetailroute.
    return {"typ": link.ziel_typ, "rolle_label": KATPLAN_LINK_ROLLEN.get(link.rolle, link.rolle),
            "titel": titel, "untertitel": untertitel, "url": url, "telefone": telefone, "fehlt": ziel is None}


def link_snapshot(db: Session, link: KatplanLink) -> dict:
    ansicht = link_ansicht(db, link, voll=True)
    return {"ziel_typ": link.ziel_typ, "ziel_id": link.ziel_id, "rolle": link.rolle, "hinweis": link.hinweis,
            "snapshot": {f: ansicht[f] for f in ("titel", "untertitel", "telefone")}}


def links_aus_version(db: Session, version_element_links: str | list[dict], *, voll: bool,
                     user_rechte: dict[str, Any]) -> list[dict]:
    """Org aus dem vertrauenswuerdigen Aufruferkontext, niemals aus Snapshot-Daten.

    user_rechte: org_id sowie je Zieltyp das gepruefte Oeffnungsrecht.
    Ohne explizite org_id wird der Tenant-Kontext der Session verwendet.
    """
    links = json.loads(version_element_links) if isinstance(version_element_links, str) else version_element_links
    org_id = user_rechte.get("org_id", db.info.get("current_org_id"))
    ergebnis = []
    for daten in links:
        link = KatplanLink(org_id=org_id, ziel_typ=daten["ziel_typ"], ziel_id=daten["ziel_id"],
                           rolle=daten["rolle"], hinweis=daten.get("hinweis", ""))
        ansicht = link_ansicht(db, link, voll=voll, user_rechte=user_rechte)
        snapshot = daten.get("snapshot", {})
        # Vergleich mit vollem Live-Inhalt; private Nummern verlassen diese Funktion nicht.
        live = link_ansicht(db, link, voll=True)
        ansicht["geaendert_seit_freigabe"] = any(live[f] != snapshot.get(f) for f in ("titel", "telefone"))
        if ansicht["fehlt"]:
            ansicht.update(titel=snapshot.get("titel", ansicht["titel"]),
                           untertitel=snapshot.get("untertitel", ""),
                           telefone=snapshot.get("telefone", []) if voll else [])
        ergebnis.append(ansicht)
    return ergebnis
