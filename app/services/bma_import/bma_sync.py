"""BMA-Webplattform-Import (Landeswarnzentrale Vorarlberg) -> Objektverwaltung:
Zuordnung, Upsert, Review-Queue, Laufprotokoll.

Schreibpolitik (siehe docs/plans/... Plan "BMA-Import"):
- Objekt entwurf (neu angelegt oder bereits im Entwurf) -> Auto-Apply: Stammdaten,
  ObjektBMA und importierte Kontakte werden direkt geschrieben.
- Objekt freigegeben/in_ueberarbeitung -> NUR Queue: das Objekt bleibt unangetastet,
  der Diff wird aus BmaImportSatz.rohdaten_json zur Anzeigezeit berechnet
  (app/routers/ui_bma_import.py). Eine Uebernahme laeuft ueber
  erstelle_arbeitskopie/uebernimm_arbeitskopie, nie direkt auf die produktive Zeile.
- Objekt archiviert -> uebersprungen (nur Zaehler, kein Schreibversuch).

Es gibt bewusst KEINE eigene Vorschlagstabelle: BmaImportSatz.rohdaten_json haelt
IMMER den zuletzt geparsten Stand (Anlage + Kontakte zusammen), unabhaengig davon,
ob er angewendet oder nur vorgemerkt wurde - der Diff wird daraus live berechnet.
bestaetigt_hash merkt sich den Stand, den ein Verwalter zuletzt entschieden hat.

Robustheit: pro Anlage ein Savepoint (Muster: syBOS-Excel-Import, ui_admin.py),
ein kaputter Datensatz bricht den Lauf nicht ab. Eine Plausibilitaetsuntergrenze
schuetzt davor, dass eine kaputte/leere Antwort (z. B. eine abgelaufene Session,
die als leere Liste durchrutscht) den bestehenden Datenbestand verfaelscht.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.bma_import import (
    BMA_LAUF_FEHLER,
    BMA_LAUF_OK,
    BMA_LAUF_SESSION_ABGELAUFEN,
    BMA_SATZ_AKTIV,
    BMA_SATZ_VERSCHWUNDEN,
    BMA_ZUORDNUNG_AUTO,
    BMA_ZUORDNUNG_MANUELL,
    BMA_ZUORDNUNG_OFFEN,
    BmaImportLauf,
    BmaImportSatz,
    OrgBmaImportConfig,
)
from app.models.master import FireDept
from app.models.objekt import (
    AUSWAHL_KONTAKTART,
    OBJEKT_STATUS_ARCHIVIERT,
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    Objekt,
    ObjektAuswahl,
    ObjektBMA,
    ObjektKontakt,
)
from app.services.bma_import.bma_client import BmaClient, BmaClientError, BmaSessionAbgelaufenError
from app.services.bma_import.bma_parser import parse_anlagen, parse_kontakte
from app.services.objekt_plan_upload_service import erstelle_objekt_aus_identitaet, finde_passendes_objekt
from app.services.objekt_service import aktualisiere_felder, telefone_zu_json, write_objekt_change

logger = logging.getLogger("einsatzleiter.bma_import.sync")

# Weniger als dieser Anteil der zuletzt aktiven Anlagen -> Antwort gilt als unplausibel
# (Muster: rettungskarten_katalog_service._MIN_KATALOG), nichts wird geschrieben.
_PLAUSIBILITAETS_FAKTOR = 0.5
# Unterhalb dieser Anzahl greift die Plausibilitaetspruefung nicht (verhindert, dass
# eine winzige Test-/Neuorganisation mit 1-3 Anlagen bei jeder kleinen Schwankung blockiert).
_PLAUSIBILITAETS_MINDESTBESTAND = 4
# Detailseite (Kontakte) erneut abrufen, wenn der letzte Abruf laenger her ist -
# faengt Kontaktaenderungen ab, die keine AlarmSystem.ChangeDate-Aenderung ausloesen.
_DETAIL_MAX_ALTER_TAGE = 30

# Neue Kontaktart der Plattform, die es im Standard-Katalog noch nicht gibt (siehe
# app/models/objekt.py::KONTAKT_ARTEN - "brandschutzbeauftragter" existiert schon).
_BMA_ALARMPERSON_CODE = "bma_alarmperson"
_BMA_ALARMPERSON_NAME = "BMA Alarmperson"


def _baue_identitaet(anlage: dict) -> dict:
    return {
        "name": anlage.get("bezeichnung"),
        "strasse": anlage.get("strasse"),
        "hausnummer": anlage.get("hausnummer"),
        "plz": anlage.get("plz"),
        "ort": anlage.get("ort"),
        "bma_nummer": anlage.get("bma_nummer"),
    }


def _stelle_kontaktart_sicher(db: Session, org_id: int) -> None:
    """Legt den Katalogeintrag 'BMA Alarmperson' fuer die Org an, falls er fehlt
    (idempotent, Muster: objekt_service.seed_objekt_auswahl). Ohne diesen Eintrag
    wuerde die importierte Kontaktart in der UI kein Label anzeigen."""
    vorhanden = (
        db.query(ObjektAuswahl)
        .filter(
            ObjektAuswahl.org_id == org_id,
            ObjektAuswahl.typ == AUSWAHL_KONTAKTART,
            ObjektAuswahl.code == _BMA_ALARMPERSON_CODE,
        )
        .execution_options(include_all_tenants=True)
        .first()
    )
    if vorhanden is None:
        max_sort = (
            db.query(ObjektAuswahl)
            .filter(ObjektAuswahl.org_id == org_id, ObjektAuswahl.typ == AUSWAHL_KONTAKTART)
            .execution_options(include_all_tenants=True)
            .count()
        )
        db.add(ObjektAuswahl(
            org_id=org_id, typ=AUSWAHL_KONTAKTART, code=_BMA_ALARMPERSON_CODE,
            name=_BMA_ALARMPERSON_NAME, sort=max_sort + 1, aktiv=True, system=True,
        ))
        db.flush()


def _kontakt_felder(kd: dict) -> dict:
    telefone = kd.get("telefone") or []
    return {
        "art": kd.get("art") or "sonstig",
        "name": (kd.get("name") or "").strip()[:150],
        "telefone_json": telefone_zu_json(", ".join(telefone)) if telefone else None,
        "email": (kd.get("email") or None),
    }


def _sync_kontakte(db: Session, objekt: Objekt, kontakte: list[dict], user_id: int | None) -> bool:
    """Gleicht die importierten Kontakte (extern_quelle='dibos_bma') mit objekt_kontakt
    ab. Haendisch gepflegte Kontakte (extern_quelle IS NULL) werden NIE angefasst.
    Gibt True zurueck, wenn tatsaechlich etwas geaendert wurde."""
    bestehende = {
        k.extern_id: k for k in objekt.kontakte
        if k.extern_quelle == "dibos_bma" and k.extern_id
    }
    gesehen: set[str] = set()
    geaendert = False
    naechster_sort = max([k.sort for k in objekt.kontakte], default=0) + 1

    for kd in kontakte:
        extern_id = kd.get("extern_id")
        name = (kd.get("name") or "").strip()
        if not extern_id or not name:
            continue
        gesehen.add(extern_id)
        felder = _kontakt_felder(kd)
        bestehender = bestehende.get(extern_id)
        if bestehender is not None:
            for feld, neu in felder.items():
                if getattr(bestehender, feld) != neu:
                    setattr(bestehender, feld, neu)
                    geaendert = True
        else:
            db.add(ObjektKontakt(
                org_id=objekt.org_id, objekt_id=objekt.id,
                extern_quelle="dibos_bma", extern_id=extern_id,
                sort=naechster_sort, **felder,
            ))
            naechster_sort += 1
            geaendert = True

    for extern_id, bestehender in bestehende.items():
        if extern_id not in gesehen:
            db.delete(bestehender)
            geaendert = True

    if geaendert:
        write_objekt_change(db, objekt.id, objekt.org_id, "kontakte", "bma_import_sync",
                            before=None, after=f"{len(kontakte)} Kontakt(e) aus DIBOS", user_id=user_id)
    return geaendert


def _sync_bma_block(db: Session, objekt: Objekt, anlage: dict, user_id: int | None) -> bool:
    """Aktualisiert den objekt_bma-Block (eigene Logik statt aktualisiere_felder,
    da ObjektBMA anders als Objekt kein aktualisiert_am/aktualisiert_von_id hat)."""
    if objekt.bma is None:
        objekt.bma = ObjektBMA(org_id=objekt.org_id, objekt_id=objekt.id)
        db.add(objekt.bma)
        db.flush()

    felder = {
        "bma_nummer": anlage.get("bma_nummer"),
        "uebertragungseinrichtung": "RFL aufgeschaltet" if anlage.get("is_rfl") else None,
    }
    geaendert = False
    for feld, neu in felder.items():
        alt = getattr(objekt.bma, feld)
        if alt != neu:
            setattr(objekt.bma, feld, neu)
            write_objekt_change(db, objekt.id, objekt.org_id, "bma", feld,
                                before=alt, after=neu, user_id=user_id)
            geaendert = True
    return geaendert


def wende_anlage_auf_objekt_an(
    db: Session, objekt: Objekt, anlage: dict, kontakte: list[dict] | None, user_id: int | None,
) -> tuple[list[str], bool, bool]:
    """Schreibt Stammdaten, ObjektBMA und (falls kontakte nicht None) die
    Kontakte einer Anlage auf ein Objekt. Gemeinsam genutzt von:
    - _verarbeite_anlage (Auto-Apply auf ein Entwurf-Objekt waehrend des Syncs)
    - ui_bma_import.py::vorschlag_uebernehmen (Uebernahme aus der Review-Queue
      auf eine frisch erstellte Arbeitskopie eines freigegebenen Objekts - eine
      Arbeitskopie hat Status 'entwurf', ist also derselbe Schreibfall).

    `kontakte=None` bedeutet "Kontakte in diesem Aufruf nicht anfassen" (siehe
    Modul-Doc zu soll_detail_holen) - eine leere Liste dagegen loescht alle
    importierten Kontakte (die Anlage hat laut Quelle aktuell keine mehr).
    """
    assert objekt.org_id is not None, "Objekt ohne org_id kann nicht importiert werden"
    if any(k.get("art") == _BMA_ALARMPERSON_CODE for k in (kontakte or [])):
        _stelle_kontaktart_sicher(db, objekt.org_id)

    identitaet = _baue_identitaet(anlage)
    stammdaten_felder = {
        "name": identitaet["name"] or objekt.name,
        "strasse": identitaet["strasse"],
        "hausnummer": identitaet["hausnummer"],
        "plz": identitaet["plz"],
        "ort": identitaet["ort"],
    }
    if anlage.get("lat") is not None:
        stammdaten_felder["lat"] = anlage["lat"]
    if anlage.get("lng") is not None:
        stammdaten_felder["lng"] = anlage["lng"]

    geaenderte_felder = aktualisiere_felder(db, objekt, stammdaten_felder, "stammdaten", user_id=user_id)
    bma_geaendert = _sync_bma_block(db, objekt, anlage, user_id=user_id)
    kontakte_geaendert = _sync_kontakte(db, objekt, kontakte, user_id=user_id) if kontakte is not None else False
    return geaenderte_felder, bma_geaendert, kontakte_geaendert


async def _verarbeite_anlage(
    db: Session, org: FireDept, config: OrgBmaImportConfig, client: BmaClient,
    anlage: dict, jetzt: datetime,
) -> str:
    """Verarbeitet eine einzelne Anlage; gibt einen Ergebnis-Schluessel zurueck:
    'neu_angelegt' | 'aktualisiert' | 'vorschlag' | 'uebersprungen' | 'unveraendert'.
    """
    system_user = SimpleNamespace(org_id=org.id, id=None)

    satz = (
        db.query(BmaImportSatz)
        .filter(BmaImportSatz.org_id == org.id, BmaImportSatz.extern_id == anlage["extern_id"])
        .first()
    )
    ist_neuer_satz = satz is None
    if satz is None:
        satz = BmaImportSatz(
            org_id=org.id, extern_id=anlage["extern_id"], status=BMA_SATZ_AKTIV,
            zuordnung=BMA_ZUORDNUNG_OFFEN, erst_gesehen_am=jetzt, zuletzt_gesehen_am=jetzt,
        )
        db.add(satz)
        db.flush()

    vorherige_rohdaten = json.loads(satz.rohdaten_json) if satz.rohdaten_json else {}
    vorheriges_change_date = satz.quell_change_date

    satz.zuletzt_gesehen_am = jetzt
    satz.status = BMA_SATZ_AKTIV
    satz.bma_nummer = anlage.get("bma_nummer")
    satz.bezeichnung = anlage.get("bezeichnung")
    satz.extern_guid = anlage.get("extern_guid")
    satz.quell_change_date = anlage.get("change_date")

    # Zielobjekt bestimmen: einmal zugeordnet bleibt es stabil (siehe Modul-Doc der
    # Anlage/Adresse-Bewegung), sonst BMA-Nummer/Adresse-Matching, sonst Neuanlage.
    objekt: Objekt | None = None
    if satz.objekt_id is not None:
        objekt = (
            db.query(Objekt)
            .filter(Objekt.id == satz.objekt_id, Objekt.org_id == org.id)
            .first()
        )
    if objekt is None:
        identitaet = _baue_identitaet(anlage)
        objekt = finde_passendes_objekt(db, org.id, identitaet)
        if objekt is not None:
            satz.objekt_id = objekt.id
            if satz.zuordnung == BMA_ZUORDNUNG_OFFEN:
                satz.zuordnung = BMA_ZUORDNUNG_AUTO
        elif config.auto_anlegen:
            objekt = erstelle_objekt_aus_identitaet(db, system_user, identitaet)  # type: ignore[arg-type]
            db.flush()
            satz.objekt_id = objekt.id
            satz.zuordnung = BMA_ZUORDNUNG_AUTO
        else:
            satz.zuordnung = BMA_ZUORDNUNG_OFFEN

    # Detailseite (Kontakte) nur bei Bedarf neu abrufen - neu, geaenderte Quelle,
    # oder der letzte Abruf ist zu lange her (faengt Kontaktaenderungen ohne
    # AlarmSystem.ChangeDate-Bump ab).
    soll_detail_holen = (
        satz.detail_geholt_am is None
        or vorheriges_change_date != satz.quell_change_date
        or (jetzt - satz.detail_geholt_am).days >= _DETAIL_MAX_ALTER_TAGE
    )
    if soll_detail_holen:
        html = await client.hole_detail_html(anlage["extern_id"])
        kontakte = parse_kontakte(html)
        satz.detail_geholt_am = jetzt
    else:
        kontakte = vorherige_rohdaten.get("kontakte") or []

    neue_rohdaten = {"anlage": anlage, "kontakte": kontakte}
    neuer_json = json.dumps(neue_rohdaten, sort_keys=True, default=str, ensure_ascii=False)
    neuer_hash = hashlib.sha256(neuer_json.encode("utf-8")).hexdigest()
    satz.rohdaten_json = neuer_json
    satz.quell_hash = neuer_hash

    if objekt is None:
        db.flush()
        return "uebersprungen"

    if objekt.status == OBJEKT_STATUS_ARCHIVIERT:
        db.flush()
        return "uebersprungen"

    if objekt.status != OBJEKT_STATUS_ENTWURF:
        # freigegeben / in_ueberarbeitung -> nur Queue, Objekt bleibt unangetastet.
        db.flush()
        if satz.bestaetigt_hash == neuer_hash:
            return "unveraendert"
        return "vorschlag"

    # Auto-Apply: Objekt ist (noch) im Entwurf und gehoert damit vollstaendig dem Import.
    kontakte_fuer_apply = kontakte if soll_detail_holen else None
    geaenderte_felder, bma_geaendert, kontakte_geaendert = wende_anlage_auf_objekt_an(
        db, objekt, anlage, kontakte_fuer_apply, user_id=None,
    )

    db.flush()
    if ist_neuer_satz:
        # Erster Lauf, in dem diese Anlage ueberhaupt getrackt wird - unabhaengig
        # davon, ob dabei ein neues Objekt entstand oder ein bestehendes Entwurf-
        # Objekt gefunden wurde, zaehlt das fuer die Laufstatistik als "neu".
        return "neu_angelegt"
    if geaenderte_felder or bma_geaendert or kontakte_geaendert:
        return "aktualisiert"
    return "unveraendert"


def verarbeite_pdf_anlage(
    db: Session, org: FireDept, config: OrgBmaImportConfig, anlage: dict, kontakte: list[dict], user,
) -> tuple[BmaImportSatz, str]:
    """Verarbeitet eine einzelne, aus einem manuell hochgeladenen BMA-Datenblatt-
    PDF geparste Anlage (siehe bma_pdf_parser.py) — Gegenstück zu
    _verarbeite_anlage() fuer den Live-Sync, aber ohne BmaClient: die Kontakte
    sind hier bereits vollstaendig aus dem PDF bekannt, ein soll_detail_holen-
    Abruf (der dort eine Detailseite nachladen wuerde) entfaellt komplett.

    Anders als beim Live-Sync gibt es hier einen echten, eingeloggten Nutzer
    (den Admin, der die Datei hochgeladen hat) statt eines synthetischen
    System-Users — wird direkt an erstelle_objekt_aus_identitaet()/
    wende_anlage_auf_objekt_an() durchgereicht (bessere Zuordnung in
    ObjektChange/write_audit als beim Hintergrund-Sync).

    Gibt (satz, ergebnis) zurueck, ergebnis wie bei _verarbeite_anlage():
    'neu_angelegt' | 'aktualisiert' | 'vorschlag' | 'uebersprungen' | 'unveraendert'.
    Wirft nie durch ausser bei einem Programmierfehler — der Aufrufer
    (ui_bma_import.py) laeuft in einer eigenen Transaktion und braucht hier
    kein Savepoint/Retry (anders als sync_org_bma(), das viele Anlagen in
    einem Lauf verarbeitet).
    """
    jetzt = datetime.now(UTC).replace(tzinfo=None)

    satz = (
        db.query(BmaImportSatz)
        .filter(BmaImportSatz.org_id == org.id, BmaImportSatz.extern_id == anlage["extern_id"])
        .first()
    )
    ist_neuer_satz = satz is None
    if satz is None:
        satz = BmaImportSatz(
            org_id=org.id, extern_id=anlage["extern_id"], status=BMA_SATZ_AKTIV,
            zuordnung=BMA_ZUORDNUNG_OFFEN, erst_gesehen_am=jetzt, zuletzt_gesehen_am=jetzt,
        )
        db.add(satz)
        db.flush()

    satz.zuletzt_gesehen_am = jetzt
    satz.status = BMA_SATZ_AKTIV
    satz.bma_nummer = anlage.get("bma_nummer")
    satz.bezeichnung = anlage.get("bezeichnung")
    satz.detail_geholt_am = jetzt  # Kontakte sind mit dem PDF bereits vollstaendig da

    objekt: Objekt | None = None
    if satz.objekt_id is not None:
        objekt = (
            db.query(Objekt)
            .filter(Objekt.id == satz.objekt_id, Objekt.org_id == org.id)
            .first()
        )
    if objekt is None:
        identitaet = dict(_baue_identitaet(anlage), quelle="bma_pdf_upload")
        objekt = finde_passendes_objekt(db, org.id, identitaet)
        if objekt is not None:
            satz.objekt_id = objekt.id
            if satz.zuordnung == BMA_ZUORDNUNG_OFFEN:
                satz.zuordnung = BMA_ZUORDNUNG_AUTO
        elif config.auto_anlegen:
            objekt = erstelle_objekt_aus_identitaet(db, user, identitaet)
            db.flush()
            satz.objekt_id = objekt.id
            satz.zuordnung = BMA_ZUORDNUNG_AUTO
        else:
            satz.zuordnung = BMA_ZUORDNUNG_OFFEN

    neue_rohdaten = {"anlage": anlage, "kontakte": kontakte}
    neuer_json = json.dumps(neue_rohdaten, sort_keys=True, default=str, ensure_ascii=False)
    neuer_hash = hashlib.sha256(neuer_json.encode("utf-8")).hexdigest()
    satz.rohdaten_json = neuer_json
    satz.quell_hash = neuer_hash

    if objekt is None:
        db.flush()
        return satz, "uebersprungen"
    if objekt.status == OBJEKT_STATUS_ARCHIVIERT:
        db.flush()
        return satz, "uebersprungen"
    if objekt.status != OBJEKT_STATUS_ENTWURF:
        # freigegeben / in_ueberarbeitung -> nur Queue, Objekt bleibt unangetastet.
        db.flush()
        if satz.bestaetigt_hash == neuer_hash:
            return satz, "unveraendert"
        return satz, "vorschlag"

    # Auto-Apply: Objekt ist (noch) im Entwurf und gehoert damit vollstaendig dem Import.
    geaenderte_felder, bma_geaendert, kontakte_geaendert = wende_anlage_auf_objekt_an(
        db, objekt, anlage, kontakte, user_id=user.id if user else None,
    )
    db.flush()
    if ist_neuer_satz:
        return satz, "neu_angelegt"
    if geaenderte_felder or bma_geaendert or kontakte_geaendert:
        return satz, "aktualisiert"
    return satz, "unveraendert"


def baue_diff(satz: BmaImportSatz, objekt: Objekt | None) -> dict:
    """Vergleicht den zuletzt geparsten DIBOS-Stand (rohdaten_json) mit dem
    aktuellen Objektstand, fuer die Diff-Ansicht in der Review-Queue
    (ui_bma_import.py). Reine Anzeige-Hilfsfunktion, schreibt nichts."""
    rohdaten = json.loads(satz.rohdaten_json) if satz.rohdaten_json else {}
    anlage = rohdaten.get("anlage") or {}
    identitaet = _baue_identitaet(anlage)
    aktuell = {
        "name": objekt.name if objekt else None,
        "strasse": objekt.strasse if objekt else None,
        "hausnummer": objekt.hausnummer if objekt else None,
        "plz": objekt.plz if objekt else None,
        "ort": objekt.ort if objekt else None,
    }
    felder = []
    for feld, label in (
        ("name", "Bezeichnung"), ("strasse", "Straße"), ("hausnummer", "Hausnummer"),
        ("plz", "PLZ"), ("ort", "Ort"),
    ):
        neu = identitaet.get(feld)
        alt = aktuell.get(feld)
        if neu != alt:
            felder.append({"feld": feld, "label": label, "aktuell": alt, "neu": neu})
    return {
        "felder": felder,
        "kontakte": rohdaten.get("kontakte") or [],
        "anlage": anlage,
    }


def uebernehme_vorschlag(db: Session, satz: BmaImportSatz, user) -> Objekt:
    """Uebernimmt einen offenen Vorschlag (Objekt freigegeben/in_ueberarbeitung)
    aus der Review-Queue - NIE direkt auf die produktive Zeile, sondern ueber
    eine Arbeitskopie (Muster: der reguläre Ueberarbeitungs-Workflow der
    Objektverwaltung, siehe app/services/objekt_service.py).
    """
    from app.services.objekt_service import erstelle_arbeitskopie, hole_arbeitskopie, uebernimm_arbeitskopie

    if satz.objekt_id is None:
        raise ValueError("Satz ist keinem Objekt zugeordnet")
    objekt = db.query(Objekt).filter(Objekt.id == satz.objekt_id).first()
    if objekt is None:
        raise ValueError("Zugeordnetes Objekt nicht gefunden")

    ziel: Objekt | None
    if objekt.status == OBJEKT_STATUS_FREIGEGEBEN:
        ziel = erstelle_arbeitskopie(db, objekt, user.id)
        db.flush()
    elif objekt.status == OBJEKT_STATUS_UEBERARBEITUNG:
        ziel = hole_arbeitskopie(db, objekt)
        if ziel is None:
            raise ValueError("Objekt ist in Ueberarbeitung, aber es existiert keine offene Arbeitskopie")
    else:
        raise ValueError(f"Uebernehmen ist fuer Status '{objekt.status}' nicht vorgesehen")

    rohdaten = json.loads(satz.rohdaten_json) if satz.rohdaten_json else {}
    anlage = rohdaten.get("anlage") or {}
    kontakte = rohdaten.get("kontakte") or []
    wende_anlage_auf_objekt_an(db, ziel, anlage, kontakte, user_id=user.id)
    db.flush()

    basis = uebernimm_arbeitskopie(db, ziel, user.id)
    satz.bestaetigt_hash = satz.quell_hash
    write_audit(db, "bma_import.vorschlag_uebernommen", org_id=objekt.org_id, user_id=user.id,
                entity_type="objekt", entity_id=basis.id, payload={"satz_id": satz.id})
    return basis


def ignoriere_vorschlag(db: Session, satz: BmaImportSatz, user) -> None:
    """Verwirft einen offenen Vorschlag, ohne das Objekt zu aendern - der Satz
    erscheint erst wieder in der Queue, wenn sich die Quelle erneut aendert."""
    satz.bestaetigt_hash = satz.quell_hash
    write_audit(db, "bma_import.vorschlag_ignoriert", org_id=satz.org_id, user_id=user.id,
                entity_type="bma_import_satz", entity_id=satz.id)


def lege_objekt_fuer_satz_an(db: Session, satz: BmaImportSatz, user) -> Objekt:
    """Legt fuer einen nicht zugeordneten Satz ('Nicht zugeordnet' in der
    Review-Queue) ein neues Entwurf-Objekt an und befuellt es sofort - Muster:
    das Auto-Apply beim naechsten Sync-Lauf, hier nur manuell ausgeloest."""
    if satz.objekt_id is not None:
        raise ValueError("Satz ist bereits einem Objekt zugeordnet")
    rohdaten = json.loads(satz.rohdaten_json) if satz.rohdaten_json else {}
    anlage = rohdaten.get("anlage") or {}
    kontakte = rohdaten.get("kontakte") or []
    identitaet = _baue_identitaet(anlage)

    objekt = erstelle_objekt_aus_identitaet(db, user, identitaet)
    db.flush()
    wende_anlage_auf_objekt_an(db, objekt, anlage, kontakte, user_id=user.id)

    satz.objekt_id = objekt.id
    satz.zuordnung = BMA_ZUORDNUNG_MANUELL
    satz.bestaetigt_hash = satz.quell_hash
    write_audit(db, "bma_import.objekt_manuell_angelegt", org_id=objekt.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id, payload={"satz_id": satz.id})
    return objekt


def ordne_satz_objekt_zu(db: Session, satz: BmaImportSatz, objekt: Objekt, user) -> None:
    """Ordnet einen nicht zugeordneten Satz einem bestehenden Objekt manuell zu
    (Auswahl in der Review-Queue). Schreibt die Daten NICHT automatisch - das
    laeuft ueber den naechsten Sync-Lauf bzw. 'Uebernehmen', je nach Objektstatus."""
    if satz.objekt_id is not None:
        raise ValueError("Satz ist bereits einem Objekt zugeordnet")
    satz.objekt_id = objekt.id
    satz.zuordnung = BMA_ZUORDNUNG_MANUELL
    write_audit(db, "bma_import.satz_manuell_zugeordnet", org_id=objekt.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id, payload={"satz_id": satz.id})


def _beende_lauf(
    db: Session, lauf: BmaImportLauf, config: OrgBmaImportConfig, status: str, meldung: str,
) -> BmaImportLauf:
    lauf.status = status
    lauf.meldung = meldung[:500]
    lauf.beendet_am = datetime.now(UTC).replace(tzinfo=None)
    config.letzter_lauf_am = lauf.beendet_am
    config.letzter_lauf_status = status
    config.letzter_lauf_meldung = meldung[:300]
    db.commit()
    return lauf


async def sync_org_bma(
    db: Session, org: FireDept, config: OrgBmaImportConfig, ausloeser: str = "loop",
) -> BmaImportLauf:
    """Fuehrt einen vollstaendigen BMA-Import-Lauf fuer eine Org aus.

    Wirft nie durch - jeder Fehlerfall endet in einem BmaImportLauf mit
    entsprechendem status/meldung, der Aufrufer (bma_loop.py, Admin-UI) muss
    nur das Ergebnis anzeigen, nicht selbst absichern.

    Voraussetzung (Muster: lis_sync.py::sync_organization): der Aufrufer muss
    VORHER `set_tenant_context(db, None)` gesetzt haben. Diese Funktion filtert
    jede Tenant-Tabellen-Query explizit selbst auf `org.id` - laeuft sie unter
    einem ANDEREN aktiven Org-Kontext (z. B. ein system_admin loest den Import
    fuer eine fremde Org manuell aus, waehrend die eigene Session-Org aktiv ist),
    wuerde der automatische Tenant-Filter zusaetzlich (und widerspruechlich)
    zuschlagen und still 0 Treffer liefern.
    """
    from app.core.crypto import decrypt_secret

    jetzt = datetime.now(UTC).replace(tzinfo=None)
    lauf = BmaImportLauf(org_id=org.id, gestartet_am=jetzt, ausloeser=ausloeser, status=BMA_LAUF_OK)
    db.add(lauf)
    db.flush()

    if not config.is_fully_configured:
        return _beende_lauf(db, lauf, config, BMA_LAUF_FEHLER,
                            "Konfiguration unvollstaendig (Basis-URL/Session-Cookie fehlt)")

    cookie = decrypt_secret(config.session_cookie_enc)  # type: ignore[arg-type]
    client = BmaClient(config.base_url, cookie)  # type: ignore[arg-type]
    try:
        rohe_anlagen = await client.hole_anlagen()
    except BmaSessionAbgelaufenError as exc:
        await client.aclose()
        return _beende_lauf(db, lauf, config, BMA_LAUF_SESSION_ABGELAUFEN, str(exc))
    except BmaClientError as exc:
        await client.aclose()
        return _beende_lauf(db, lauf, config, BMA_LAUF_FEHLER, str(exc))

    anlagen = parse_anlagen({"Data": rohe_anlagen})
    lauf.gefunden = len(anlagen)

    vorheriger_bestand = (
        db.query(BmaImportSatz)
        .filter(BmaImportSatz.org_id == org.id, BmaImportSatz.status == BMA_SATZ_AKTIV)
        .count()
    )
    if (
        vorheriger_bestand >= _PLAUSIBILITAETS_MINDESTBESTAND
        and len(anlagen) < vorheriger_bestand * _PLAUSIBILITAETS_FAKTOR
    ):
        await client.aclose()
        meldung = (
            f"Antwort unplausibel ({len(anlagen)} Anlagen < "
            f"{_PLAUSIBILITAETS_FAKTOR:.0%} von zuletzt {vorheriger_bestand}) - nicht uebernommen."
        )
        logger.warning("BMA-Import (Org %s): %s", org.id, meldung)
        return _beende_lauf(db, lauf, config, BMA_LAUF_FEHLER, meldung)

    gesehene_ids: set[str] = set()
    session_abgelaufen: BmaSessionAbgelaufenError | None = None
    for anlage in anlagen:
        gesehene_ids.add(anlage["extern_id"])
        sp = db.begin_nested()
        try:
            ergebnis = await _verarbeite_anlage(db, org, config, client, anlage, jetzt)
            sp.commit()
            if ergebnis == "neu_angelegt":
                lauf.neu_angelegt += 1
            elif ergebnis == "aktualisiert":
                lauf.aktualisiert += 1
            elif ergebnis == "vorschlag":
                lauf.vorschlaege += 1
            elif ergebnis == "uebersprungen":
                lauf.uebersprungen += 1
        except BmaSessionAbgelaufenError as exc:
            sp.rollback()
            session_abgelaufen = exc
            break
        except Exception:
            sp.rollback()
            lauf.fehler += 1
            logger.exception(
                "BMA-Import: Anlage %s (Org %s) fehlgeschlagen",
                anlage.get("extern_id"), org.id,
            )

    await client.aclose()

    if session_abgelaufen is not None:
        db.commit()  # bereits verarbeitete Anlagen dieses Laufs bleiben erhalten
        return _beende_lauf(db, lauf, config, BMA_LAUF_SESSION_ABGELAUFEN, str(session_abgelaufen))

    # Anlagen, die aktiv waren, aber in dieser Antwort nicht mehr vorkamen -> verschwunden.
    # Kein automatisches Loeschen des zugehoerigen Objekts.
    verschwundene = (
        db.query(BmaImportSatz)
        .filter(BmaImportSatz.org_id == org.id, BmaImportSatz.status == BMA_SATZ_AKTIV)
        .all()
    )
    for satz in verschwundene:
        if satz.extern_id not in gesehene_ids:
            satz.status = BMA_SATZ_VERSCHWUNDEN
            satz.zuletzt_geaendert_am = jetzt

    meldung = (
        f"{lauf.neu_angelegt} neu, {lauf.aktualisiert} aktualisiert, "
        f"{lauf.vorschlaege} Vorschlaege, {lauf.uebersprungen} uebersprungen, {lauf.fehler} Fehler"
    )
    write_audit(db, "bma_import.run", org_id=org.id, user_id=None, payload={
        "gefunden": lauf.gefunden, "neu_angelegt": lauf.neu_angelegt,
        "aktualisiert": lauf.aktualisiert, "vorschlaege": lauf.vorschlaege,
        "uebersprungen": lauf.uebersprungen, "fehler": lauf.fehler,
    })
    return _beende_lauf(db, lauf, config, BMA_LAUF_OK, meldung)
