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
    AUSWAHL_DOKUMENTART,
    AUSWAHL_KONTAKTART,
    AUSWAHL_PIKTOGRAMM,
    OBJEKT_KOPIERBARE_FELDER,
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    OBJEKT_STATUS_UEBERGAENGE,
    Objekt,
    ObjektBMA,
    ObjektChange,
    ObjektGefahr,
    ObjektKartenObjekt,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektWohnanlage,
    ObjektZusatzadresse,
)


def telefone_zu_json(telefone_raw: str) -> str | None:
    """Serialisiert eine kommagetrennte Telefonliste zu JSON (ObjektKontakt.telefone_json).

    Verschoben aus ui_objekt.py::_telefone_to_json (2026-07-26), damit der
    BMA-Webplattform-Import (app/services/bma_import/bma_sync.py) dieselbe
    Funktion nutzt wie das manuelle Kontaktformular.
    """
    nummern = [t.strip() for t in telefone_raw.replace(";", ",").split(",") if t.strip()]
    return json.dumps(nummern, ensure_ascii=False) if nummern else None


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


# ── Arbeitskopie-Workflow ──────────────────────────────────────────────────────
#
# Ein freigegebenes Objekt bleibt waehrend einer Ueberarbeitung inhaltlich unveraendert
# produktiv (Matching, Sync, Objektblatt, Einsatzansicht) - bearbeitet wird ausschliesslich
# eine separate Arbeitskopie (eigene Objekt-Zeile, Objekt.entwurf_von_id gesetzt). Erst die
# Uebernahme (Merge) schreibt die Aenderungen auf die produktive Zeile zurueck; die id des
# produktiven Objekts bleibt dabei stabil (ObjektEinsatz/ObjektDokument/ObjektChange/
# Medienpfade referenzieren sie unveraendert weiter). Siehe docs/plans/objekt-arbeitskopie-plan.md.


def nur_produktiv(query):
    """Haengt an eine Objekt-Query den Filter 'keine offene Arbeitskopie' an.

    Arbeitskopien (Objekt.entwurf_von_id gesetzt) sind reine Bearbeitungsstaende und
    duerfen NIE in Listen, Matching, Sync-Manifest, Auswahlfeldern oder Erinnerungen
    auftauchen. Der Tenant-Listener (app/core/tenant.py) filtert nur nach org_id, nicht
    danach - MUSS daher bei jeder Objekt-Query verwendet werden, die nicht ohnehin ueber
    eine einzelne bekannte id filtert.
    """
    return query.filter(Objekt.entwurf_von_id.is_(None))


def hole_arbeitskopie(db: Session, objekt: Objekt) -> Objekt | None:
    """Laedt die offene Arbeitskopie eines produktiven Objekts, falls vorhanden."""
    return (
        db.query(Objekt)
        .filter(Objekt.entwurf_von_id == objekt.id, Objekt.org_id == objekt.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )


def _kopiere_kindzeile(quelle: Any, ziel_cls: type, *, objekt_id: int, org_id: int | None,
                        exclude: tuple[str, ...] = ()) -> Any:
    """Kopiert eine Kind-Zeile (BMA/Zusatzadresse/Gefahr/Merkmal/Kontakt/Wohnanlage/
    Kartenobjekt) auf ein anderes Objekt. Kopiert generisch alle gemappten Spalten ausser
    id/objekt_id/org_id (+ optionale weitere Ausschluesse, z. B. FKs die separat remappt
    werden muessen) - so bricht das Kopieren nicht, wenn dem Modell spaeter ein Feld
    hinzugefuegt wird.
    """
    from sqlalchemy import inspect as sa_inspect
    ausschluss = {"id", "objekt_id", "org_id", *exclude}
    kwargs = {
        spalte.key: getattr(quelle, spalte.key)
        for spalte in sa_inspect(type(quelle)).mapper.column_attrs
        if spalte.key not in ausschluss
    }
    kwargs["objekt_id"] = objekt_id
    kwargs["org_id"] = org_id
    return ziel_cls(**kwargs)


def erstelle_arbeitskopie(db: Session, objekt: Objekt, user_id: int | None) -> Objekt:
    """Legt eine Arbeitskopie eines freigegebenen Objekts an (tiefe Kopie aller
    Stammdaten + Kinddaten: BMA, Zusatzadressen, Gefahren, Merkmale, Kontakte, Wohnanlage,
    Lagekarten-Symbole). Das produktive Objekt wechselt auf 'in_ueberarbeitung', seine
    DATEN bleiben dabei unveraendert. Caller committet.

    Wirft ValueError, wenn das Objekt nicht freigegeben ist oder bereits eine offene
    Arbeitskopie existiert (zusaetzlich zur DB-seitigen UNIQUE-Absicherung).
    """
    if objekt.status != OBJEKT_STATUS_FREIGEGEBEN:
        raise ValueError("Nur freigegebene Objekte koennen ueberarbeitet werden")
    if hole_arbeitskopie(db, objekt) is not None:
        raise ValueError("Es existiert bereits eine offene Arbeitskopie fuer dieses Objekt")

    kopie = Objekt(
        org_id=objekt.org_id,
        nummer=None,
        status=OBJEKT_STATUS_ENTWURF,
        entwurf_von_id=objekt.id,
        erstellt_von_id=user_id,
        aktualisiert_von_id=user_id,
        **{feld: getattr(objekt, feld) for feld in OBJEKT_KOPIERBARE_FELDER},
    )
    db.add(kopie)
    db.flush()  # kopie.id fuer die Kind-Fremdschluessel unten

    if objekt.bma is not None:
        kopie.bma = _kopiere_kindzeile(objekt.bma, ObjektBMA, objekt_id=kopie.id, org_id=kopie.org_id)

    for adresse in objekt.zusatzadressen:
        db.add(_kopiere_kindzeile(adresse, ObjektZusatzadresse, objekt_id=kopie.id, org_id=kopie.org_id))

    for gefahr in objekt.gefahren:
        db.add(_kopiere_kindzeile(gefahr, ObjektGefahr, objekt_id=kopie.id, org_id=kopie.org_id))

    for merkmal in objekt.merkmale:
        db.add(_kopiere_kindzeile(merkmal, ObjektMerkmal, objekt_id=kopie.id, org_id=kopie.org_id))

    # Kontakte zuerst kopieren und die alt→neu-id merken - die Wohnanlage kann per
    # hausverwaltung_kontakt_id auf einen Kontakt zeigen und muss remappt werden.
    kontakt_map: dict[int, int] = {}
    for kontakt in objekt.kontakte:
        neuer_kontakt = _kopiere_kindzeile(kontakt, ObjektKontakt, objekt_id=kopie.id, org_id=kopie.org_id)
        db.add(neuer_kontakt)
        db.flush()
        kontakt_map[kontakt.id] = neuer_kontakt.id

    if objekt.wohnanlage is not None:
        neue_wohnanlage = _kopiere_kindzeile(
            objekt.wohnanlage, ObjektWohnanlage, objekt_id=kopie.id, org_id=kopie.org_id,
            exclude=("hausverwaltung_kontakt_id",),
        )
        alte_kontakt_id = objekt.wohnanlage.hausverwaltung_kontakt_id
        if alte_kontakt_id is not None and alte_kontakt_id in kontakt_map:
            neue_wohnanlage.hausverwaltung_kontakt_id = kontakt_map[alte_kontakt_id]
        kopie.wohnanlage = neue_wohnanlage

    for karten_objekt in objekt.karten_objekte:
        db.add(_kopiere_kindzeile(karten_objekt, ObjektKartenObjekt, objekt_id=kopie.id, org_id=kopie.org_id))

    alt_status = objekt.status
    objekt.status = OBJEKT_STATUS_UEBERARBEITUNG
    objekt.aktualisiert_von_id = user_id
    write_objekt_change(db, objekt.id, objekt.org_id, "status", "status",
                        before=alt_status, after=objekt.status, user_id=user_id)
    return kopie


def _ersetze_kinddaten(db: Session, basis: Objekt, kopie: Objekt, *, user_id: int | None) -> None:
    """Ersetzt alle Kind-Datensaetze der Basis 1:1 durch die der Arbeitskopie (Merge-
    Schritt von uebernimm_arbeitskopie): alte Kinder loeschen, neue aus der Kopie anlegen.
    Ein feldgenauer Diff waere hier unverhaeltnismaessig (Listen mit Sortierung/
    Fremdschluesseln) - stattdessen EIN Sammel-Change-Eintrag je Bereich.
    """
    # BMA (1:1)
    hatte_bma = basis.bma is not None
    if basis.bma is not None:
        db.delete(basis.bma)
        db.flush()
    hat_bma = kopie.bma is not None
    if kopie.bma is not None:
        basis.bma = _kopiere_kindzeile(kopie.bma, ObjektBMA, objekt_id=basis.id, org_id=basis.org_id)
    if hatte_bma != hat_bma:
        write_objekt_change(db, basis.id, basis.org_id, "bma", "vorhanden",
                            before=hatte_bma, after=hat_bma, user_id=user_id)

    # Zusatzadressen
    vorher = len(basis.zusatzadressen)
    for alte_adresse in list(basis.zusatzadressen):
        db.delete(alte_adresse)
    db.flush()  # DELETE muss vor dem INSERT der Kopie durchsein, sonst evtl. UNIQUE-Kollision
    neue_adressen = [_kopiere_kindzeile(e, ObjektZusatzadresse, objekt_id=basis.id, org_id=basis.org_id)
                     for e in kopie.zusatzadressen]
    db.add_all(neue_adressen)
    if vorher or neue_adressen:
        write_objekt_change(db, basis.id, basis.org_id, "zusatzadressen", "anzahl",
                            before=vorher, after=len(neue_adressen), user_id=user_id)

    # Gefahren
    vorher = len(basis.gefahren)
    for alte_gefahr in list(basis.gefahren):
        db.delete(alte_gefahr)
    db.flush()  # s.o.
    neue_gefahren = [_kopiere_kindzeile(e, ObjektGefahr, objekt_id=basis.id, org_id=basis.org_id)
                     for e in kopie.gefahren]
    db.add_all(neue_gefahren)
    if vorher or neue_gefahren:
        write_objekt_change(db, basis.id, basis.org_id, "gefahren", "anzahl",
                            before=vorher, after=len(neue_gefahren), user_id=user_id)

    # Merkmale
    vorher = len(basis.merkmale)
    for altes_merkmal in list(basis.merkmale):
        db.delete(altes_merkmal)
    db.flush()  # s.o. - ohne dieses Flush schlaegt uq_objekt_merkmal fehl, wenn dieselbe
    # Merkmal-Zuordnung auf Basis UND Arbeitskopie existiert (Vorfall 2026-07-26, Objekt 1082:
    # "Duplicate entry '1082-41' for key 'uq_objekt_merkmal'" - der INSERT der Kopie lief vor
    # dem DELETE der alten Zeile, da SQLAlchemy die Reihenfolge zwischen unabhaengigen
    # Delete-/Insert-Objekten ohne FK-Beziehung nicht danach ausrichtet).
    neue_merkmale = [_kopiere_kindzeile(e, ObjektMerkmal, objekt_id=basis.id, org_id=basis.org_id)
                     for e in kopie.merkmale]
    db.add_all(neue_merkmale)
    if vorher or neue_merkmale:
        write_objekt_change(db, basis.id, basis.org_id, "merkmale", "anzahl",
                            before=vorher, after=len(neue_merkmale), user_id=user_id)

    # Wohnanlage muss VOR den Kontakten geloescht werden (haengt per FK an einem Kontakt);
    # neu angelegt wird sie erst NACH den neuen Kontakten (Remap der Hausverwaltung).
    hatte_wohnanlage = basis.wohnanlage is not None
    if basis.wohnanlage is not None:
        db.delete(basis.wohnanlage)
        db.flush()

    # Kontakte
    vorher = len(basis.kontakte)
    for alter_kontakt in list(basis.kontakte):
        db.delete(alter_kontakt)
    db.flush()
    kontakt_map: dict[int, int] = {}
    for kontakt_vorlage in kopie.kontakte:
        neuer_kontakt = _kopiere_kindzeile(kontakt_vorlage, ObjektKontakt, objekt_id=basis.id, org_id=basis.org_id)
        db.add(neuer_kontakt)
        db.flush()
        kontakt_map[kontakt_vorlage.id] = neuer_kontakt.id
    if vorher or kontakt_map:
        write_objekt_change(db, basis.id, basis.org_id, "kontakte", "anzahl",
                            before=vorher, after=len(kontakt_map), user_id=user_id)

    # Wohnanlage neu anlegen (nach den Kontakten - Remap der Hausverwaltung)
    hat_wohnanlage = kopie.wohnanlage is not None
    if kopie.wohnanlage is not None:
        neue_wohnanlage = _kopiere_kindzeile(
            kopie.wohnanlage, ObjektWohnanlage, objekt_id=basis.id, org_id=basis.org_id,
            exclude=("hausverwaltung_kontakt_id",),
        )
        alte_kontakt_id = kopie.wohnanlage.hausverwaltung_kontakt_id
        if alte_kontakt_id is not None and alte_kontakt_id in kontakt_map:
            neue_wohnanlage.hausverwaltung_kontakt_id = kontakt_map[alte_kontakt_id]
        basis.wohnanlage = neue_wohnanlage
    if hatte_wohnanlage != hat_wohnanlage:
        write_objekt_change(db, basis.id, basis.org_id, "wohnanlage", "vorhanden",
                            before=hatte_wohnanlage, after=hat_wohnanlage, user_id=user_id)

    # Lagekarten-Symbole
    vorher = len(basis.karten_objekte)
    for altes_kartenobjekt in list(basis.karten_objekte):
        db.delete(altes_kartenobjekt)
    db.flush()  # DELETE muss vor dem INSERT der Kopie durchsein, sonst evtl. UNIQUE-Kollision
    neue_kartenobjekte = [_kopiere_kindzeile(e, ObjektKartenObjekt, objekt_id=basis.id, org_id=basis.org_id)
                          for e in kopie.karten_objekte]
    db.add_all(neue_kartenobjekte)
    if vorher or neue_kartenobjekte:
        write_objekt_change(db, basis.id, basis.org_id, "karte", "anzahl",
                            before=vorher, after=len(neue_kartenobjekte), user_id=user_id)


def uebernimm_arbeitskopie(db: Session, kopie: Objekt, user_id: int | None) -> Objekt:
    """Uebernimmt eine Arbeitskopie in das produktive Basis-Objekt (Merge, eine
    Transaktion): Stammdaten + Kinddaten werden auf die Basis zurueckgeschrieben, die
    Basis-id bleibt stabil. Die Arbeitskopie wird anschliessend geloescht.
    Gibt das aktualisierte Basis-Objekt zurueck. Caller committet.
    """
    basis = kopie.basis_objekt
    if basis is None or basis.status != OBJEKT_STATUS_UEBERARBEITUNG:
        raise ValueError("Arbeitskopie ohne gueltiges Basis-Objekt in Ueberarbeitung")

    daten = {feld: getattr(kopie, feld) for feld in OBJEKT_KOPIERBARE_FELDER}
    if daten.get("revision_datum") != basis.revision_datum:
        daten["revision_erinnert_am"] = None
    aktualisiere_felder(db, basis, daten, bereich="stammdaten", user_id=user_id)

    _ersetze_kinddaten(db, basis, kopie, user_id=user_id)

    # Bearbeitungshistorie der Kopie an die Basis umhaengen, damit sie erhalten bleibt.
    db.query(ObjektChange).filter(
        ObjektChange.objekt_id == kopie.id, ObjektChange.org_id == kopie.org_id,
    ).update({ObjektChange.objekt_id: basis.id}, synchronize_session=False)

    alt_status = basis.status
    basis.status = OBJEKT_STATUS_FREIGEGEBEN
    basis.aktualisiert_von_id = user_id
    basis.aktualisiert_am = datetime.now(UTC)
    write_objekt_change(db, basis.id, basis.org_id, "status", "status",
                        before=alt_status, after=basis.status, user_id=user_id)

    db.delete(kopie)
    return basis


def verwirf_arbeitskopie(db: Session, kopie: Objekt, user_id: int | None) -> Objekt:
    """Verwirft eine Arbeitskopie ohne Uebernahme; die Basis bleibt unveraendert
    freigegeben (ihre Daten wurden waehrend der Ueberarbeitung nie angetastet).
    Caller committet.
    """
    basis = kopie.basis_objekt
    if basis is None:
        raise ValueError("Arbeitskopie ohne Basis-Objekt")

    db.delete(kopie)
    if basis.status == OBJEKT_STATUS_UEBERARBEITUNG:
        alt_status = basis.status
        basis.status = OBJEKT_STATUS_FREIGEGEBEN
        basis.aktualisiert_von_id = user_id
        write_objekt_change(db, basis.id, basis.org_id, "status", "status",
                            before=alt_status, after=basis.status, user_id=user_id)
    return basis


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


# Zuordnung Stammdaten-Feld/-Katalogtyp -> Kartensymbol (nur eindeutige 1:1-Treffer;
# mehrdeutige Merkmale wie "tiefgarage" ohne exaktes Symbol werden bewusst NICHT
# vorgeschlagen, um Fehlplatzierungen/falsche Symbolwahl zu vermeiden).
_GEFAHR_SYMBOL_TYP = {
    "ex": "gefahr_ex", "gas": "gefahr_gas", "chemie": "gefahr_chemie",
    "hochspannung": "gefahr_strom", "pv": "gefahr_pv",
}
_MERKMAL_SYMBOL_TYP = {
    "brandschutzplan": "bsp", "dlk_stellplatz": "dlk_stellplatz",
    "objektfunk": "objektfunk", "sammelplatz": "sammelplatz",
}


def fehlende_kartensymbole(objekt: Objekt) -> list[dict]:
    """Stammdaten (BMA/Gefahren/Merkmale), zu denen es laut OBJEKT_SYMBOL_TYPEN
    ein passendes Kartensymbol gibt, das am Objekt aber noch nicht gesetzt ist.

    Reiner Hinweis fuer die Lagekarte, KEINE automatische Platzierung - die
    genaue Position (Grundriss-Koordinate) laesst sich aus Text-Stammdaten
    nicht zuverlaessig ermitteln, das muss der Sachbearbeiter per Drag&Drop
    auf der Karte setzen.
    """
    from app.models.objekt import OBJEKT_SYMBOL_TYPEN

    vorhandene_typen = {k.typ for k in objekt.karten_objekte}
    vorschlaege: list[dict] = []

    if objekt.bma is not None:
        for bma_typ, wert, label in (
            ("bmz", objekt.bma.bmz_standort, "BMZ (Brandmelderzentrale)"),
            ("fbf", objekt.bma.fbf_standort, "FBF (Feuerwehr-Bedienfeld)"),
        ):
            if wert and bma_typ not in vorhandene_typen:
                vorschlaege.append({"typ": bma_typ, "label": label, "hinweis": wert})
        if objekt.bma.schluesselsafe_vorhanden and "fsd" not in vorhandene_typen:
            vorschlaege.append({
                "typ": "fsd", "label": OBJEKT_SYMBOL_TYPEN.get("fsd", "FSD / Schlüsselsafe"),
                "hinweis": objekt.bma.schluesselsafe_standort or "",
            })

    for gefahr_eintrag in objekt.gefahren:
        gefahr_typ = (
            _GEFAHR_SYMBOL_TYP.get(gefahr_eintrag.gefahr.piktogramm_typ) if gefahr_eintrag.gefahr else None
        )
        if gefahr_typ and gefahr_typ not in vorhandene_typen:
            vorschlaege.append({
                "typ": gefahr_typ, "label": OBJEKT_SYMBOL_TYPEN.get(gefahr_typ, gefahr_typ),
                "hinweis": gefahr_eintrag.detail or (gefahr_eintrag.gefahr.name if gefahr_eintrag.gefahr else ""),
            })
            vorhandene_typen.add(gefahr_typ)  # mehrere Gefahren gleichen Typs nur einmal vorschlagen

    for merkmal_eintrag in objekt.merkmale:
        code = merkmal_eintrag.merkmal.code if merkmal_eintrag.merkmal else None
        merkmal_typ = _MERKMAL_SYMBOL_TYP.get(code) if code else None
        if merkmal_typ and merkmal_typ not in vorhandene_typen:
            vorschlaege.append({
                "typ": merkmal_typ, "label": OBJEKT_SYMBOL_TYPEN.get(merkmal_typ, merkmal_typ), "hinweis": "",
            })
            vorhandene_typen.add(merkmal_typ)

    return vorschlaege


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

# Pflegbare Auswahllisten je Org: typ -> [(code, icon|None, name)].
# label = f"{icon} {name}" reproduziert die alten Modul-Konstanten exakt.
STANDARD_AUSWAHL: dict[str, list[tuple[str, str | None, str]]] = {
    AUSWAHL_KONTAKTART: [
        ("brandschutzbeauftragter", None, "Brandschutzbeauftragter"),
        ("betreiber", None, "Betreiber"),
        ("hausverwaltung", None, "Hausverwaltung"),
        ("schluesseltraeger", None, "Schlüsselträger"),
        ("sonstig", None, "Sonstiger Kontakt"),
    ],
    AUSWAHL_DOKUMENTART: [
        ("bma_datenblatt", None, "BMA Datenblatt"),
        ("bma_melderplan", None, "BMA Melderplan"),
        ("brandschutzplan", None, "Brandschutzplan"),
        ("gefahrgutdatenblatt", None, "Gefahrgutdatenblatt"),
        ("lageplan", None, "Lageplan"),
        ("objektinformation", None, "Objektinformation"),
        ("foerderstrecke", None, "Einsatzplan Wasserförderung"),
    ],
    AUSWAHL_PIKTOGRAMM: [
        ("ex", "💥", "EX-Bereich"),
        ("gas", "🔥", "Gas"),
        ("chemie", "🧪", "Chemie / Gefahrstoff"),
        ("hochspannung", "⚡", "Hochspannung"),
        ("pv", "☀️", "Photovoltaik"),
        ("nh3", "❄️", "Ammoniak (NH3)"),
        ("brandlast", "🔥", "Hohe Brandlast"),
        ("sonstig", "⚠️", "Sonstige Gefahr"),
    ],
}


def lade_auswahl(db: Session, org_id: int | None, typ: str) -> dict[str, str]:
    """Aktive Auswahleintraege einer Org als {code: label}, sort-geordnet.

    Fallback auf die Modul-Konstante, solange die Tabelle (fuer diese Org/diesen Typ)
    leer ist — z. B. in einer frischen Testumgebung ohne Seeds. `label` enthaelt das
    Icon-Prefix (Piktogramme), reproduziert also die alten Konstanten-Werte.
    """
    from app.models.objekt import _AUSWAHL_FALLBACK, ObjektAuswahl

    rows = (
        db.query(ObjektAuswahl)
        .filter(
            ObjektAuswahl.org_id == org_id,
            ObjektAuswahl.typ == typ,
            ObjektAuswahl.aktiv.is_(True),
        )
        .order_by(ObjektAuswahl.sort, ObjektAuswahl.name)
        .execution_options(include_all_tenants=True)
        .all()
    )
    if rows:
        return {r.code: r.label for r in rows}
    return dict(_AUSWAHL_FALLBACK.get(typ, {}))


def seed_objekt_auswahl(db: Session, org_id: int) -> None:
    """Legt die Standard-Auswahllisten (Kontaktarten/Dokumentarten/Piktogramme) an (idempotent)."""
    from app.models.objekt import ObjektAuswahl

    for typ, eintraege in STANDARD_AUSWAHL.items():
        for i, (code, icon, name) in enumerate(eintraege, start=1):
            exists = (
                db.query(ObjektAuswahl)
                .filter(
                    ObjektAuswahl.org_id == org_id,
                    ObjektAuswahl.typ == typ,
                    ObjektAuswahl.code == code,
                )
                .execution_options(include_all_tenants=True)
                .first()
            )
            if not exists:
                db.add(ObjektAuswahl(
                    org_id=org_id, typ=typ, code=code, icon=icon, name=name,
                    sort=i, aktiv=True, system=True,
                ))
    db.flush()


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
    seed_objekt_auswahl(db, org_id)
    from app.services.objekt_symbol_service import seed_objekt_symbole
    seed_objekt_symbole(db, org_id)


def gefahr_links(objekt_gefahr) -> list[dict]:  # type: ignore[no-untyped-def]
    """Merged Link-Liste einer Objekt-Gefahr: Katalog-Standard + objektspezifisch + generierte DB-Links.

    Dedupliziert nach URL (erste Beschriftung gewinnt).
    """
    from app.services.gefahrgut_service import generierte_links

    gesehen: set[str] = set()
    out: list[dict] = []

    def _add(items: list[dict]) -> None:
        for eintrag in items:
            url = (eintrag.get("url") or "").strip()
            if url and url not in gesehen:
                gesehen.add(url)
                out.append({"label": eintrag.get("label") or url, "url": url})

    if getattr(objekt_gefahr, "gefahr", None) is not None:
        _add(objekt_gefahr.gefahr.links)
    _add(objekt_gefahr.links)
    stoff = objekt_gefahr.stoffname or (
        objekt_gefahr.gefahr.name if getattr(objekt_gefahr, "gefahr", None) else None
    )
    _add(generierte_links(
        objekt_gefahr.un_nummer,
        stoff,
        getattr(objekt_gefahr, "gefahrnummer", None),
    ))
    return out


def links_aus_form(labels: list[str], urls: list[str]) -> str | None:
    """Serialisiert Label/URL-Paare aus einem Formular zu JSON. Nur http(s)-URLs."""
    import json as _json

    paare: list[dict] = []
    for label, url in zip(labels, urls, strict=False):
        u = (url or "").strip()
        if not u:
            continue
        if not (u.startswith("http://") or u.startswith("https://")):
            u = "https://" + u
        paare.append({"label": (label or "").strip() or u, "url": u})
    return _json.dumps(paare, ensure_ascii=False) if paare else None


def pruefe_revision_erinnerungen(db: Session) -> list[dict]:
    """Findet Objekte mit faelligem Revisionsdatum ohne gesendete Erinnerung.

    Setzt den Sent-Marker (revision_erinnert_am = heute) und gibt die Treffer
    fuer WS-Benachrichtigung zurueck. Caller committet und broadcastet.
    Muster: verleih_erinnerung (Due-Spalte + Sent-Marker, kein Doppelversand).
    """
    from datetime import date as _date

    from app.models.objekt import OBJEKT_STATUS_ARCHIVIERT

    heute = _date.today()
    # nur_produktiv: eine Arbeitskopie erbt beim Kopieren das revision_datum der Basis und
    # wuerde sonst eine zweite (Karteileichen-)Erinnerung fuer dasselbe Objekt ausloesen.
    kandidaten = (
        nur_produktiv(db.query(Objekt))
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
        nur_produktiv(db.query(Objekt))
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
