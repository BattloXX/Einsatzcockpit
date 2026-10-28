"""Tenantgebundene Anhaenge und unveraenderliche Versionsreferenzen."""

import json
import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.katplan import (
    KatplanAbschnitt,
    KatplanAnhang,
    KatplanDokument,
    KatplanElement,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
    _utcnow,
)
from app.models.master import OrgSettings
from app.models.objekt import ObjektDokument
from app.models.user import User
from app.services.db_transactions import ist_wiederholbar
from app.services.katplan_service import KatplanKonflikt, markiere_ueberarbeitung, protokolliere, pruefe_version
from app.services.katplan_ziel_service import ziel_laden, ziel_verfuegbar
from app.services.katplan_zugriff import darf_bearbeiten, sieht_vertraulich
from app.services.media_service import delete_katplan_anhang_dateien, store_upload_for_katplan_anhang

PFLEGE_FELDER = ("pruefdatum", "gueltig_bis", "pruefintervall_monate", "zustaendig_user_id")
SNAPSHOT_FELDER = ("uuid", "art", "beschriftung", "dateiname", "mime", "url",
                   "objekt_dokument_id", "seiten", "vertraulich", "sort", *PFLEGE_FELDER)
HASH_FELDER = ("uuid", "art", "beschriftung", "url", "objekt_dokument_id", "seiten", "vertraulich",
               "sort", *PFLEGE_FELDER)


def dokument_pruefstatus(daten: dict, heute: date) -> dict:
    from app.services.katplan_freigabe_service import naechste_pruefung

    pruefdatum = daten.get("pruefdatum")
    gueltig = daten.get("gueltig_bis")
    if isinstance(pruefdatum, str):
        pruefdatum = date.fromisoformat(pruefdatum)
    if isinstance(gueltig, str):
        gueltig = date.fromisoformat(gueltig)
    intervall = daten.get("pruefintervall_monate")
    termin = naechste_pruefung(pruefdatum, intervall) if pruefdatum and intervall else None
    return {"naechste_pruefung": termin.isoformat() if termin else None,
            "pruefung_faellig": bool(termin and termin <= heute),
            "pruefangabe_fehlt": not pruefdatum,
            "gueltigkeit_abgelaufen": bool(gueltig and gueltig < heute)}


def pflege_version(db: Session, plan: KatplanPlan, user: User, version: int) -> None:
    """Gemeinsamer Schreibschutz für Web und MCP, einschließlich konkurrierender Anfragen."""
    if not darf_bearbeiten(user):
        raise ValueError("Keine Berechtigung zur Dokumentenpflege.")
    try:
        _bindung(db, plan, user)
        db.refresh(plan, with_for_update=True)
    except OperationalError as exc:
        # MariaDB kann unter REPEATABLE READ bereits das sperrende Neulesen ablehnen.
        # Der Aufrufer rollt zurück und zeigt denselben Konflikt wie bei alter Planversion.
        if ist_wiederholbar(exc):
            raise KatplanKonflikt("Der Arbeitsstand wird gleichzeitig geändert. Bitte neu laden.") from exc
        raise
    _bindung(db, plan, user)
    pruefe_version(plan, version)


def dokumentbibliothek(db: Session, user: User) -> list[dict]:
    if not darf_bearbeiten(user):
        return []
    query = db.query(KatplanAnhang).join(KatplanPlan, KatplanPlan.id == KatplanAnhang.plan_id).filter(
        KatplanAnhang.org_id == user.org_id, KatplanPlan.org_id == user.org_id,
        KatplanPlan.status != "archiviert", KatplanAnhang.geloescht_am.is_(None),
        KatplanAnhang.art.in_(["datei", "foto"]),
    ).order_by(KatplanAnhang.id)
    result, gesehen = [], set()
    for a in query:
        element = db.query(KatplanElement).filter_by(
            org_id=user.org_id, id=a.element_id).first() if a.element_id else None
        if (a.vertraulich or element and element.vertraulich) and not sieht_vertraulich(user):
            continue
        if a.pfad in gesehen:
            continue
        gesehen.add(a.pfad)
        result.append({"referenz": f"anhang:{a.uuid}", "titel": a.beschriftung or a.dateiname,
                       "plan_id": a.plan_id, "vertraulich": a.vertraulich or bool(element and element.vertraulich)})
    for d in db.query(KatplanDokument).join(KatplanPlan, KatplanPlan.id == KatplanDokument.plan_id).filter(
        KatplanDokument.org_id == user.org_id, KatplanPlan.org_id == user.org_id,
        KatplanPlan.status != "archiviert", KatplanDokument.archiviert.is_(False),
        KatplanDokument.ist_aktuelle_version.is_(True),
    ).order_by(KatplanDokument.id):
        result.append({"referenz": f"quelle:{d.uuid}", "titel": d.titel,
                       "plan_id": d.plan_id, "vertraulich": False})
    return result


def dokument_referenzieren(db: Session, plan: KatplanPlan, user: User, referenz: str, *,
                           element: KatplanElement | None = None, abschnitt: KatplanAbschnitt | None = None,
                           beschriftung: str = "", seiten: str = "", quelle: str = "ui") -> KatplanAnhang:
    """Neue Zuordnung auf unveränderliche Bytes; kein Kopieren und kein Überschreiben."""
    from app.services.dokumentviewer_service import seiten_auswahl
    from app.services.katplan_quelle_service import absolute_pfad

    _bindung(db, plan, user, element, abschnitt)
    eintrag = next((a for a in dokumentbibliothek(db, user) if a["referenz"] == referenz), None)
    if eintrag is None:
        raise ValueError("Dokument nicht verfügbar.")
    art, uid = referenz.split(":", 1)
    original: KatplanAnhang | KatplanDokument
    if art == "anhang":
        original = db.query(KatplanAnhang).filter_by(org_id=plan.org_id, uuid=uid).one()
    else:
        original = db.query(KatplanDokument).filter_by(org_id=plan.org_id, uuid=uid).one()
    neu = _neu(db, plan, user, element=element, abschnitt=abschnitt,
               beschriftung=beschriftung or eintrag["titel"] or "Dokument",
               vertraulich=eintrag["vertraulich"])
    if isinstance(original, KatplanAnhang):
        for feld in ("art", "mime", "dateiname", "pfad", "thumb_pfad", "breite", "hoehe",
                     "groesse_bytes", "datei_hash", "seiten", *PFLEGE_FELDER):
            setattr(neu, feld, getattr(original, feld))
    else:
        neu.art, neu.mime = "datei", "application/pdf"
        neu.dateiname, neu.pfad = original.dateiname_original, original.pfad
        neu.groesse_bytes = original.groesse_bytes
    if not neu.pfad or not absolute_pfad(neu.pfad).is_file():
        raise ValueError("Dokumentdatei nicht verfügbar.")
    bereich = seiten or neu.seiten or ""
    if bereich:
        if neu.mime != "application/pdf":
            raise ValueError("Seitenbereiche sind nur für PDF-Dokumente möglich.")
        import io

        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            count = len(PdfReader(io.BytesIO(absolute_pfad(neu.pfad).read_bytes())).pages)
            seiten_auswahl(bereich, count)
        except (OSError, ValueError, PyPdfError) as exc:
            raise ValueError("Dokument oder Seitenbereich ist ungültig.") from exc
        if len(bereich) > 30:
            raise ValueError("Seitenbereich darf höchstens 30 Zeichen enthalten.")
        neu.seiten = bereich
    db.add(neu)
    db.flush()
    _geaendert(db, plan, user, neu, "referenziert",
               alt=json.dumps({"quellreferenz": referenz}, ensure_ascii=False), quelle=quelle)
    return neu


def _bindung(db: Session, plan: KatplanPlan, user: User, element: KatplanElement | None = None,
             abschnitt: KatplanAbschnitt | None = None) -> None:
    if plan.org_id is None or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    if getattr(user, "org_id", plan.org_id) != plan.org_id:
        raise ValueError("Benutzer gehört nicht zu diesem Plan.")
    db.query(OrgSettings).filter_by(org_id=plan.org_id).with_for_update().first()
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
                   vertraulich: bool, sort: int, pflege: dict | None = None, quelle: str = "ui") -> None:
    _pruefe_anhang(db, plan, user, anhang)
    _metadaten(beschriftung, urheber, aufnahme_datum)
    pflege = pflege or {}
    if set(pflege) - set(PFLEGE_FELDER):
        raise ValueError("Unbekannte Prüfangabe.")
    for feld in ("pruefdatum", "gueltig_bis"):
        if pflege.get(feld) is not None and not isinstance(pflege[feld], date):
            raise ValueError("Ungültiges Datum.")
    intervall = pflege.get("pruefintervall_monate")
    if intervall is not None and (type(intervall) is not int or not 1 <= intervall <= 60):
        raise ValueError("Prüfintervall muss zwischen 1 und 60 Monaten liegen.")
    person = pflege.get("zustaendig_user_id")
    if person is not None and (type(person) is not int or not db.query(User.id).filter_by(
        org_id=plan.org_id, id=person, active=True,
    ).first()):
        raise ValueError("Zuständige Person nicht verfügbar.")
    alt = json.dumps(_protokoll_daten(anhang), ensure_ascii=False)
    anhang.beschriftung, anhang.urheber, anhang.aufnahme_datum = beschriftung, urheber, aufnahme_datum
    anhang.vertraulich, anhang.sort = vertraulich, sort
    for feld, wert in pflege.items():
        setattr(anhang, feld, wert)
    _geaendert(db, plan, user, anhang, "geaendert", alt, quelle=quelle)


def _protokoll_daten(anhang: KatplanAnhang) -> dict:
    return {**anhang_snapshot(anhang), "urheber": anhang.urheber, "sort": anhang.sort,
            "aufnahme_datum": anhang.aufnahme_datum.isoformat() if anhang.aufnahme_datum else None}


def anhang_snapshot(anhang: KatplanAnhang) -> dict:
    return {feld: wert.isoformat() if isinstance(wert := getattr(anhang, feld), date) else wert
            for feld in SNAPSHOT_FELDER}


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
