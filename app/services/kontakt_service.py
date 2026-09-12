"""Geschaeftslogik fuer organisationsweite zentrale Kontakte."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload

from app.core.telefon import telefon_normalisiert
from app.models.kontakt import (
    KONTAKT_TYP_PERSON,
    KONTAKT_TYP_STELLE,
    Kontakt,
    KontaktAnhang,
    KontaktExterneReferenz,
    KontaktKategorie,
    KontaktKategorieZuordnung,
    KontaktTelefon,
)
from app.models.objekt import ObjektKontakt, ObjektKontaktBenachrichtigung

PRO_SEITE = 50


class KontaktKonflikt(Exception):
    """Das Formular basiert auf einer nicht mehr aktuellen Kontaktversion."""


@dataclass
class KontaktMergeErgebnis:
    kontakt: Kontakt
    freigabe_konflikte: list[str]


def _mit_details(query):
    return query.options(
        selectinload(Kontakt.telefone),
        selectinload(Kontakt.kategorien).selectinload(KontaktKategorieZuordnung.kategorie),
    )


def get_kontakt(db: Session, kontakt_id: int, *, include_archiviert: bool = False) -> Kontakt | None:
    query = _mit_details(db.query(Kontakt)).filter(Kontakt.id == kontakt_id)
    if not include_archiviert:
        query = query.filter(Kontakt.archiviert.is_(False))
    return query.first()


def _werte(kontakt: Kontakt, daten: dict[str, Any], user_id: int | None) -> None:
    for feld in (
        "typ",
        "anzeigename",
        "vorname",
        "nachname",
        "funktion",
        "organisation",
        "email",
        "erreichbarkeit",
        "notizen",
    ):
        if feld in daten:
            wert = daten[feld]
            setattr(kontakt, feld, wert.strip() if isinstance(wert, str) else wert)
    if kontakt.typ not in (KONTAKT_TYP_PERSON, KONTAKT_TYP_STELLE):
        raise ValueError("Ungueltiger Kontakttyp")
    if not kontakt.anzeigename:
        kontakt.anzeigename = " ".join(x for x in (kontakt.vorname, kontakt.nachname) if x).strip()
    if not kontakt.anzeigename:
        raise ValueError("Bitte einen Anzeigenamen eingeben")
    kontakt.aktualisiert_von_id = user_id


def _kategorien_sync(db: Session, kontakt: Kontakt, namen: list[str], org_id: int) -> None:
    bereinigt = list(dict.fromkeys(name.strip() for name in namen if name.strip()))
    kategorien: list[KontaktKategorie] = []
    for name in bereinigt:
        kategorie = db.query(KontaktKategorie).filter(KontaktKategorie.name == name).first()
        if kategorie is None:
            kategorie = KontaktKategorie(org_id=org_id, name=name)
            db.add(kategorie)
            db.flush()
        kategorien.append(kategorie)
    # Vor dem erneuten Anhaengen flushen: bei gleicher Kategorie verletzt SQLAlchemy
    # sonst auf SQLite/MariaDB die Unique-Constraint, weil INSERT vor DELETE kommt.
    kontakt.kategorien[:] = []
    db.flush()
    kontakt.kategorien.extend(
        KontaktKategorieZuordnung(org_id=org_id, kategorie_id=kategorie.id) for kategorie in kategorien
    )


def _telefone_sync(kontakt: Kontakt, telefone: list[dict[str, Any]], org_id: int) -> None:
    zeilen = [zeile for zeile in telefone if str(zeile.get("nummer", "")).strip()]
    if len(zeilen) > 20:
        raise ValueError("Hoechstens 20 Telefonnummern sind erlaubt")
    kontakt.telefone[:] = [
        KontaktTelefon(
            org_id=org_id,
            nummer=str(zeile["nummer"]).strip(),
            label=str(zeile.get("label") or "").strip() or None,
            sort=index,
            bevorzugt=bool(zeile.get("bevorzugt")),
            sms_eignung=bool(zeile.get("sms_eignung")) if zeile.get("sms_eignung") is not None else None,
        )
        for index, zeile in enumerate(zeilen)
    ]


def create_kontakt(
    db: Session,
    daten: dict[str, Any],
    telefone: list[dict[str, Any]],
    kategorien: list[str],
    *,
    org_id: int,
    user_id: int | None,
) -> Kontakt:
    kontakt = Kontakt(org_id=org_id, erstellt_von_id=user_id, aktualisiert_von_id=user_id)
    _werte(kontakt, daten, user_id)
    _telefone_sync(kontakt, telefone, org_id)
    db.add(kontakt)
    db.flush()
    _kategorien_sync(db, kontakt, kategorien, org_id)
    db.commit()
    return get_kontakt(db, kontakt.id, include_archiviert=True)  # type: ignore[return-value]


def update_kontakt(
    db: Session,
    kontakt_id: int,
    daten: dict[str, Any],
    telefone: list[dict[str, Any]],
    kategorien: list[str],
    *,
    version: int,
    org_id: int,
    user_id: int | None,
) -> Kontakt:
    kontakt = get_kontakt(db, kontakt_id, include_archiviert=True)
    if kontakt is None:
        raise LookupError("Kontakt nicht gefunden")
    if kontakt.version != version:
        raise KontaktKonflikt()
    _werte(kontakt, daten, user_id)
    _telefone_sync(kontakt, telefone, org_id)
    _kategorien_sync(db, kontakt, kategorien, org_id)
    kontakt.version += 1
    db.commit()
    return get_kontakt(db, kontakt.id, include_archiviert=True)  # type: ignore[return-value]


def archive_kontakt(db: Session, kontakt_id: int, *, user_id: int | None) -> Kontakt:
    kontakt = get_kontakt(db, kontakt_id)
    if kontakt is None:
        raise LookupError("Kontakt nicht gefunden")
    kontakt.archiviert = True
    kontakt.aktiv = False
    kontakt.aktualisiert_von_id = user_id
    kontakt.version += 1
    db.commit()
    return kontakt


def list_kontakte(
    db: Session, *, q: str = "", typ: str = "all", kategorie_id: int | None = None, page: int = 1
) -> tuple[list[Kontakt], int]:
    query = _mit_details(db.query(Kontakt)).filter(Kontakt.archiviert.is_(False))
    if typ in (KONTAKT_TYP_PERSON, KONTAKT_TYP_STELLE):
        query = query.filter(Kontakt.typ == typ)
    if kategorie_id:
        query = query.join(KontaktKategorieZuordnung).filter(KontaktKategorieZuordnung.kategorie_id == kategorie_id)
    if q.strip():
        term = f"%{q.strip().lower()}%"
        nummer = telefon_normalisiert(q)
        query = query.outerjoin(KontaktTelefon).filter(
            or_(
                func.lower(Kontakt.anzeigename).like(term),
                func.lower(Kontakt.organisation).like(term),
                func.lower(Kontakt.funktion).like(term),
                func.lower(Kontakt.email).like(term),
                KontaktTelefon.nummer.like(term),
                KontaktTelefon.nummer_normalisiert.like(f"%{nummer}%"),
            )
        )
    total = query.distinct().count()
    kontakte = (
        query.distinct()
        .order_by(Kontakt.anzeigename, Kontakt.id)
        .offset(max(page - 1, 0) * PRO_SEITE)
        .limit(PRO_SEITE)
        .all()
    )
    return kontakte, total


def list_kategorien(db: Session) -> list[KontaktKategorie]:
    return db.query(KontaktKategorie).order_by(KontaktKategorie.name, KontaktKategorie.id).all()


def find_duplicate_candidates(
    db: Session,
    *,
    anzeigename: str,
    organisation: str | None = None,
    email: str | None = None,
    telefone: list[str] | None = None,
) -> list[Kontakt]:
    filters = []
    if email and email.strip():
        filters.append(func.lower(Kontakt.email) == email.strip().lower())
    nummern = [telefon_normalisiert(nummer) for nummer in (telefone or []) if nummer.strip()]
    if nummern:
        filters.append(KontaktTelefon.nummer_normalisiert.in_(nummern))
    if anzeigename.strip() and organisation and organisation.strip():
        filters.append(
            and_(
                func.lower(Kontakt.anzeigename) == anzeigename.strip().lower(),
                func.lower(Kontakt.organisation) == organisation.strip().lower(),
            )
        )
    if not filters:
        return []
    return (
        _mit_details(db.query(Kontakt))
        .outerjoin(KontaktTelefon)
        .filter(Kontakt.archiviert.is_(False), or_(*filters))
        .distinct()
        .order_by(Kontakt.anzeigename, Kontakt.id)
        .all()
    )


def merge_kontakte(
    db: Session, quelle_id: int, ziel_id: int, feldwahl: dict[str, str], *, user_id: int | None
) -> KontaktMergeErgebnis:
    """Fuehrt zwei Kontakte derselben Organisation verlustfrei zusammen."""
    if quelle_id == ziel_id:
        raise ValueError("Quelle und Ziel muessen verschieden sein")
    quelle = get_kontakt(db, quelle_id, include_archiviert=True)
    ziel = get_kontakt(db, ziel_id, include_archiviert=True)
    if quelle is None or ziel is None:
        raise LookupError("Kontakt nicht gefunden")
    if quelle.archiviert or ziel.archiviert:
        raise ValueError("Archivierte Kontakte koennen nicht zusammengefuehrt werden")
    if quelle.org_id != ziel.org_id:
        raise ValueError("Kontakte gehoeren nicht zur selben Organisation")

    for feld in (
        "typ", "anzeigename", "vorname", "nachname", "funktion", "organisation",
        "email", "erreichbarkeit", "notizen",
    ):
        if feldwahl.get(feld) == "quelle":
            setattr(ziel, feld, getattr(quelle, feld))

    nummern = {telefon.nummer_normalisiert for telefon in ziel.telefone}
    next_sort = max((telefon.sort for telefon in ziel.telefone), default=-1) + 1
    for telefon in list(quelle.telefone):
        if telefon.nummer_normalisiert in nummern:
            db.delete(telefon)
        else:
            telefon.kontakt_id, telefon.sort = ziel.id, next_sort
            next_sort += 1
            nummern.add(telefon.nummer_normalisiert)
    kategorien = {zuordnung.kategorie_id for zuordnung in ziel.kategorien}
    for zuordnung in list(quelle.kategorien):
        if zuordnung.kategorie_id in kategorien:
            db.delete(zuordnung)
        else:
            zuordnung.kontakt_id = ziel.id
            kategorien.add(zuordnung.kategorie_id)
    for anhang in db.query(KontaktAnhang).filter(KontaktAnhang.kontakt_id == quelle.id).all():
        anhang.kontakt_id = ziel.id
    refs = {
        (ref.quelle, ref.quelle_kontext, ref.extern_id)
        for ref in db.query(KontaktExterneReferenz).filter(KontaktExterneReferenz.kontakt_id == ziel.id).all()
    }
    for ref in db.query(KontaktExterneReferenz).filter(KontaktExterneReferenz.kontakt_id == quelle.id).all():
        key = (ref.quelle, ref.quelle_kontext, ref.extern_id)
        if key in refs:
            db.delete(ref)
        else:
            ref.kontakt_id = ziel.id
            refs.add(key)

    konflikte: list[str] = []
    for objekt_zuordnung in db.query(ObjektKontakt).filter(ObjektKontakt.kontakt_id == quelle.id).all():
        gleich = db.query(ObjektKontakt).filter(
            ObjektKontakt.kontakt_id == ziel.id,
            ObjektKontakt.objekt_id == objekt_zuordnung.objekt_id,
            ObjektKontakt.art == objekt_zuordnung.art,
        ).first()
        if gleich is None:
            objekt_zuordnung.kontakt_id = ziel.id
            continue
        for freigabe in list(objekt_zuordnung.freigaben):
            vorhanden = next((ziel_freigabe for ziel_freigabe in gleich.freigaben
                              if ziel_freigabe.kanal == freigabe.kanal
                              and ziel_freigabe.ziel_wert == freigabe.ziel_wert), None)
            if vorhanden is None:
                freigabe.objekt_kontakt_id = gleich.id
            else:
                if vorhanden.aktiv != freigabe.aktiv:
                    vorhanden.aktiv = False
                    konflikte.append(
                        f"Objektkontakt {gleich.id}: {freigabe.kanal} {freigabe.ziel_wert} "
                        "wurde wegen widerspruechlicher Freigaben deaktiviert."
                    )
                db.delete(freigabe)
        db.flush()
        # Nach dem Umhaengen nicht die im Python-Objekt noch alte Sammlung beim
        # delete-orphan-Cascade auswerten lassen.
        db.expire(objekt_zuordnung, ["freigaben"])
        for benachrichtigung in (
            db.query(ObjektKontaktBenachrichtigung)
            .filter(
                ObjektKontaktBenachrichtigung.org_id == objekt_zuordnung.org_id,
                ObjektKontaktBenachrichtigung.objekt_kontakt_id == objekt_zuordnung.id,
            )
            .all()
        ):
            benachrichtigung.objekt_kontakt_id = gleich.id
        db.delete(objekt_zuordnung)

    hinweis = f"Zusammengefuehrt nach Kontakt #{ziel.id} am {datetime.now(UTC):%Y-%m-%d %H:%M UTC}."
    quelle.notizen = f"{quelle.notizen.rstrip()}\n{hinweis}" if quelle.notizen else hinweis
    quelle.archiviert, quelle.aktiv, quelle.aktualisiert_von_id = True, False, user_id
    quelle.version += 1
    ziel.aktualisiert_von_id = user_id
    ziel.version += 1
    db.commit()
    kontakt = get_kontakt(db, ziel.id, include_archiviert=True)
    assert kontakt is not None
    return KontaktMergeErgebnis(kontakt=kontakt, freigabe_konflikte=konflikte)
