"""Kernlogik: Fahrtenbuch – Plausibilität, Erfassung, Korrektur."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.fahrtenbuch import (
    Fahrt,
    FahrtErfassungsweg,
    FahrtKategorie,
    FahrtStatus,
    Fahrtzweck,
)
from app.models.master import Member, OrgSettings, VehicleMaster

logger = logging.getLogger("einsatzleiter.fahrtenbuch")

_SEILWINDE_SCHWELLE_DEFAULT = Decimal("10")


@dataclass
class ZaehlerErgebnis:
    aktuell: Decimal | int
    neu: Decimal | int
    delta: Decimal | int
    warnung: bool


def pruefe_zaehler(
    fahrzeug: VehicleMaster,
    art: str,
    neuer_wert: Decimal | int,
) -> ZaehlerErgebnis:
    """Prüft einen Zählerstand auf Plausibilität.

    art: 'km' | 'bh' | 'seilwinde_bh'
    Raises HTTPException(422) wenn neuer_wert < aktuell.
    """
    if art == "km":
        aktuell = int(fahrzeug.km_aktuell or 0)
        schwelle = int(fahrzeug.warn_schwelle_km or 50)
        neu = int(neuer_wert)
    elif art == "bh":
        aktuell = Decimal(str(fahrzeug.betriebsstunden_aktuell or 0))
        schwelle = Decimal(str(fahrzeug.warn_schwelle_bh or 10))
        neu = Decimal(str(neuer_wert))
    elif art == "seilwinde_bh":
        aktuell = Decimal(str(fahrzeug.seilwinde_bh_aktuell or 0))
        schwelle = _SEILWINDE_SCHWELLE_DEFAULT
        neu = Decimal(str(neuer_wert))
    else:
        raise ValueError(f"Unbekannte Zählerart: {art}")

    if neu < aktuell:
        raise HTTPException(
            status_code=422,
            detail=f"Zählerstand kann nicht sinken: aktuell {aktuell}, neu {neu}",
        )

    delta = neu - aktuell
    warnung = delta > schwelle
    return ZaehlerErgebnis(aktuell=aktuell, neu=neu, delta=delta, warnung=warnung)


def pruefe_doppelfahrt(fahrzeug: VehicleMaster, db: Session, jetzt: datetime | None = None) -> bool:
    """Gibt True zurück wenn in den letzten fahrt_doppel_minuten bereits eine Fahrt erfasst wurde."""
    if jetzt is None:
        jetzt = datetime.now(UTC)
    org = db.query(OrgSettings).filter(OrgSettings.org_id == fahrzeug.dept_id).first()
    fenster_minuten = (org.fahrt_doppel_minuten if org else 10) or 10
    grenze = jetzt - timedelta(minutes=fenster_minuten)
    existiert = (
        db.query(Fahrt)
        .filter(
            Fahrt.fahrzeug_id == fahrzeug.id,
            Fahrt.status == FahrtStatus.aktiv,
            Fahrt.zeitpunkt >= grenze,
        )
        .options()
        .execution_options(include_all_tenants=True)
        .first()
    )
    return existiert is not None


def recompute_zaehlerstand(fahrzeug: VehicleMaster, art: str, db: Session) -> None:
    """Setzt den Zählerstand auf den höchsten aktiven Wert aller Fahrten des Fahrzeugs."""
    if art == "km":
        result = (
            db.query(func.max(Fahrt.km_stand_neu))
            .filter(Fahrt.fahrzeug_id == fahrzeug.id, Fahrt.status == FahrtStatus.aktiv)
            .execution_options(include_all_tenants=True)
            .scalar()
        )
        if result is not None:
            fahrzeug.km_aktuell = int(result)
    elif art == "bh":
        result = (
            db.query(func.max(Fahrt.betriebsstunden_neu))
            .filter(Fahrt.fahrzeug_id == fahrzeug.id, Fahrt.status == FahrtStatus.aktiv)
            .execution_options(include_all_tenants=True)
            .scalar()
        )
        if result is not None:
            fahrzeug.betriebsstunden_aktuell = Decimal(str(result))
    elif art == "seilwinde_bh":
        result = (
            db.query(func.max(Fahrt.seilwinde_bh_neu))
            .filter(Fahrt.fahrzeug_id == fahrzeug.id, Fahrt.status == FahrtStatus.aktiv)
            .execution_options(include_all_tenants=True)
            .scalar()
        )
        if result is not None:
            fahrzeug.seilwinde_bh_aktuell = Decimal(str(result))


def erstelle_fahrt(daten: dict[str, Any], db: Session) -> Fahrt:
    """Validiert und speichert eine neue Fahrt. Aktualisiert Zählerstände atomar."""
    fahrzeug_id = daten["fahrzeug_id"]
    fahrzeug = db.query(VehicleMaster).filter(VehicleMaster.id == fahrzeug_id).execution_options(include_all_tenants=True).first()
    if not fahrzeug:
        raise HTTPException(status_code=404, detail="Fahrzeug nicht gefunden")

    zweck = db.query(Fahrtzweck).filter(Fahrtzweck.id == daten["zweck_id"]).first()
    if not zweck:
        raise HTTPException(status_code=404, detail="Fahrtzweck nicht gefunden")

    # Zähler-Plausibilität
    km_delta = None
    bh_delta = None
    sw_delta = None

    if fahrzeug.erfasst_km and daten.get("km_stand_neu") is not None:
        erg = pruefe_zaehler(fahrzeug, "km", daten["km_stand_neu"])
        if erg.warnung and not daten.get("km_warnung_bestaetigt"):
            raise HTTPException(status_code=422, detail="km_warnung_nicht_bestaetigt")
        km_delta = int(erg.delta)

    if fahrzeug.erfasst_betriebsstunden and daten.get("betriebsstunden_neu") is not None:
        erg = pruefe_zaehler(fahrzeug, "bh", daten["betriebsstunden_neu"])
        if erg.warnung and not daten.get("bh_warnung_bestaetigt"):
            raise HTTPException(status_code=422, detail="bh_warnung_nicht_bestaetigt")
        bh_delta = Decimal(str(erg.delta))

    if fahrzeug.seilwinde_abfrage and daten.get("seilwinde_bh_neu") is not None:
        erg = pruefe_zaehler(fahrzeug, "seilwinde_bh", daten["seilwinde_bh_neu"])
        if erg.warnung and not daten.get("seilwinde_warnung_bestaetigt"):
            raise HTTPException(status_code=422, detail="seilwinde_warnung_nicht_bestaetigt")
        sw_delta = Decimal(str(erg.delta))

    # Maschinist-Name auflösen (Member → denormalisierter Name)
    maschinist_name = daten.get("maschinist_name", "")
    if daten.get("maschinist_member_id") and not maschinist_name:
        m = db.query(Member).filter(Member.id == daten["maschinist_member_id"]).first()
        if m:
            maschinist_name = m.full_name

    fahrt = Fahrt(
        org_id=daten["org_id"],
        zeitpunkt=daten.get("zeitpunkt") or datetime.now(UTC),
        fahrzeug_id=fahrzeug_id,
        maschinist_member_id=daten.get("maschinist_member_id"),
        maschinist_name=maschinist_name,
        maschinist2_member_id=daten.get("maschinist2_member_id"),
        maschinist2_name=daten.get("maschinist2_name"),
        km_stand_neu=daten.get("km_stand_neu"),
        km_delta=km_delta,
        km_warnung_bestaetigt=bool(daten.get("km_warnung_bestaetigt")),
        betriebsstunden_neu=daten.get("betriebsstunden_neu"),
        betriebsstunden_delta=bh_delta,
        bh_warnung_bestaetigt=bool(daten.get("bh_warnung_bestaetigt")),
        seilwinde_bh_neu=daten.get("seilwinde_bh_neu"),
        seilwinde_bh_delta=sw_delta,
        seilwinde_warnung_bestaetigt=bool(daten.get("seilwinde_warnung_bestaetigt")),
        seilwinde_bediener_member_id=daten.get("seilwinde_bediener_member_id"),
        seilwinde_bediener_name=daten.get("seilwinde_bediener_name"),
        seilwinde_zuege=daten.get("seilwinde_zuege"),
        seilwinde_wartung=daten.get("seilwinde_wartung"),
        zielort_id=daten.get("zielort_id"),
        zielort_freitext=daten.get("zielort_freitext"),
        zweck_id=daten["zweck_id"],
        fahrttyp=zweck.kategorie,
        incident_id=daten.get("incident_id"),
        ausbildner_member_id=daten.get("ausbildner_member_id"),
        ausbildner_name=daten.get("ausbildner_name"),
        gruppenkommandant_member_id=daten.get("gruppenkommandant_member_id"),
        gruppenkommandant_name=daten.get("gruppenkommandant_name"),
        schaden_vorhanden=bool(daten.get("schaden_vorhanden")),
        schaden_betriebsfaehig=daten.get("schaden_betriebsfaehig"),
        schaden_beschreibung=daten.get("schaden_beschreibung"),
        bemerkung=daten.get("bemerkung"),
        nicht_statistikrelevant=bool(daten.get("nicht_statistikrelevant")),
        status=FahrtStatus.aktiv,
        erfasst_von_user_id=daten.get("erfasst_von_user_id"),
        erfasst_via=daten.get("erfasst_via", FahrtErfassungsweg.web),
        token_label=daten.get("token_label"),
    )
    db.add(fahrt)
    db.flush()

    # Zählerstände aktualisieren
    if fahrzeug.erfasst_km and daten.get("km_stand_neu") is not None:
        fahrzeug.km_aktuell = int(daten["km_stand_neu"])
    if fahrzeug.erfasst_betriebsstunden and daten.get("betriebsstunden_neu") is not None:
        fahrzeug.betriebsstunden_aktuell = Decimal(str(daten["betriebsstunden_neu"]))
    if fahrzeug.seilwinde_abfrage and daten.get("seilwinde_bh_neu") is not None:
        fahrzeug.seilwinde_bh_aktuell = Decimal(str(daten["seilwinde_bh_neu"]))

    write_audit(
        db,
        action="fahrt_erstellt",
        org_id=daten["org_id"],
        user_id=daten.get("erfasst_von_user_id"),
        entity_type="fahrt",
        entity_id=fahrt.id,
    )
    return fahrt


def storniere_fahrt(fahrt: Fahrt, grund: str, user_id: int, db: Session) -> None:
    """Setzt Status auf storniert. Zählerstände werden nicht automatisch zurückgesetzt."""
    fahrt.status = FahrtStatus.storniert
    fahrt.storno_grund = grund
    fahrt.geaendert_von_user_id = user_id
    write_audit(
        db,
        action="fahrt_storniert",
        org_id=fahrt.org_id,
        user_id=user_id,
        entity_type="fahrt",
        entity_id=fahrt.id,
        payload={"grund": grund},
    )
    # Zählerstände auf höchsten aktiven Wert zurücksetzen
    fahrzeug = db.query(VehicleMaster).filter(VehicleMaster.id == fahrt.fahrzeug_id).execution_options(include_all_tenants=True).first()
    if fahrzeug:
        for art in ["km", "bh", "seilwinde_bh"]:
            recompute_zaehlerstand(fahrzeug, art, db)


def korrigiere_fahrt(original: Fahrt, neue_daten: dict[str, Any], user_id: int, db: Session) -> Fahrt:
    """Erstellt eine korrigierte Version; markiert Original als ersetzt."""
    neue_daten["org_id"] = original.org_id
    neue_daten["erfasst_von_user_id"] = user_id
    neue_daten["erfasst_via"] = original.erfasst_via
    neue_daten["token_label"] = original.token_label

    neue_fahrt = erstelle_fahrt(neue_daten, db)
    neue_fahrt.original_fahrt_id = original.id

    original.status = FahrtStatus.ersetzt
    original.ersetzt_durch_id = neue_fahrt.id
    original.geaendert_von_user_id = user_id

    write_audit(
        db,
        action="fahrt_korrigiert",
        org_id=original.org_id,
        user_id=user_id,
        entity_type="fahrt",
        entity_id=original.id,
        payload={"neue_fahrt_id": neue_fahrt.id},
    )
    return neue_fahrt


def stammdaten_korrektur_zaehler(
    fahrzeug: VehicleMaster, art: str, wert: Decimal | int, user_id: int, db: Session
) -> None:
    """Setzt Zählerstand direkt (ohne Plausibilisierung). Nur für Admin/Leitung."""
    if art == "km":
        fahrzeug.km_aktuell = int(wert)
    elif art == "bh":
        fahrzeug.betriebsstunden_aktuell = Decimal(str(wert))
    elif art == "seilwinde_bh":
        fahrzeug.seilwinde_bh_aktuell = Decimal(str(wert))
    write_audit(
        db,
        action="zaehler_korrektur",
        org_id=fahrzeug.dept_id,
        user_id=user_id,
        entity_type="vehicle_master",
        entity_id=fahrzeug.id,
        payload={"art": art, "neuer_wert": str(wert)},
    )
