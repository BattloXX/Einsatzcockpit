"""Objektverwaltung-Service: Feature-Flags, Nummernvergabe, Change-Log, Workflow.

Effektive Aktivierung: System-Flag (SystemSettings key "objekt_module_enabled" == "true")
UND Org-Flag (OrgSettings.objekt_module_enabled == True) — Muster UAS-Modul.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.objekt import (
    OBJEKT_STATUS_UEBERGAENGE,
    Objekt,
    ObjektChange,
)


def objekt_system_enabled(db: Session) -> bool:
    """Systemweiter Objekt-Flag aus SystemSettings. Fehlender Key → False."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == "objekt_module_enabled").first()
    return row is not None and row.value == "true"


def objekt_effective_enabled(org_id: int | None, db: Session) -> bool:
    """Objektverwaltung effektiv aktiv ⟺ System-Flag AN und Org-Flag AN.

    Gibt False wenn org_id None (system_admin ohne Impersonation).
    """
    if org_id is None:
        return False
    if not objekt_system_enabled(db):
        return False
    from app.models.master import OrgSettings
    org_s = (
        db.query(OrgSettings)
        .filter(OrgSettings.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return bool(org_s and org_s.objekt_module_enabled)


def naechste_nummer(db: Session, org_id: int) -> int:
    """Vergibt die naechste org-interne Objektnummer (MAX+1, Start bei 1)."""
    from sqlalchemy import func
    max_nummer = (
        db.query(func.max(Objekt.nummer))
        .filter(Objekt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .scalar()
    )
    return (max_nummer or 0) + 1


def status_uebergang_erlaubt(von: str, nach: str) -> bool:
    """Prueft, ob der Status-Workflow den Uebergang erlaubt."""
    return nach in OBJEKT_STATUS_UEBERGAENGE.get(von, set())


def write_objekt_change(
    db: Session,
    objekt_id: int,
    org_id: int | None,
    bereich: str,
    feld: str,
    before: Any,
    after: Any,
    user_id: int | None = None,
) -> None:
    """Haengt einen feldgenauen Change-Eintrag an (Caller committet)."""
    db.add(ObjektChange(
        objekt_id=objekt_id,
        org_id=org_id,
        user_id=user_id,
        bereich=bereich,
        feld=feld,
        before_json=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
        erstellt_am=datetime.now(UTC),
    ))


def aktualisiere_felder(
    db: Session,
    objekt: Objekt,
    daten: dict[str, Any],
    bereich: str,
    user_id: int | None = None,
) -> list[str]:
    """Setzt Felder am Objekt und protokolliert jede tatsaechliche Aenderung.

    Gibt die Liste der geaenderten Feldnamen zurueck. Caller committet.
    """
    geaendert: list[str] = []
    for feld, neu in daten.items():
        alt = getattr(objekt, feld)
        if alt == neu:
            continue
        setattr(objekt, feld, neu)
        write_objekt_change(
            db, objekt.id, objekt.org_id, bereich, feld,
            before=alt, after=neu, user_id=user_id,
        )
        geaendert.append(feld)
    if geaendert:
        objekt.aktualisiert_am = datetime.now(UTC)
        objekt.aktualisiert_von_id = user_id
    return geaendert


def berechne_vollstaendigkeit(
    objekt: Objekt,
    *,
    kontakt_count: int | None = None,
    gefahren_count: int | None = None,
    dokument_count: int | None = None,
    karten_count: int | None = None,
) -> dict:
    """Datenqualitaets-Indikator: Vollstaendigkeit der Kernfelder in Prozent.

    Basis: Adresse, Koordinaten, Kategorie, Revision (+ BMA-Details falls BMA).
    Jeder count-Parameter erweitert die Punktematrix nur, wenn er uebergeben
    wird (None = Bereich noch nicht verfuegbar/geladen — nicht bewerten).
    """
    punkte: list[tuple[str, bool]] = [
        ("Adresse", bool(objekt.strasse and objekt.ort)),
        ("Koordinaten", objekt.lat is not None and objekt.lng is not None),
        ("Kategorie", objekt.kategorie_id is not None),
        ("Revisionsdatum", objekt.revision_datum is not None),
    ]
    # BMA-Block zaehlt nur, wenn vorhanden: dann muessen die Kernfelder gefuellt sein
    if objekt.bma is not None:
        punkte.append(("BMA-Details", bool(objekt.bma.bma_nummer and objekt.bma.bmz_standort)))
    if kontakt_count is not None:
        punkte.append(("Kontakte", kontakt_count > 0))
    if gefahren_count is not None:
        punkte.append(("Gefahren", gefahren_count > 0))
    if dokument_count is not None:
        punkte.append(("Dokumente", dokument_count > 0))
    if karten_count is not None:
        punkte.append(("Lagekarte", karten_count > 0))

    erfuellt = [name for name, ok in punkte if ok]
    fehlend = [name for name, ok in punkte if not ok]
    prozent = int(round(100 * len(erfuellt) / len(punkte))) if punkte else 0
    return {"prozent": prozent, "erfuellt": erfuellt, "fehlend": fehlend}


# Standard-Kataloge fuer neue Orgs (identisch zu den Migration-0124/0125-Seeds)
STANDARD_KATEGORIEN = [
    "Gewerbe/Industrie", "Wohnanlage", "Öffentliches Gebäude", "Landwirtschaft", "Sonderobjekt",
]
STANDARD_GEFAHREN = [
    ("EX-Bereich", "ex"),
    ("Gasanschluss / Gasflaschen", "gas"),
    ("Chemie / Gefahrstoff", "chemie"),
    ("Hochspannung", "hochspannung"),
    ("Photovoltaikanlage", "pv"),
    ("Ammoniak (NH3)", "nh3"),
    ("Hohe Brandlast", "brandlast"),
]
STANDARD_MERKMALE = [
    ("schluesselbox", "Schlüsselbox", "🔑"),
    ("brandschutzplan", "Brandschutzplan vorhanden", "📕"),
    ("dlk_stellplatz", "Drehleiterstellplatz", "🚒"),
    ("objektfunk", "Objektfunkanlage", "📻"),
    ("tiefgarage", "Tiefgarage", "🅿️"),
    ("pv", "Photovoltaikanlage", "☀️"),
    ("feuerwehraufzug", "Lift / Feuerwehraufzug", "🛗"),
    ("sammelplatz", "Sammelplatz", "🚻"),
    ("gas", "Gasanschluss", "🔥"),
    ("sprinkler", "Sprinkleranlage", "💧"),
    ("rwa", "RWA (Rauch-/Wärmeabzug)", "🌀"),
]


def seed_objekt_kataloge(db: Session, org_id: int) -> None:
    """Legt Standard-Kataloge (Kategorien/Gefahren/Merkmale) fuer eine Org an (idempotent).

    Wird beim Anlegen neuer Orgs aufgerufen (seed_service.apply_seed_profile);
    Bestandsorgs wurden per Migration 0124/0125 befuellt.
    """
    from app.models.objekt import GefahrenKatalog, MerkmalKatalog, ObjektKategorie

    for i, name in enumerate(STANDARD_KATEGORIEN, start=1):
        exists = (
            db.query(ObjektKategorie)
            .filter(ObjektKategorie.org_id == org_id, ObjektKategorie.name == name)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if not exists:
            db.add(ObjektKategorie(org_id=org_id, name=name, sort=i, aktiv=True))
    for i, (name, typ) in enumerate(STANDARD_GEFAHREN, start=1):
        gefahr_exists = (
            db.query(GefahrenKatalog)
            .filter(GefahrenKatalog.org_id == org_id, GefahrenKatalog.name == name)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if not gefahr_exists:
            db.add(GefahrenKatalog(org_id=org_id, name=name, piktogramm_typ=typ, sort=i, aktiv=True))
    for i, (code, name, icon) in enumerate(STANDARD_MERKMALE, start=1):
        merkmal_exists = (
            db.query(MerkmalKatalog)
            .filter(MerkmalKatalog.org_id == org_id, MerkmalKatalog.name == name)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if not merkmal_exists:
            db.add(MerkmalKatalog(org_id=org_id, code=code, name=name, icon=icon, sort=i, aktiv=True))
    db.flush()


def pruefe_revision_erinnerungen(db: Session) -> list[dict]:
    """Findet Objekte mit faelligem Revisionsdatum ohne gesendete Erinnerung.

    Setzt den Sent-Marker (revision_erinnert_am = heute) und gibt die Treffer
    fuer WS-Benachrichtigung zurueck. Caller committet und broadcastet.
    Muster: verleih_erinnerung (Due-Spalte + Sent-Marker, kein Doppelversand).
    """
    from datetime import date as _date

    from app.models.objekt import OBJEKT_STATUS_ARCHIVIERT

    heute = _date.today()
    kandidaten = (
        db.query(Objekt)
        .filter(
            Objekt.revision_datum.isnot(None),
            Objekt.revision_datum <= heute,
            Objekt.status != OBJEKT_STATUS_ARCHIVIERT,
        )
        .all()
    )
    faellig: list[dict] = []
    for objekt in kandidaten:
        if objekt.revision_datum is None:
            continue
        # Bereits erinnert → ueberspringen. Der Marker wird beim Setzen eines
        # neuen Revisionsdatums zurueckgesetzt (stammdaten_speichern).
        if objekt.revision_erinnert_am is not None:
            continue
        objekt.revision_erinnert_am = heute
        faellig.append({
            "org_id": objekt.org_id,
            "objekt_id": objekt.id,
            "nummer": objekt.nummer,
            "name": objekt.name,
            "revision_datum": objekt.revision_datum.isoformat(),
        })
    return faellig


def build_sync_manifest(db: Session, org_id: int) -> dict:
    """Offline-Sync-Manifest fuer die Android-App (PR9).

    Nur FREIGEGEBENE Objekte; je Objekt die Einsatzansicht-URL, aktualisiert_am
    als Versionsindikator und alle Seiten-Dateien (Thumb/Bild/Einzel-PDF).
    Seiten-Dateien sind unveraenderlich (UUID-Pfade) — ein Eintrag verschwindet
    nur, wenn die Seite geloescht wurde; der Client kann daher rein ueber die
    ID-Menge synchronisieren.
    """
    from app.models.objekt import (
        OBJEKT_STATUS_FREIGEGEBEN,
        ObjektDokumentSeite,
    )

    objekte = (
        db.query(Objekt)
        .filter(Objekt.org_id == org_id, Objekt.status == OBJEKT_STATUS_FREIGEGEBEN)
        .order_by(Objekt.nummer)
        .execution_options(include_all_tenants=True)
        .all()
    )
    objekt_ids = [o.id for o in objekte]
    seiten_by_objekt: dict[int, list] = {}
    if objekt_ids:
        seiten = (
            db.query(ObjektDokumentSeite)
            .filter(ObjektDokumentSeite.objekt_id.in_(objekt_ids))
            .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
            .execution_options(include_all_tenants=True)
            .all()
        )
        for s in seiten:
            eintrag = {
                "seite_id": s.id,
                "urls": [u for u in (
                    f"/objekt-medien/seite/{s.id}/thumb" if s.thumb_pfad else None,
                    f"/objekt-medien/seite/{s.id}/bild" if s.bild_pfad else None,
                    f"/objekt-medien/seite/{s.id}/pdf" if s.einzel_pdf_pfad else None,
                ) if u],
                "dokumentart": s.dokumentart,
            }
            seiten_by_objekt.setdefault(s.objekt_id, []).append(eintrag)

    return {
        "version": 1,
        "objekte": [
            {
                "objekt_id": o.id,
                "nummer": o.anzeige_nummer,
                "name": o.name,
                "aktualisiert_am": o.aktualisiert_am.isoformat() if o.aktualisiert_am else None,
                "einsatz_url": f"/objekte/{o.id}/einsatz",
                "seiten": seiten_by_objekt.get(o.id, []),
            }
            for o in objekte
        ],
    }
