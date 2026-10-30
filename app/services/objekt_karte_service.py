"""Lesen und Pflegen der Objekt-Lagekarte.

erstelle_arbeitskopie/uebernimm_arbeitskopie in objekt_service kopieren Marker
und ersetzen sie beim Merge komplett. Dabei aendern sich die IDs; spaetere
Freigabe-Logik muss das beruecksichtigen.
"""

from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from app.models.objekt import OBJEKT_SYMBOL_TYPEN, Objekt, ObjektKartenObjekt, ObjektSymbol
from app.services.objekt_service import write_objekt_change
from app.services.objekt_symbol_service import lade_symbol_katalog

EINSATZKRITISCHE_SYMBOLTYPEN = frozenset({
    "fsd", "bmz", "fbf", "hauptzugang", "nebenzugang", "dlk_stellplatz",
    "hydrant_ueberflur", "hydrant_unterflur",
})


SYMBOL_ALIASE: dict[str, list[str]] = {
    "fsd": ["Feuerwehrschlüsseldepot", "FSD", "Schlüsselsafe"],
    "schluesselbox": ["Schlüsselbox"],
    "bsp": ["Brandschutzplan-Ablage", "Brandschutzplanablage"],
    "bmz": ["Brandmeldezentrale", "Brandmelderzentrale", "BMZ"],
    "fbf": ["Feuerwehrbedienfeld", "Feuerwehr-Bedienfeld", "FBF"],
    "dlk_stellplatz": ["Drehleiter-Stellplatz", "Drehleiteraufstellfläche"],
    "objektfunk": ["Objektfunk-Bedienfeld"],
    "sammelplatz": ["Sammelplatz", "Sammelstelle"],
    "feuerloescher": ["Feuerlöscher", "Tragbarer Feuerlöscher"],
    "hauptzugang": ["Feuerwehrzugang", "Hauptzugang", "Angriffsweg"],
    "nebenzugang": ["Nebenzugang"],
    "stiege": ["Stiege", "Stiegenhaus", "Treppenhaus"],
    "aufzug": ["Aufzug", "Lift"],
    "gefahr_ex": ["Explosionsgefahr", "EX-Bereich"],
    "gefahr_gas": ["Gasgefahr"],
    "gefahr_chemie": ["Chemische Gefahr"],
    "gefahr_strom": ["Hochspannung", "Elektrische Gefahr"],
    "gefahr_pv": ["Photovoltaikanlage", "PV-Anlage"],
    "hydrant_ueberflur": ["Überflurhydrant", "OH"],
    "hydrant_unterflur": ["Unterflurhydrant", "UH"],
}


def stil_basisrichtung(stil: str) -> int:
    """Pfeilstile zeigen bei Rotation 0 nach Osten."""
    return 90 if stil in {"pfeil-voll", "pfeil-leer"} else 0


class ObjektKartenFehler(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.status_code = status_code


def _karten_objekt_dict(k: ObjektKartenObjekt) -> dict:
    from app.models.objekt import parse_karten_geometry

    return {
        "id": k.id,
        "typ": k.typ,
        "lat": k.lat,
        "lng": k.lng,
        "geometry": parse_karten_geometry(k.geometry_json),
        "label": k.label,
        "rotation": k.rotation % 360,
        "position_status": k.position_status,
    }


def _karten_rotation(wert: object) -> int:
    try:
        # Nur ganze Grad akzeptieren; ungueltige JSON-Werte werden abgewiesen.
        if not isinstance(wert, (int, str)) or isinstance(wert, bool):
            raise ValueError
        return int(wert) % 360
    except ValueError:
        raise ObjektKartenFehler("Rotation muss eine ganze Gradzahl sein") from None


def karten_objekte_lesen(db: Session, objekt: Objekt) -> list[ObjektKartenObjekt]:
    return (
        db.query(ObjektKartenObjekt)
        .filter(ObjektKartenObjekt.objekt_id == objekt.id, ObjektKartenObjekt.org_id == objekt.org_id)
        .order_by(ObjektKartenObjekt.sort, ObjektKartenObjekt.id)
        .all()
    )


def karten_lesen(db: Session, objekt: Objekt) -> dict:
    return {
        "objekt": {"id": objekt.id, "lat": objekt.lat, "lng": objekt.lng, "name": objekt.name},
        "eintraege": [_karten_objekt_dict(k) for k in karten_objekte_lesen(db, objekt)],
    }


def karten_objekt_neu(db: Session, objekt: Objekt, daten: dict[str, Any], *, user_id: int) -> dict:
    typ = str(daten.get("typ", ""))
    if typ not in OBJEKT_SYMBOL_TYPEN and typ != "geometrie" and not db.query(ObjektSymbol).filter(
        ObjektSymbol.org_id == objekt.org_id, ObjektSymbol.code == typ, ObjektSymbol.aktiv.is_(True)
    ).first():
        raise ObjektKartenFehler("Unbekannter Symboltyp")
    geometry = daten.get("geometry")
    lat = daten.get("lat")
    lng = daten.get("lng")
    if geometry is None and (lat is None or lng is None):
        raise ObjektKartenFehler("lat/lng oder geometry erforderlich")

    max_sort = (
        db.query(ObjektKartenObjekt)
        .filter(ObjektKartenObjekt.objekt_id == objekt.id, ObjektKartenObjekt.org_id == objekt.org_id)
        .count()
    )
    eintrag = ObjektKartenObjekt(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        typ=typ,
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
        geometry_json=_json.dumps(geometry, ensure_ascii=False) if geometry else None,
        label=(str(daten.get("label") or "").strip() or None),
        sort=max_sort + 1,
        rotation=_karten_rotation(daten.get("rotation", 0)),
    )
    db.add(eintrag)
    write_objekt_change(db, objekt.id, objekt.org_id, "karte", "symbol_neu", before=None, after=typ, user_id=user_id)
    db.commit()
    return _karten_objekt_dict(eintrag)


def karten_objekt_update(db: Session, objekt: Objekt, eintrag_id: int, daten: dict[str, Any], *, user_id: int) -> dict:
    eintrag = (
        db.query(ObjektKartenObjekt)
        .filter(
            ObjektKartenObjekt.id == eintrag_id,
            ObjektKartenObjekt.objekt_id == objekt.id,
            ObjektKartenObjekt.org_id == objekt.org_id,
        )
        .first()
    )
    if eintrag is None:
        raise ObjektKartenFehler("Kartenobjekt nicht gefunden", 404)
    vorher = _karten_objekt_dict(eintrag)
    if "rotation" in daten:
        eintrag.rotation = _karten_rotation(daten["rotation"])
    if "lat" in daten:
        eintrag.lat = float(daten["lat"]) if daten["lat"] is not None else None
    if "lng" in daten:
        eintrag.lng = float(daten["lng"]) if daten["lng"] is not None else None
    if "geometry" in daten:
        geometry = daten["geometry"]
        eintrag.geometry_json = _json.dumps(geometry, ensure_ascii=False) if geometry else None
    if "label" in daten:
        eintrag.label = str(daten["label"] or "").strip() or None
    eintrag.revision += 1
    write_objekt_change(
        db, objekt.id, objekt.org_id, "karte", "symbol_geaendert",
        before=vorher, after=_karten_objekt_dict(eintrag), user_id=user_id,
    )
    db.commit()
    return _karten_objekt_dict(eintrag)


def karten_objekt_loeschen(db: Session, objekt: Objekt, eintrag_id: int, *, user_id: int) -> dict:
    eintrag = (
        db.query(ObjektKartenObjekt)
        .filter(
            ObjektKartenObjekt.id == eintrag_id,
            ObjektKartenObjekt.objekt_id == objekt.id,
            ObjektKartenObjekt.org_id == objekt.org_id,
        )
        .first()
    )
    if eintrag is None:
        raise ObjektKartenFehler("Kartenobjekt nicht gefunden", 404)
    write_objekt_change(
        db, objekt.id, objekt.org_id, "karte", "symbol_geloescht", before=eintrag.typ, after=None, user_id=user_id
    )
    db.delete(eintrag)
    db.commit()
    return {"ok": True}


def symbolkatalog_lesen(db: Session, org_id: int) -> list[dict]:
    """Liest den Org-Katalog ohne Seeding oder sonstige Schreibwirkung."""
    return [
        {
            "code": s.code,
            "name": s.name,
            "stil": s.stil,
            "text": s.text,
            "system": s.system,
            "aliase": list(SYMBOL_ALIASE.get(s.code, [])),
            "traegt_richtung": stil_basisrichtung(s.stil) == 90,
            "basisrichtung_grad": stil_basisrichtung(s.stil),
            "einsatzkritisch": s.code in EINSATZKRITISCHE_SYMBOLTYPEN or s.code.startswith(("gefahr_", "hydrant_")),
        }
        for s in lade_symbol_katalog(db, org_id)
    ]
