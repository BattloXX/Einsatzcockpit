"""Tenantgebundene Anhaenge und unveraenderliche Versionsreferenzen."""

import json
import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.katplan import (
    KatplanAbschnitt,
    KatplanAnhang,
    KatplanElement,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
    _utcnow,
)
from app.models.objekt import ObjektDokument
from app.models.user import User
from app.services.katplan_service import markiere_ueberarbeitung, protokolliere
from app.services.katplan_ziel_service import ziel_laden, ziel_verfuegbar
from app.services.katplan_zugriff import sieht_vertraulich
from app.services.media_service import delete_katplan_anhang_dateien, store_upload_for_katplan_anhang

SNAPSHOT_FELDER = ("uuid", "art", "beschriftung", "dateiname", "mime", "url",
                   "objekt_dokument_id", "seiten", "vertraulich")
HASH_FELDER = ("uuid", "art", "beschriftung", "url", "objekt_dokument_id", "seiten", "vertraulich")


def _bindung(db: Session, plan: KatplanPlan, user: User, element: KatplanElement | None = None,
             abschnitt: KatplanAbschnitt | None = None) -> None:
    if plan.org_id is None or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    if getattr(user, "org_id", plan.org_id) != plan.org_id:
        raise ValueError("Benutzer gehört nicht zu diesem Plan.")
    for obj in (element, abschnitt):
        if obj is not None and (obj.plan_id != plan.id or obj.org_id != plan.org_id or obj in db.deleted):
            raise ValueError("Element/Kapitel gehört nicht zu diesem Plan.")
    if element and abschnitt and element.abschnitt_id != abschnitt.id:
        raise ValueError("Element gehört nicht zu diesem Kapitel.")


def _metadaten(beschriftung: str, urheber: str, aufnahme_datum: date | None) -> None:
    if len(beschriftung) > 300 or len(urheber) > 150:
        raise ValueError("Beschriftung (300) oder Urheber (150) ist zu lang.")
    if aufnahme_datum is not None and not isinstance(aufnahme_datum, date):
        raise ValueError("Ungültiges Aufnahmedatum.")


def _neu(db: Session, plan: KatplanPlan, user: User, *, element: KatplanElement | None = None,
         abschnitt: KatplanAbschnitt | None = None, beschriftung: str = "", urheber: str = "",
         aufnahme_datum: date | None = None, vertraulich: bool = False) -> KatplanAnhang:
    _bindung(db, plan, user, element, abschnitt)
    _metadaten(beschriftung, urheber, aufnahme_datum)
    return KatplanAnhang(org_id=plan.org_id, plan_id=plan.id, uuid=str(uuid4()),
                        element_id=element.id if element else None,
                        abschnitt_id=abschnitt.id if abschnitt else None,
                        beschriftung=beschriftung, urheber=urheber, aufnahme_datum=aufnahme_datum,
                        vertraulich=vertraulich, erstellt_von_id=user.id)


def _geaendert(db: Session, plan: KatplanPlan, user: User, anhang: KatplanAnhang, aktion: str,
               alt: str | None = None, *, quelle: str = "ui") -> None:
    protokolliere(db, plan, "anhang", aktion, user, feld=anhang.uuid,
                  alt=alt, neu=json.dumps(_protokoll_daten(anhang), ensure_ascii=False),
                  element_id=anhang.element_id, quelle=quelle)
    markiere_ueberarbeitung(db, plan, user, quelle=quelle)
    plan.version += 1
    plan.aktualisiert_am = _utcnow()
    plan.aktualisiert_von_id = user.id
    if anhang.element_id:
        element = db.query(KatplanElement).filter_by(
            org_id=plan.org_id, plan_id=plan.id, id=anhang.element_id,
        ).one_or_none()
        if element:
            element.version += 1
            element.aktualisiert_von_id = user.id


def anhang_hochladen(db: Session, plan: KatplanPlan, user: User, datei_bytes: bytes,
                     dateiname: str, content_type: str | None, *, element: KatplanElement | None = None,
                     abschnitt: KatplanAbschnitt | None = None, beschriftung: str = "", urheber: str = "",
                     aufnahme_datum: date | None = None, vertraulich: bool = False,
                     quelle: str = "ui") -> KatplanAnhang:
    anhang = _neu(db, plan, user, element=element, abschnitt=abschnitt, beschriftung=beschriftung,
                  urheber=urheber, aufnahme_datum=aufnahme_datum, vertraulich=vertraulich)
    store_upload_for_katplan_anhang(datei_bytes, dateiname, content_type, anhang, plan, db)
    _geaendert(db, plan, user, anhang, "hochgeladen", quelle=quelle)
    return anhang


def link_hinzufuegen(db: Session, plan: KatplanPlan, user: User, url: str, beschriftung: str = "",
                     *, element: KatplanElement | None = None, abschnitt: KatplanAbschnitt | None = None,
                     urheber: str = "", aufnahme_datum: date | None = None,
                     vertraulich: bool = False) -> KatplanAnhang:
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid or len(url) > 1000 or any(ord(c) < 32 for c in url):
        raise ValueError("Nur gültige HTTP/HTTPS-Links mit höchstens 1000 Zeichen sind erlaubt.")
    anhang = _neu(db, plan, user, element=element, abschnitt=abschnitt, beschriftung=beschriftung,
                  urheber=urheber, aufnahme_datum=aufnahme_datum, vertraulich=vertraulich)
    anhang.art, anhang.url = "link", url
    db.add(anhang)
    db.flush()
    _geaendert(db, plan, user, anhang, "verlinkt")
    return anhang


def objektdokument_verlinken(db: Session, plan: KatplanPlan, user: User, objekt_dokument_id: int,
                            seiten: str = "", *, element: KatplanElement | None = None,
                            abschnitt: KatplanAbschnitt | None = None, beschriftung: str = "",
                            urheber: str = "", aufnahme_datum: date | None = None,
                            vertraulich: bool = False) -> KatplanAnhang:
    dokument = db.query(ObjektDokument).filter_by(id=objekt_dokument_id, org_id=plan.org_id,
                                                 ist_aktuelle_version=True).one_or_none()
    if (not ziel_verfuegbar(db, plan.org_id, "objekt") or dokument is None
            or not dokument.ist_aktuelle_version or dokument.org_id != plan.org_id
            or ziel_laden(db, plan.org_id, "objekt", dokument.objekt_id) is None):
        raise ValueError("Objektdokument nicht verfügbar.")
    if seiten:
        match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", seiten)
        if not match or len(seiten) > 30:
            raise ValueError("Ungültiger Seitenbereich.")
        start, ende = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= ende <= dokument.seitenzahl:
            raise ValueError("Seitenbereich liegt außerhalb des Dokuments.")
    anhang = _neu(db, plan, user, element=element, abschnitt=abschnitt, beschriftung=beschriftung,
                  urheber=urheber, aufnahme_datum=aufnahme_datum, vertraulich=vertraulich)
    anhang.art, anhang.objekt_dokument_id, anhang.seiten = "objekt_dokument", dokument.id, seiten
    db.add(anhang)
    db.flush()
    _geaendert(db, plan, user, anhang, "verlinkt")
    return anhang


def _pruefe_anhang(db: Session, plan: KatplanPlan, user: User, anhang: KatplanAnhang) -> None:
    _bindung(db, plan, user)
    if anhang.plan_id != plan.id or anhang.org_id != plan.org_id or anhang.geloescht_am:
        raise ValueError("Anhang nicht verfügbar.")


def anhang_aendern(db: Session, plan: KatplanPlan, user: User, anhang: KatplanAnhang, *,
                   beschriftung: str, urheber: str, aufnahme_datum: date | None,
                   vertraulich: bool, sort: int) -> None:
    _pruefe_anhang(db, plan, user, anhang)
    _metadaten(beschriftung, urheber, aufnahme_datum)
    alt = json.dumps(_protokoll_daten(anhang), ensure_ascii=False)
    anhang.beschriftung, anhang.urheber, anhang.aufnahme_datum = beschriftung, urheber, aufnahme_datum
    anhang.vertraulich, anhang.sort = vertraulich, sort
    _geaendert(db, plan, user, anhang, "geaendert", alt)


def _protokoll_daten(anhang: KatplanAnhang) -> dict:
    return {**anhang_snapshot(anhang), "urheber": anhang.urheber, "sort": anhang.sort,
            "aufnahme_datum": anhang.aufnahme_datum.isoformat() if anhang.aufnahme_datum else None}


def anhang_snapshot(anhang: KatplanAnhang) -> dict:
    return {feld: getattr(anhang, feld) for feld in SNAPSHOT_FELDER}


def anhaenge_aus_version(db: Session, plan: KatplanPlan, version_id: int) -> list[dict]:
    version = db.query(KatplanVersion).filter_by(
        org_id=plan.org_id, plan_id=plan.id, id=version_id,
    ).one_or_none()
    if version is None:
        return []
    daten = list(json.loads(version.plan_json).get("anhaenge", []))
    for element in db.query(KatplanVersionElement).filter_by(org_id=plan.org_id, version_id=version.id):
        daten.extend({**a, "vertraulich": a.get("vertraulich", False) or element.vertraulich}
                     for a in json.loads(element.anhaenge_json))
    return daten


def anhang_entfernen(db: Session, plan: KatplanPlan, user: User, anhang: KatplanAnhang) -> None:
    _pruefe_anhang(db, plan, user, anhang)
    referenziert = any(a["uuid"] == anhang.uuid
                      for version in db.query(KatplanVersion).filter_by(org_id=plan.org_id, plan_id=plan.id)
                      for a in anhaenge_aus_version(db, plan, version.id))
    if referenziert:
        anhang.geloescht_am = _utcnow()
    else:
        delete_katplan_anhang_dateien(anhang, db)
        db.delete(anhang)
    _geaendert(db, plan, user, anhang, "entfernt")
    db.flush()


def sichtbare_anhaenge(db: Session, plan: KatplanPlan, user: User, *, element_id: int | None = None,
                       abschnitt_id: int | None = None,
                       ebene: Literal["element", "abschnitt", "plan"] = "element") -> list[KatplanAnhang]:
    if user.org_id != plan.org_id:
        return []
    db.flush()
    query = db.query(KatplanAnhang).filter_by(org_id=plan.org_id, plan_id=plan.id, geloescht_am=None)
    if ebene == "element":
        if element_id is None:
            return []
        element = db.query(KatplanElement).filter_by(
            id=element_id, org_id=plan.org_id, plan_id=plan.id,
        ).one_or_none()
        if element is None or (element.vertraulich and not sieht_vertraulich(user)):
            return []
        query = query.filter_by(element_id=element_id)
    elif ebene == "abschnitt":
        if abschnitt_id is None:
            return []
        query = query.filter_by(element_id=None, abschnitt_id=abschnitt_id)
    elif ebene == "plan":
        query = query.filter_by(element_id=None, abschnitt_id=None)
    else:
        raise ValueError("Ungültige Anhangsebene.")
    if not sieht_vertraulich(user):
        query = query.filter_by(vertraulich=False)
    return query.order_by(KatplanAnhang.sort, KatplanAnhang.id).all()
