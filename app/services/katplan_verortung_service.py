"""Lesende Ortskandidaten aus Stammdaten, Adresse und raeumlichem Freitext."""

import hashlib
import json
import math
import re

from sqlalchemy.orm import Session

from app.models.katplan import KatplanElement, KatplanLink
from app.models.master import FireDept
from app.services import geocoding
from app.services.katplan_typen import ELEMENT_TYPEN
from app.services.katplan_ziel_service import ziel_laden, ziel_verfuegbar


def entfernung_m(lat: float, lng: float, lat2: float, lng2: float) -> float:
    """Grosskreisabstand in Metern, auch fuer nahe Punkte stabil."""
    a, b = math.radians(lat), math.radians(lat2)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lng2 - lng) / 2) ** 2
    return 6371000 * 2 * math.asin(min(1.0, math.sqrt(h)))


def kandidat(quelle: str, treffer: dict, org: FireDept | None) -> dict:
    """Stabile Identitaet unabhaengig von Reihenfolge und wechselnden Anzeigenamen."""
    lat, lng = float(treffer["lat"]), float(treffer["lng"])
    ident = f"{quelle}:{lat:.6f}:{lng:.6f}"
    wert = {**treffer, "quelle": quelle, "lat": lat, "lng": lng,
            "kandidat_id": hashlib.sha256(ident.encode()).hexdigest()[:12]}
    if org is not None and org.fallback_lat is not None and org.fallback_lng is not None:
        wert["entfernung_m"] = round(entfernung_m(lat, lng, org.fallback_lat, org.fallback_lng))
    return wert


async def kandidaten(db: Session, org: FireDept, element: KatplanElement, *, max_kandidaten: int = 3) -> list[dict]:
    """Priorisierte, auf 25 Meter deduplizierte Vorschlaege; schreibt keine Daten."""
    if element.org_id != org.id or max_kandidaten <= 0 or ELEMENT_TYPEN[element.typ].geometrie not in (
        "punkt", "beliebig",
    ):
        return []
    ergebnis: list[dict] = []

    def aufnehmen(quelle: str, treffer: dict) -> None:
        wert = kandidat(quelle, treffer, org)
        if not any(entfernung_m(wert["lat"], wert["lng"], k["lat"], k["lng"]) < 25 for k in ergebnis):
            ergebnis.append(wert)

    links = db.query(KatplanLink).filter_by(org_id=org.id, plan_id=element.plan_id, element_id=element.id).filter(
        KatplanLink.ziel_typ.in_(("objekt", "wasserstelle")),
    ).order_by(KatplanLink.sort, KatplanLink.id)
    for link in links:
        if not ziel_verfuegbar(db, org.id, link.ziel_typ):
            continue
        ziel = ziel_laden(db, org.id, link.ziel_typ, link.ziel_id)
        if ziel is not None and ziel.lat is not None and ziel.lng is not None:
            aufnehmen(link.ziel_typ, {"ziel_id": ziel.id, "lat": ziel.lat, "lng": ziel.lng,
                                      "bezeichnung": ziel.name if link.ziel_typ == "objekt" else ziel.bezeichnung,
                                      "genauigkeit": "hoch"})
    ort = element.ort or org.city or ""
    if element.strasse and len(ergebnis) < max_kandidaten:
        adresse = " ".join(v for v in (element.strasse, element.hausnummer, element.plz, ort) if v)
        for t in await geocoding.search_addresses(adresse, limit=3):
            aufnehmen("adresse", t)
    if len(ergebnis) < max_kandidaten:
        teile = [element.titel]
        if element.typ == "kontrollstelle":
            daten = json.loads(element.daten_json or "{}")
            for feld in ("raeumliche_hinweise", "zugaenglichkeit"):
                if daten.get(feld):
                    teile.append(re.split(r"[.!?](?:\s|$)", str(daten[feld]), maxsplit=1)[0])
        teile.append(ort)
        viewbox = None
        if org.fallback_lat is not None and org.fallback_lng is not None:
            viewbox = (org.fallback_lng - .08, org.fallback_lat - .08,
                       org.fallback_lng + .08, org.fallback_lat + .08)
        for t in await geocoding.search_addresses(
            ", ".join(t for t in teile if t), limit=3, viewbox=viewbox, bounded=viewbox is not None,
        ):
            aufnehmen("titel", t)
    return ergebnis[:max_kandidaten]
