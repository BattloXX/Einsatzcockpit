"""BMA-Datenblatt-PDF-Upload: Zuordnung, Upsert und Review-Queue."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.bma_import import (
    BMA_SATZ_AKTIV,
    BMA_ZUORDNUNG_AUTO,
    BMA_ZUORDNUNG_MANUELL,
    BMA_ZUORDNUNG_OFFEN,
    BmaImportSatz,
    OrgBmaImportConfig,
)
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
from app.services.objekt_plan_upload_service import erstelle_objekt_aus_identitaet, finde_passendes_objekt
from app.services.objekt_service import aktualisiere_felder, telefone_zu_json, write_objekt_change

_BMA_ALARMPERSON_CODE = "bma_alarmperson"
_BMA_ALARMPERSON_NAME = "BMA Alarmperson"


def hole_oder_erstelle_config(db: Session, org_id: int) -> OrgBmaImportConfig:
    config = db.query(OrgBmaImportConfig).filter(OrgBmaImportConfig.org_id == org_id).first()
    if config is None:
        config = OrgBmaImportConfig(org_id=org_id, auto_anlegen=True)
        db.add(config)
        db.flush()
    return config


def _baue_identitaet(anlage: dict) -> dict:
    return {k: anlage.get(k) for k in ("name", "strasse", "hausnummer", "plz", "ort", "bma_nummer")} | {
        "name": anlage.get("bezeichnung")
    }


def _stelle_kontaktart_sicher(db: Session, org_id: int) -> None:
    vorhanden = db.query(ObjektAuswahl).filter(
        ObjektAuswahl.org_id == org_id,
        ObjektAuswahl.typ == AUSWAHL_KONTAKTART,
        ObjektAuswahl.code == _BMA_ALARMPERSON_CODE,
    ).execution_options(include_all_tenants=True).first()
    if vorhanden is None:
        sort = db.query(ObjektAuswahl).filter(
            ObjektAuswahl.org_id == org_id, ObjektAuswahl.typ == AUSWAHL_KONTAKTART,
        ).execution_options(include_all_tenants=True).count() + 1
        db.add(ObjektAuswahl(org_id=org_id, typ=AUSWAHL_KONTAKTART,
                            code=_BMA_ALARMPERSON_CODE, name=_BMA_ALARMPERSON_NAME,
                            sort=sort, aktiv=True, system=True))
        db.flush()


def _kontakt_felder(kontakt: dict) -> dict:
    telefone = kontakt.get("telefone") or []
    return {
        "art": kontakt.get("art") or "sonstig",
        "name": (kontakt.get("name") or "").strip()[:150],
        "telefone_json": telefone_zu_json(", ".join(telefone)) if telefone else None,
        "email": kontakt.get("email") or None,
    }


def _kontakt_praefix(satz: BmaImportSatz) -> str:
    return f"{satz.extern_id}:"


def _importierte_kontakt_ids(satz: BmaImportSatz, objekt: Objekt) -> set[str]:
    praefix = _kontakt_praefix(satz)
    return {k.extern_id for k in objekt.kontakte
            if k.extern_quelle == "dibos_bma" and k.extern_id and k.extern_id.startswith(praefix)}


def kontakt_abweichung(satz: BmaImportSatz, objekt: Objekt) -> tuple[list[dict], list[str]]:
    rohdaten = json.loads(satz.rohdaten_json or "{}")
    kontakte = rohdaten.get("kontakte") or []
    erwartet = {k.get("extern_id"): k for k in kontakte if k.get("extern_id")}
    vorhanden = _importierte_kontakt_ids(satz, objekt)
    return [k for extern_id, k in erwartet.items() if extern_id not in vorhanden], sorted(vorhanden - erwartet.keys())


def ist_offener_vorschlag(satz: BmaImportSatz, objekt: Objekt | None) -> bool:
    if objekt is None or objekt.status not in (OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG):
        return False
    if satz.ignoriert_hash == satz.quell_hash:
        return False
    if satz.bestaetigt_hash != satz.quell_hash:
        return True
    fehlende, _ = kontakt_abweichung(satz, objekt)
    return bool(fehlende)


def _sync_kontakte(db: Session, satz: BmaImportSatz, objekt: Objekt,
                    kontakte: list[dict], user_id: int | None) -> bool:
    praefix = _kontakt_praefix(satz)
    bestehende = {k.extern_id: k for k in objekt.kontakte
                  if k.extern_quelle == "dibos_bma" and k.extern_id and k.extern_id.startswith(praefix)}
    gesehen: set[str] = set()
    geaendert = False
    naechster_sort = max((k.sort for k in objekt.kontakte), default=0) + 1
    for daten in kontakte:
        extern_id = daten.get("extern_id")
        if not extern_id or not (daten.get("name") or "").strip():
            continue
        gesehen.add(extern_id)
        felder = _kontakt_felder(daten)
        kontakt = bestehende.get(extern_id)
        if kontakt is None:
            db.add(ObjektKontakt(org_id=objekt.org_id, objekt_id=objekt.id,
                                 extern_quelle="dibos_bma", extern_id=extern_id,
                                 sort=naechster_sort, **felder))
            naechster_sort += 1
            geaendert = True
        else:
            for feld, wert in felder.items():
                if getattr(kontakt, feld) != wert:
                    setattr(kontakt, feld, wert)
                    geaendert = True
    for extern_id, kontakt in bestehende.items():
        if extern_id not in gesehen:
            db.delete(kontakt)
            geaendert = True
    if geaendert:
        write_objekt_change(db, objekt.id, objekt.org_id, "kontakte", "bma_import_sync",
                            before=None, after=f"{len(kontakte)} Kontakt(e) aus BMA-Datenblatt",
                            user_id=user_id)
    return geaendert


def _sync_bma_block(db: Session, objekt: Objekt, anlage: dict, user_id: int | None) -> bool:
    if objekt.bma is None:
        objekt.bma = ObjektBMA(org_id=objekt.org_id, objekt_id=objekt.id)
        db.add(objekt.bma)
        db.flush()
    felder = {"bma_nummer": anlage.get("bma_nummer"),
              "uebertragungseinrichtung": "RFL aufgeschaltet" if anlage.get("is_rfl") else None}
    geaendert = False
    for feld, wert in felder.items():
        alt = getattr(objekt.bma, feld)
        if alt != wert:
            setattr(objekt.bma, feld, wert)
            write_objekt_change(db, objekt.id, objekt.org_id, "bma", feld,
                                before=alt, after=wert, user_id=user_id)
            geaendert = True
    return geaendert


def wende_anlage_auf_objekt_an(db: Session, satz: BmaImportSatz, objekt: Objekt,
                               anlage: dict, kontakte: list[dict], user_id: int | None) -> tuple[list[str], bool, bool]:
    _stelle_kontaktart_sicher(db, objekt.org_id)
    identitaet = _baue_identitaet(anlage)
    felder = {k: identitaet.get(k) for k in ("name", "strasse", "hausnummer", "plz", "ort") if identitaet.get(k)}
    geaenderte_felder = aktualisiere_felder(db, objekt, felder, user_id=user_id)
    bma_geaendert = _sync_bma_block(db, objekt, anlage, user_id)
    kontakte_geaendert = _sync_kontakte(db, satz, objekt, kontakte, user_id)
    return geaenderte_felder, bma_geaendert, kontakte_geaendert


def _quell_hash(anlage: dict, kontakte: list[dict]) -> tuple[str, str]:
    rohdaten = json.dumps({"anlage": anlage, "kontakte": kontakte}, ensure_ascii=False, sort_keys=True)
    return rohdaten, hashlib.sha256(rohdaten.encode("utf-8")).hexdigest()


def verarbeite_pdf_anlage(db: Session, org_id: int, config: OrgBmaImportConfig,
                          anlage: dict, kontakte: list[dict], user) -> tuple[BmaImportSatz, str]:
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    extern_id = anlage["extern_id"]
    satz = db.query(BmaImportSatz).filter(
        BmaImportSatz.org_id == org_id, BmaImportSatz.extern_id == extern_id,
    ).execution_options(include_all_tenants=True).first()
    if satz is None:
        satz = BmaImportSatz(org_id=org_id, extern_id=extern_id)
        db.add(satz)
    satz.bma_nummer = anlage.get("bma_nummer")
    satz.bezeichnung = anlage.get("bezeichnung")
    satz.rohdaten_json, satz.quell_hash = _quell_hash(anlage, kontakte)
    satz.status = BMA_SATZ_AKTIV
    satz.zuletzt_gesehen_am = jetzt
    satz.zuletzt_geaendert_am = jetzt
    db.flush()

    objekt = db.get(Objekt, satz.objekt_id) if satz.objekt_id else finde_passendes_objekt(db, org_id, _baue_identitaet(anlage))
    if objekt is None and config.auto_anlegen:
        objekt = erstelle_objekt_aus_identitaet(db, user, _baue_identitaet(anlage))
        satz.zuordnung = BMA_ZUORDNUNG_AUTO
    if objekt is None:
        satz.objekt_id = None
        satz.zuordnung = BMA_ZUORDNUNG_OFFEN
        return satz, "offen"
    satz.objekt_id = objekt.id
    if satz.zuordnung == BMA_ZUORDNUNG_OFFEN:
        satz.zuordnung = BMA_ZUORDNUNG_AUTO
    if objekt.status == OBJEKT_STATUS_ARCHIVIERT:
        return satz, "uebersprungen"
    if objekt.status == OBJEKT_STATUS_ENTWURF:
        wende_anlage_auf_objekt_an(db, satz, objekt, anlage, kontakte, user.id)
        satz.bestaetigt_hash = satz.quell_hash
        satz.ignoriert_hash = None
        return satz, "angewendet"
    db.flush()
    return satz, "vorschlag" if ist_offener_vorschlag(satz, objekt) else "unveraendert"


def baue_diff(satz: BmaImportSatz, objekt: Objekt | None) -> dict:
    rohdaten = json.loads(satz.rohdaten_json or "{}")
    anlage, kontakte = rohdaten.get("anlage") or {}, rohdaten.get("kontakte") or []
    felder = []
    if objekt is not None:
        for feld, label in (("name", "Name"), ("strasse", "Straße"), ("hausnummer", "Hausnummer"),
                            ("plz", "PLZ"), ("ort", "Ort")):
            neu = anlage.get("bezeichnung") if feld == "name" else anlage.get(feld)
            if neu and getattr(objekt, feld) != neu:
                felder.append({"feld": feld, "label": label, "aktuell": getattr(objekt, feld), "neu": neu})
        fehlend, ueberzaehlig = kontakt_abweichung(satz, objekt)
    else:
        fehlend, ueberzaehlig = [], []
    return {"felder": felder, "kontakte": kontakte, "kontakte_fehlend": fehlend,
            "kontakte_ueberzaehlig": ueberzaehlig,
            "grund": "quelle_geaendert" if satz.bestaetigt_hash != satz.quell_hash else "kontakte_fehlen"}


def uebernehme_vorschlag(db: Session, satz: BmaImportSatz, user) -> Objekt:
    from app.services.objekt_service import erstelle_arbeitskopie, uebernimm_arbeitskopie
    basis = db.get(Objekt, satz.objekt_id)
    if basis is None:
        raise ValueError("Kein Zielobjekt")
    rohdaten = json.loads(satz.rohdaten_json or "{}")
    ziel = basis if basis.status == OBJEKT_STATUS_ENTWURF else erstelle_arbeitskopie(db, basis, user.id)
    wende_anlage_auf_objekt_an(db, satz, ziel, rohdaten.get("anlage") or {}, rohdaten.get("kontakte") or [], user.id)
    if ziel is not basis:
        db.expire(ziel, ["kontakte"])
        uebernimm_arbeitskopie(db, ziel, user.id)
        db.expire(basis, ["kontakte"])
    satz.bestaetigt_hash = satz.quell_hash
    satz.ignoriert_hash = None
    write_audit(db, "bma_import.vorschlag_uebernommen", org_id=basis.org_id, user_id=user.id,
                entity_type="objekt", entity_id=basis.id, payload={"satz_id": satz.id})
    return basis


def ignoriere_vorschlag(db: Session, satz: BmaImportSatz, user) -> None:
    satz.ignoriert_hash = satz.quell_hash
    write_audit(db, "bma_import.vorschlag_ignoriert", org_id=satz.org_id, user_id=user.id,
                entity_type="bma_import_satz", entity_id=satz.id)


def lege_objekt_fuer_satz_an(db: Session, satz: BmaImportSatz, user) -> Objekt:
    rohdaten = json.loads(satz.rohdaten_json or "{}")
    anlage, kontakte = rohdaten.get("anlage") or {}, rohdaten.get("kontakte") or []
    objekt = erstelle_objekt_aus_identitaet(db, user, _baue_identitaet(anlage))
    satz.objekt_id, satz.zuordnung = objekt.id, BMA_ZUORDNUNG_MANUELL
    wende_anlage_auf_objekt_an(db, satz, objekt, anlage, kontakte, user.id)
    satz.bestaetigt_hash, satz.ignoriert_hash = satz.quell_hash, None
    return objekt


def ordne_satz_objekt_zu(db: Session, satz: BmaImportSatz, objekt: Objekt, user) -> None:
    if satz.objekt_id is not None:
        raise ValueError("Satz ist bereits einem Objekt zugeordnet")
    satz.objekt_id, satz.zuordnung = objekt.id, BMA_ZUORDNUNG_MANUELL
    write_audit(db, "bma_import.satz_manuell_zugeordnet", org_id=objekt.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id, payload={"satz_id": satz.id})
