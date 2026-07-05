"""Alarm-Matching: verknuepft Einsaetze automatisch mit Objekten.

Drei Stufen (Reihenfolge, erste Treffer-Stufe gewinnt):
1. BMA-/RFL-Nummer im Alarmtext (report_text) — Regex robust gegen
   Schreibvarianten ("bmz 1044", "BMA-Nr.: 1044", "RFL/1044") → bestaetigt
2. Adress-Uebereinstimmung (normalisiert, inkl. Zusatzadressen/Stiegen)
   → bestaetigt (bei mehreren Treffern: alle als Vorschlag)
3. Geo-Naehe (Haversine < OrgSettings.objekt_geo_match_radius_m, Default 75 m)
   → immer nur Vorschlag; wird nach dem Background-Geocoding erneut angestossen

Nur Objekte mit Status freigegeben/in_ueberarbeitung werden gematcht.
Nach Persistenz: broadcast_org {"type": "objekt_match", ...} fuer Board-Panel
und Alarm-Infoscreen (PR6). Keine Melderlinien-Erkennung (Entscheidung 2026-07-05).
"""
from __future__ import annotations

import logging
import math
import re

from sqlalchemy.orm import Session, selectinload

from app.models.incident import Incident
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_EINSATZ_VORSCHLAG,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    Objekt,
    ObjektBMA,
    ObjektEinsatz,
)

logger = logging.getLogger("einsatzleiter.objekt_matching")

# Robust gegen Varianten: "bmz 1044", "BMA-Nr.: 1044", "rfl/1044", "BMZ:1044"
BMA_REGEX = re.compile(r"(?:bmz|bma|rfl)(?:[\s\-]?(?:nr|nummer))?[\s:.\-/]*(\d{2,6})", re.IGNORECASE)

_MATCHBARE_STATUS = (OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG)


def _norm_bma(nummer: str | None) -> str | None:
    """Normalisiert eine BMA-/RFL-Nummer fuer den Vergleich: nur Ziffern, ohne
    fuehrende Nullen (Alarm 'BMA 1044' matcht Objekt-Nr. '01044' und umgekehrt)."""
    if not nummer:
        return None
    ziffern = re.sub(r"\D", "", nummer)
    if not ziffern:
        return None
    return ziffern.lstrip("0") or "0"


def finde_bma_nummern(text: str | None) -> list[str]:
    """Extrahiert alle BMA-/RFL-Nummern aus dem Alarmtext (dedupliziert, Reihenfolge stabil)."""
    if not text:
        return []
    gefunden: list[str] = []
    for treffer in BMA_REGEX.findall(text):
        if treffer not in gefunden:
            gefunden.append(treffer)
    return gefunden


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distanz zweier Koordinaten in Metern."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _normalisierte_adresse(strasse: str | None, hausnummer: str | None, ort: str | None) -> str:
    from app.services.lis.lis_mapping import normalize_address
    street = f"{strasse or ''} {hausnummer or ''}".strip()
    return normalize_address(street, ort)


def _geo_radius_m(db: Session, org_id: int) -> int:
    from app.models.master import OrgSettings
    org_s = (
        db.query(OrgSettings)
        .filter(OrgSettings.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return org_s.objekt_geo_match_radius_m if org_s else 75


def _bestehende_objekt_ids(db: Session, incident_id: int) -> set[int]:
    return {
        oe.objekt_id
        for oe in db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.incident_id == incident_id)
        .execution_options(include_all_tenants=True)
        .all()
    }


def match_incident(db: Session, incident: Incident, *, nur_geo: bool = False) -> list[ObjektEinsatz]:
    """Fuehrt das Matching fuer einen Einsatz aus und persistiert neue Verknuepfungen.

    nur_geo=True: nur Stufe 3 (Aufruf nach Background-Geocoding); Stufe 3 laeuft
    generell nur, wenn es noch keine Verknuepfung gibt. Caller committet.
    """
    org_id = incident.primary_org_id
    if org_id is None:
        return []

    objekte = (
        db.query(Objekt)
        .options(selectinload(Objekt.bma), selectinload(Objekt.zusatzadressen))
        .filter(Objekt.org_id == org_id, Objekt.status.in_(_MATCHBARE_STATUS))
        .execution_options(include_all_tenants=True)
        .all()
    )
    if not objekte:
        return []

    vorhandene = _bestehende_objekt_ids(db, incident.id)
    neu: list[ObjektEinsatz] = []

    def _anlegen(objekt: Objekt, quelle: str, status: str, distanz_m: int | None = None) -> None:
        if objekt.id in vorhandene:
            return
        eintrag = ObjektEinsatz(
            org_id=org_id,
            objekt_id=objekt.id,
            incident_id=incident.id,
            quelle=quelle,
            status=status,
            distanz_m=distanz_m,
        )
        db.add(eintrag)
        vorhandene.add(objekt.id)
        neu.append(eintrag)

    if not nur_geo:
        # ── Stufe 1: BMA-/RFL-Nummer im Alarmtext ──
        nummern = finde_bma_nummern(incident.report_text) + finde_bma_nummern(incident.reason)
        if nummern:
            bma_index: dict[str, Objekt] = {}
            for o in objekte:
                bma: ObjektBMA | None = o.bma
                if bma is None:
                    continue
                for wert in (bma.bma_nummer, bma.rfl_nummer):
                    norm = _norm_bma(wert)
                    if norm:
                        bma_index.setdefault(norm, o)
            for nummer in nummern:
                objekt = bma_index.get(_norm_bma(nummer) or "")
                if objekt is not None:
                    _anlegen(objekt, "bma", OBJEKT_EINSATZ_BESTAETIGT)
        if neu:
            return neu

        # ── Stufe 2: Adress-Uebereinstimmung (inkl. Zusatzadressen) ──
        einsatz_adresse = _normalisierte_adresse(
            incident.address_street, incident.address_no, incident.address_city
        )
        if einsatz_adresse:
            adress_treffer: list[Objekt] = []
            for o in objekte:
                kandidaten = [_normalisierte_adresse(o.strasse, o.hausnummer, o.ort)]
                kandidaten += [
                    _normalisierte_adresse(z.strasse, z.hausnummer, z.ort)
                    for z in o.zusatzadressen
                ]
                if any(k and k == einsatz_adresse for k in kandidaten):
                    adress_treffer.append(o)
            if len(adress_treffer) == 1:
                _anlegen(adress_treffer[0], "adresse", OBJEKT_EINSATZ_BESTAETIGT)
            elif len(adress_treffer) > 1:
                # Mehrdeutig → alle nur als Vorschlag
                for o in adress_treffer:
                    _anlegen(o, "adresse", OBJEKT_EINSATZ_VORSCHLAG)
        if neu:
            return neu

    # ── Stufe 3: Geo-Naehe (nur wenn noch keine Verknuepfung existiert) ──
    if vorhandene:
        return neu
    if incident.lat is None or incident.lng is None:
        return neu
    radius = _geo_radius_m(db, org_id)
    naechstes: tuple[float, Objekt] | None = None
    for o in objekte:
        if o.lat is None or o.lng is None:
            continue
        distanz = _haversine_m(incident.lat, incident.lng, o.lat, o.lng)
        if distanz <= radius and (naechstes is None or distanz < naechstes[0]):
            naechstes = (distanz, o)
    if naechstes is not None:
        _anlegen(naechstes[1], "geo", OBJEKT_EINSATZ_VORSCHLAG, distanz_m=int(naechstes[0]))
    return neu


async def _broadcast_matches(incident: Incident, neu: list[ObjektEinsatz], db: Session) -> None:
    """WS-Event fuer Board-Panel + Alarm-Infoscreen."""
    from app.services.broadcast import broadcast_org
    if not neu or incident.primary_org_id is None:
        return
    objekt_namen = {}
    for oe in neu:
        objekt = db.get(Objekt, oe.objekt_id)
        if objekt:
            objekt_namen[oe.objekt_id] = objekt.name
    try:
        await broadcast_org(incident.primary_org_id, {
            "type": "objekt_match",
            "incident_id": incident.id,
            "matches": [
                {
                    "objekt_id": oe.objekt_id,
                    "name": objekt_namen.get(oe.objekt_id, ""),
                    "quelle": oe.quelle,
                    "status": oe.status,
                }
                for oe in neu
            ],
        })
    except Exception:
        logger.exception("objekt_match-Broadcast fehlgeschlagen (Einsatz %d)", incident.id)


async def match_incident_background(incident_id: int, *, nur_geo: bool = False) -> None:
    """Background-Task (Muster _geocode_incident): eigene Session, nie Request blockieren."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if incident is None:
            return
        # Modul-Gate: Matching nur, wenn Objektverwaltung fuer die Org aktiv ist
        from app.services.objekt_service import objekt_effective_enabled
        if not objekt_effective_enabled(incident.primary_org_id, db):
            return
        neu = match_incident(db, incident, nur_geo=nur_geo)
        if neu:
            db.commit()
            await _broadcast_matches(incident, neu, db)
    except Exception:
        logger.exception("Objekt-Matching fehlgeschlagen (Einsatz %d)", incident_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
