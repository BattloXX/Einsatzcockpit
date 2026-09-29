"""Schreiboperationen der Objektpflege ohne HTTP- oder Request-Abhaengigkeit."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.kontakt import Kontakt
from app.models.objekt import (
    AUSWAHL_KONTAKTART,
    OBJEKT_STATUS_ENTWURF,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektGefahr,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektWohnanlage,
    ObjektZusatzadresse,
)
from app.services.objekt_service import (
    lade_auswahl,
    links_aus_form,
    naechste_nummer,
    nur_produktiv,
    write_objekt_change,
)


class ObjektFehler(Exception):
    """Fachlicher Fehler mit der bisherigen HTTP-Bedeutung fuer den Router."""

    status_code = 400


class ObjektNichtGefundenFehler(ObjektFehler):
    status_code = 404


class ObjektValidierungsFehler(ObjektFehler):
    status_code = 400


def _text(wert: str) -> str | None:
    return wert.strip() or None


def erstelle_objekt(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    name: str,
    vulgoname: str = "",
    kategorie_id: str | int | None = None,
    strasse: str = "",
    hausnummer: str = "",
    plz: str = "",
    ort: str = "",
    lat: str | float | None = None,
    lng: str | float | None = None,
    quelle: str = "intern",
) -> tuple[Objekt, bool]:
    if not name.strip():
        raise ObjektValidierungsFehler("Name ist erforderlich")
    try:
        validiert_lat = float(lat) if lat is not None and str(lat).strip() else None
        validiert_lng = float(lng) if lng is not None and str(lng).strip() else None
    except ValueError as exc:
        raise ObjektValidierungsFehler("Ungueltige Koordinaten") from exc
    # Der Savepoint fuer den Nummern-Retry braucht eine echte aeussere
    # Transaktion; sonst kann SQLite beim RELEASE implizit persistieren.
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        verbindung = db.connection()
        treiber = verbindung.connection.driver_connection if verbindung.connection is not None else None
        if treiber is not None and not treiber.in_transaction:  # type: ignore[attr-defined]
            verbindung.exec_driver_sql("BEGIN")
    elif not db.in_transaction():
        db.begin()
    for _ in range(3):
        try:
            with db.begin_nested():
                kategorie = str(kategorie_id or "").strip()
                objekt = Objekt(
                    org_id=org_id,
                    nummer=naechste_nummer(db, org_id),
                    name=name.strip(),
                    vulgoname=_text(vulgoname),
                    kategorie_id=int(kategorie) if kategorie else None,
                    strasse=_text(strasse),
                    hausnummer=_text(hausnummer),
                    plz=_text(plz),
                    ort=_text(ort),
                    lat=validiert_lat,
                    lng=validiert_lng,
                    status=OBJEKT_STATUS_ENTWURF,
                    erstellt_von_id=user_id,
                    aktualisiert_von_id=user_id,
                )
                db.add(objekt)
                db.flush()
                write_objekt_change(db, objekt.id, org_id, "stammdaten", "angelegt", None, objekt.name, user_id, quelle)
                write_audit(
                    db,
                    "objekt.created",
                    org_id=org_id,
                    user_id=user_id,
                    entity_type="objekt",
                    entity_id=objekt.id,
                    payload={"name": objekt.name, "nummer": objekt.nummer},
                )
            return objekt, bool((strasse.strip() or ort.strip()) and validiert_lat is None)
        except IntegrityError as exc:
            if "uq_objekt_org_nummer" not in str(exc.orig) and "objekt.org_id, objekt.nummer" not in str(exc.orig):
                raise
    raise ObjektFehler("Objektnummer konnte nicht vergeben werden")


def bma_speichern(
    db: Session, objekt: Objekt, *, user_id: int | None, vorhanden: bool, daten: dict[str, Any], quelle: str = "intern"
) -> None:
    if not vorhanden:
        if objekt.bma is not None:
            write_objekt_change(
                db, objekt.id, objekt.org_id, "bma", "bma_entfernt", objekt.bma.bma_nummer, None, user_id, quelle
            )
            db.delete(objekt.bma)
            objekt.bma = None
        db.flush()
        return
    if objekt.bma is None:
        objekt.bma = ObjektBMA(org_id=objekt.org_id, objekt_id=objekt.id)
        db.add(objekt.bma)
        write_objekt_change(
            db, objekt.id, objekt.org_id, "bma", "bma_angelegt", None, daten.get("bma_nummer") or "-", user_id, quelle
        )
    for feld, neu in daten.items():
        alt = getattr(objekt.bma, feld)
        if alt != neu:
            setattr(objekt.bma, feld, neu)
            write_objekt_change(db, objekt.id, objekt.org_id, "bma", feld, alt, neu, user_id, quelle)
    db.flush()


def zusatzadresse_anlegen(
    db: Session,
    objekt: Objekt,
    *,
    user_id: int | None,
    bezeichnung: str,
    strasse: str = "",
    hausnummer: str = "",
    plz: str = "",
    ort: str = "",
    quelle: str = "intern",
) -> ObjektZusatzadresse:
    if not bezeichnung.strip():
        raise ObjektValidierungsFehler("Bezeichnung ist erforderlich")
    adresse = ObjektZusatzadresse(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        bezeichnung=bezeichnung.strip(),
        strasse=_text(strasse),
        hausnummer=_text(hausnummer),
        plz=_text(plz),
        ort=_text(ort),
        sort=max((z.sort for z in objekt.zusatzadressen), default=0) + 1,
    )
    db.add(adresse)
    write_objekt_change(
        db, objekt.id, objekt.org_id, "stammdaten", "zusatzadresse_neu", None, adresse.bezeichnung, user_id, quelle
    )
    db.flush()
    return adresse


def zusatzadresse_entfernen(
    db: Session, objekt: Objekt, adresse_id: int, *, user_id: int | None, quelle: str = "intern"
) -> None:
    adresse = (
        db.query(ObjektZusatzadresse)
        .filter(ObjektZusatzadresse.id == adresse_id, ObjektZusatzadresse.objekt_id == objekt.id)
        .first()
    )
    if adresse is None:
        raise ObjektNichtGefundenFehler("Zusatzadresse nicht gefunden")
    write_objekt_change(
        db,
        objekt.id,
        objekt.org_id,
        "stammdaten",
        "zusatzadresse_geloescht",
        adresse.bezeichnung,
        None,
        user_id,
        quelle,
    )
    db.delete(adresse)
    db.flush()


def gefahr_anlegen(
    db: Session,
    objekt: Objekt,
    *,
    user_id: int | None,
    gefahr_id: int,
    un_nummer: str = "",
    detail: str = "",
    stoffname: str = "",
    gefahrklasse: str = "",
    gefahrnummer: str = "",
    link_label: list[str] | None = None,
    link_url: list[str] | None = None,
    quelle: str = "intern",
) -> ObjektGefahr:
    katalog = db.query(GefahrenKatalog).filter(GefahrenKatalog.id == gefahr_id).first()
    if katalog is None:
        raise ObjektNichtGefundenFehler("Gefahr nicht im Katalog")
    eintrag = ObjektGefahr(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        gefahr_id=gefahr_id,
        un_nummer=_text(un_nummer),
        detail=_text(detail),
        stoffname=_text(stoffname),
        gefahrklasse=_text(gefahrklasse),
        gefahrnummer=_text(gefahrnummer),
        links_json=links_aus_form(link_label or [], link_url or []),
        sort=max((g.sort for g in objekt.gefahren), default=0) + 1,
    )
    db.add(eintrag)
    write_objekt_change(db, objekt.id, objekt.org_id, "gefahren", "gefahr_neu", None, katalog.name, user_id, quelle)
    db.flush()
    return eintrag


def gefahr_aendern(
    db: Session, objekt: Objekt, eintrag_id: int, *, user_id: int | None, daten: dict[str, Any], quelle: str = "intern"
) -> ObjektGefahr:
    eintrag = db.query(ObjektGefahr).filter(ObjektGefahr.id == eintrag_id, ObjektGefahr.objekt_id == objekt.id).first()
    if eintrag is None:
        raise ObjektNichtGefundenFehler("Gefahren-Eintrag nicht gefunden")
    for feld, wert in daten.items():
        setattr(eintrag, feld, wert)
    write_objekt_change(
        db,
        objekt.id,
        objekt.org_id,
        "gefahren",
        "gefahr_bearbeitet",
        None,
        eintrag.gefahr.name if eintrag.gefahr else None,
        user_id,
        quelle,
    )
    db.flush()
    return eintrag


def gefahr_entfernen(
    db: Session, objekt: Objekt, eintrag_id: int, *, user_id: int | None, quelle: str = "intern"
) -> None:
    eintrag = db.query(ObjektGefahr).filter(ObjektGefahr.id == eintrag_id, ObjektGefahr.objekt_id == objekt.id).first()
    if eintrag is None:
        raise ObjektNichtGefundenFehler("Gefahren-Eintrag nicht gefunden")
    write_objekt_change(
        db,
        objekt.id,
        objekt.org_id,
        "gefahren",
        "gefahr_geloescht",
        eintrag.gefahr.name if eintrag.gefahr else str(eintrag.gefahr_id),
        None,
        user_id,
        quelle,
    )
    db.delete(eintrag)
    db.flush()


def merkmal_zuordnen(
    db: Session, objekt: Objekt, *, user_id: int | None, merkmal_id: int, hinweis: str = "", quelle: str = "intern"
) -> ObjektMerkmal | None:
    katalog = db.query(MerkmalKatalog).filter(MerkmalKatalog.id == merkmal_id).first()
    if katalog is None:
        raise ObjektNichtGefundenFehler("Merkmal nicht im Katalog")
    if (
        db.query(ObjektMerkmal)
        .filter(ObjektMerkmal.objekt_id == objekt.id, ObjektMerkmal.merkmal_id == merkmal_id)
        .first()
        is not None
    ):
        return None
    zuordnung = ObjektMerkmal(org_id=objekt.org_id, objekt_id=objekt.id, merkmal_id=merkmal_id, hinweis=_text(hinweis))
    db.add(zuordnung)
    write_objekt_change(db, objekt.id, objekt.org_id, "merkmale", "merkmal_neu", None, katalog.name, user_id, quelle)
    db.flush()
    return zuordnung


def merkmal_entfernen(
    db: Session, objekt: Objekt, zuordnung_id: int, *, user_id: int | None, quelle: str = "intern"
) -> None:
    zuordnung = (
        db.query(ObjektMerkmal).filter(ObjektMerkmal.id == zuordnung_id, ObjektMerkmal.objekt_id == objekt.id).first()
    )
    if zuordnung is None:
        raise ObjektNichtGefundenFehler("Merkmal-Zuordnung nicht gefunden")
    write_objekt_change(
        db,
        objekt.id,
        objekt.org_id,
        "merkmale",
        "merkmal_entfernt",
        zuordnung.merkmal.name if zuordnung.merkmal else str(zuordnung.merkmal_id),
        None,
        user_id,
        quelle,
    )
    db.delete(zuordnung)
    db.flush()


def kontakt_zuordnen(
    db: Session, objekt: Objekt, zentraler_kontakt: Kontakt, *, art: str, user_id: int | None, quelle: str = "intern"
) -> ObjektKontakt:
    if zentraler_kontakt.org_id != objekt.org_id:
        raise ObjektNichtGefundenFehler("Kontakt nicht gefunden")
    if objekt.org_id is None:
        raise ObjektValidierungsFehler("Objekt hat keine Organisation")
    art = art if art in lade_auswahl(db, objekt.org_id, AUSWAHL_KONTAKTART) else "sonstig"
    if (
        db.query(ObjektKontakt)
        .filter(
            ObjektKontakt.objekt_id == objekt.id,
            ObjektKontakt.kontakt_id == zentraler_kontakt.id,
            ObjektKontakt.art == art,
        )
        .first()
        is not None
    ):
        raise ObjektValidierungsFehler("Kontakt ist bereits mit dieser Art zugeordnet")
    kontakt = ObjektKontakt(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        kontakt_id=zentraler_kontakt.id,
        art=art,
        sort=max((k.sort for k in objekt.kontakte), default=0) + 1,
    )
    db.add(kontakt)
    db.flush()
    from app.services.kontakt_sync_service import mapping_payload, record_change

    record_change(db, objekt.org_id, "zuordnung", kontakt.id, "upsert", mapping_payload(kontakt))
    write_objekt_change(
        db,
        objekt.id,
        objekt.org_id,
        "kontakte",
        "kontakt_zugeordnet",
        None,
        zentraler_kontakt.anzeigename,
        user_id,
        quelle,
    )
    return kontakt


def kontakt_zuordnung_entfernen(
    db: Session, objekt: Objekt, zuordnung_id: int, *, user_id: int | None, quelle: str = "intern"
) -> None:
    """Entfernt ausschliesslich die Objekt-Kontakt-Zuordnung, nie den zentralen Kontakt."""
    zuordnung = (
        db.query(ObjektKontakt)
        .filter(ObjektKontakt.id == zuordnung_id, ObjektKontakt.objekt_id == objekt.id)
        .first()
    )
    if zuordnung is None:
        raise ObjektNichtGefundenFehler("Kontakt-Zuordnung nicht gefunden")
    if objekt.org_id is None:
        raise ObjektValidierungsFehler("Objekt hat keine Organisation")
    name = zuordnung.zentraler_kontakt.anzeigename if zuordnung.zentraler_kontakt else str(zuordnung.kontakt_id)
    from app.services.kontakt_sync_service import record_change

    record_change(db, objekt.org_id, "zuordnung", zuordnung.id, "delete", {})
    write_objekt_change(
        db, objekt.id, objekt.org_id, "kontakte", "kontakt_entfernt", name, None, user_id, quelle
    )
    db.delete(zuordnung)
    db.flush()


def wohnanlage_speichern(
    db: Session, objekt: Objekt, *, user_id: int | None, vorhanden: bool, daten: dict[str, Any], quelle: str = "intern"
) -> None:
    if not vorhanden:
        if objekt.wohnanlage is not None:
            write_objekt_change(
                db,
                objekt.id,
                objekt.org_id,
                "stammdaten",
                "wohnanlage_entfernt",
                "Wohnanlagen-Block",
                None,
                user_id,
                quelle,
            )
            db.delete(objekt.wohnanlage)
            objekt.wohnanlage = None
        db.flush()
        return
    if objekt.wohnanlage is None:
        objekt.wohnanlage = ObjektWohnanlage(org_id=objekt.org_id, objekt_id=objekt.id)
        db.add(objekt.wohnanlage)
        write_objekt_change(
            db,
            objekt.id,
            objekt.org_id,
            "stammdaten",
            "wohnanlage_angelegt",
            None,
            "Wohnanlagen-Block",
            user_id,
            quelle,
        )
    for feld, neu in daten.items():
        alt = getattr(objekt.wohnanlage, feld)
        if alt != neu:
            setattr(objekt.wohnanlage, feld, neu)
            write_objekt_change(
                db, objekt.id, objekt.org_id, "stammdaten", f"wohnanlage_{feld}", alt, neu, user_id, quelle
            )
    db.flush()


def filtere_objekte_nach_text(query: Any, q: str) -> Any:
    """Wendet den gemeinsamen Freitextfilter auf eine Objekt-Query an."""
    if q.strip():
        term = f"%{q.strip()}%"
        filters: list[ColumnElement[bool]] = [
            Objekt.name.like(term),
            Objekt.vulgoname.like(term),
            Objekt.strasse.like(term),
            Objekt.ort.like(term),
        ]
        if q.strip().isdigit():
            filters.append(Objekt.nummer == int(q.strip()))
        return query.filter(or_(*filters))
    return query


def suche_objekte(db: Session, org_id: int, q: str = "", status: str = "", limit: int = 100) -> list[Objekt]:
    query = nur_produktiv(db.query(Objekt)).filter(Objekt.org_id == org_id)
    query = filtere_objekte_nach_text(query, q)
    if status:
        query = query.filter(Objekt.status == status)
    return query.order_by(Objekt.nummer).limit(max(1, min(limit, 200))).all()
