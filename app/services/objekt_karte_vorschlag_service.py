"""Vorschlaege getrennt von der produktiven Karte; Transaktion beim Aufrufer."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.objekt import Objekt, ObjektDokument, ObjektKartenObjekt, ObjektKartenVorschlag
from app.services.objekt_karte_service import (
    EINSATZKRITISCHE_SYMBOLTYPEN,
    ObjektKartenFehler,
    _karten_rotation,
    karten_objekte_lesen,
    symbolkatalog_lesen,
)
from app.services.objekt_service import write_objekt_change

MAX_ABSTAND_OBJEKT_M = 2000
DUBLETTEN_ABSTAND_M = 3
BILD_TOLERANZ = 0.01
FREIGABE_STATUS = {"offen", "angenommen", "abgelehnt", "ersetzt"}
_FELDER = {
    "georef_id", "bild_x", "bild_y", "basis_marker_id", "operation", "symbol_code", "label", "rotation", "lat", "lng",
    "geometry_json", "bild_richtung_x", "bild_richtung_y", "batch_id",
    "position_status", "position_methode", "genauigkeit_m", "erkennungs_konfidenz", "quelle_dokument_id",
    "quelle_seite", "quelle_ausschnitt", "quelle_dokument_hash", "begruendung", "expected_revision", "quelle",
}


def produktives_objekt(db: Session, org_id: int, objekt_id: int) -> Objekt:
    objekt = db.query(Objekt).filter_by(id=objekt_id, org_id=org_id).first()
    if objekt is None:
        raise ObjektKartenFehler("Objekt nicht gefunden", 404)
    if objekt.entwurf_von_id is not None:
        raise ObjektKartenFehler(f"Arbeitskopie: bitte Basisobjekt {objekt.entwurf_von_id} verwenden")
    return objekt


def _abstand(lat: float | None, lng: float | None, lat2: float | None, lng2: float | None) -> float:
    if lat is None or lng is None or lat2 is None or lng2 is None:
        return math.inf
    a, b, c, d = map(math.radians, (lat, lng, lat2, lng2))
    h = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, h)))


def _hash(dokument: ObjektDokument) -> str | None:
    root = Path(settings.OBJEKT_MEDIA_DIR).resolve()
    pfad = (root / dokument.pfad).resolve()
    if not pfad.is_relative_to(root):
        return None
    try:
        with pfad.open("rb") as datei:
            return hashlib.file_digest(datei, "sha256").hexdigest()
    except OSError:
        return None


def _dokument(db: Session, objekt: Objekt, dokument_id: int) -> ObjektDokument:
    dokument = db.query(ObjektDokument).filter_by(id=dokument_id, objekt_id=objekt.id, org_id=objekt.org_id).first()
    if dokument is None:
        raise ObjektKartenFehler("Quelldokument nicht gefunden")
    return dokument


def _veraltet(db: Session, objekt: Objekt, v: ObjektKartenVorschlag | ObjektKartenObjekt) -> bool:
    if v.quelle_dokument_id is None:
        return bool(v.quelle_dokument_hash)
    dokument = db.query(ObjektDokument).filter_by(
        id=v.quelle_dokument_id, objekt_id=objekt.id, org_id=objekt.org_id).first()
    if dokument is None:
        return True
    gruppe = dokument.dokument_gruppe_id or dokument.id
    aktuell = db.query(ObjektDokument).filter(
        ObjektDokument.objekt_id == objekt.id, ObjektDokument.org_id == objekt.org_id,
        (ObjektDokument.id == gruppe) | (ObjektDokument.dokument_gruppe_id == gruppe),
        ObjektDokument.ist_aktuelle_version.is_(True),
    ).order_by(ObjektDokument.versionsnummer.desc()).first()
    return aktuell is None or bool(v.quelle_dokument_hash and _hash(aktuell) != v.quelle_dokument_hash)


def vorschlag_dict(v: ObjektKartenVorschlag, db: Session | None = None, objekt: Objekt | None = None) -> dict[str, Any]:
    daten = {name: getattr(v, name) for name in _FELDER}
    daten["bild_richtung"] = ({"dx": v.bild_richtung_x, "dy": v.bild_richtung_y}
                              if v.bild_richtung_x is not None and v.bild_richtung_y is not None else None)
    daten.update(id=v.id, objekt_id=v.objekt_id, freigabe_status=v.freigabe_status,
                 warnungen=json.loads(v.warnungen_json or "[]"), erstellt_von=v.erstellt_von,
                 erstellt_am=v.erstellt_am.isoformat() if v.erstellt_am else None,
                 geprueft_von=v.geprueft_von, geprueft_am=v.geprueft_am.isoformat() if v.geprueft_am else None)
    if (v.georef_id is not None or v.position_methode == "plan_georeferenzierung") and db is not None and objekt:
        from app.models.objekt import ObjektDokumentGeoref
        from app.services.objekt_georef_service import georef_pruefen
        g = db.query(ObjektDokumentGeoref).filter_by(
            id=v.georef_id, org_id=objekt.org_id, objekt_id=objekt.id).first()
        status = georef_pruefen(db, objekt, g) if g else "veraltet"
        if status != "gueltig" and v.position_methode != "manuell_korrigiert":
            daten["altposition"] = {"lat": v.lat, "lng": v.lng, "genauigkeit_m": v.genauigkeit_m}
            daten.update(lat=None, lng=None, genauigkeit_m=None,
                         position_status="veraltet" if v.position_status == "veraltet" else "pruefung_erforderlich")
            daten["warnungen"] = daten["warnungen"] + [{"code": "georef_ungueltig"}, {"code": "georef_" + status}]
            if v.bild_richtung_x is not None and v.bild_richtung_y is not None:
                daten["rotation"] = 0
                if not any(w["code"] == "richtung_ohne_georef" for w in daten["warnungen"]):
                    daten["warnungen"].append({"code": "richtung_ohne_georef"})
    return daten


def vorschlaege_auflisten(db: Session, objekt: Objekt, filter_status: str | None = None,
                         batch_id: str | None = None) -> list[ObjektKartenVorschlag]:
    query = db.query(ObjektKartenVorschlag).filter_by(objekt_id=objekt.id, org_id=objekt.org_id)
    if filter_status is not None:
        if filter_status not in FREIGABE_STATUS:
            raise ObjektKartenFehler("Unbekannter Freigabestatus")
        query = query.filter_by(freigabe_status=filter_status)
    if batch_id is not None:
        batch_id_validieren(batch_id)
        query = query.filter_by(batch_id=batch_id)
    return query.order_by(ObjektKartenVorschlag.id).all()


def batch_id_validieren(batch_id: str) -> None:
    if not isinstance(batch_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", batch_id) is None:
        raise ObjektKartenFehler("batch_id: 1-64 Zeichen aus A-Za-z0-9_.:- erforderlich")


def symbol_basisrichtung(db: Session, org_id: int | None, symbol_code: str) -> int:
    """Pfeile zeigen ohne Rotation nach Osten, andere Symbole nach Norden."""
    if org_id is None:
        raise ObjektKartenFehler("Organisation erforderlich")
    symbol = next((s for s in symbolkatalog_lesen(db, org_id) if s["code"] == symbol_code), None)
    return symbol["basisrichtung_grad"] if symbol else 0


def _zahl(wert: Any, feld: str, minimum: float, maximum: float) -> float:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise ObjektKartenFehler(f"{feld}: endliche Zahl erforderlich")
    if not minimum <= wert <= maximum:
        raise ObjektKartenFehler(f"{feld}: ungueltiger Wertebereich")
    return float(wert)


def _json(wert: Any) -> str | None:
    if wert is None:
        return None
    try:
        return json.dumps(json.loads(wert) if isinstance(wert, str) else wert, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        raise ObjektKartenFehler("Ungueltiges JSON") from None


def _validiere(db: Session, objekt: Objekt, daten: dict) -> dict:
    if set(daten) - _FELDER - {"geometry"}:
        raise ObjektKartenFehler("Unbekannte Vorschlagsfelder")
    d = dict(daten)
    if d.get("bild_richtung_x") is not None or d.get("bild_richtung_y") is not None:
        dx = _zahl(d.get("bild_richtung_x"), "dx", -1, 1)
        dy = _zahl(d.get("bild_richtung_y"), "dy", -1, 1)
        if math.hypot(dx, dy) <= 1e-6:
            raise ObjektKartenFehler("bild_richtung: Vektor zu kurz")
    if d.get("batch_id") is not None:
        batch_id_validieren(d["batch_id"])
    if objekt.org_id is None:
        raise ObjektKartenFehler("Organisation erforderlich")
    d.setdefault("operation", "hinzufuegen")
    d.setdefault("position_methode", "unbekannt")
    d.setdefault("quelle", "mcp")
    if not isinstance(d["operation"], str) or d["operation"] not in {"hinzufuegen", "aendern", "entfernen"}:
        raise ObjektKartenFehler("Unbekannte Operation")
    codes = {s["code"] for s in symbolkatalog_lesen(db, objekt.org_id)} | {"geometrie"}
    if not isinstance(d.get("symbol_code"), str) or d["symbol_code"] not in codes:
        raise ObjektKartenFehler("Unbekannter Symbolcode im Org-Katalog")
    if not isinstance(d["quelle"], str) or d["quelle"] not in {"mcp", "ui"}:
        raise ObjektKartenFehler("Unbekannte Quelle")
    if not isinstance(d["position_methode"], str) or not 1 <= len(d["position_methode"]) <= 40:
        raise ObjektKartenFehler("Ungueltige position_methode")
    for feld in ("basis_marker_id", "quelle_dokument_id", "expected_revision", "quelle_seite"):
        if d.get(feld) is not None and (type(d[feld]) is not int or d[feld] < 1):
            raise ObjektKartenFehler(f"{feld}: positive Ganzzahl erforderlich")
    if d.get("quelle_seite", 1) is not None and d.get("quelle_seite", 1) > 32767:
        raise ObjektKartenFehler("quelle_seite zu gross")
    if d["operation"] in {"aendern", "entfernen"} and (not d.get("basis_marker_id") or not d.get("expected_revision")):
        raise ObjektKartenFehler("basis_marker_id und expected_revision erforderlich")
    if d.get("basis_marker_id"):
        marker = next((m for m in karten_objekte_lesen(db, objekt) if m.id == d["basis_marker_id"]), None)
        if marker is None:
            raise ObjektKartenFehler("Basismarker nicht gefunden")
        if d.get("expected_revision") != marker.revision:
            raise ObjektKartenFehler("Versionskonflikt: expected_revision stimmt nicht", 409)
    for feld, grenze in (("lat", 90), ("lng", 180), ("erkennungs_konfidenz", 1), ("genauigkeit_m", math.inf)):
        if d.get(feld) is not None:
            d[feld] = _zahl(d[feld], feld, -grenze if feld in {"lat", "lng"} else 0, grenze)
    if (d.get("lat") is None) != (d.get("lng") is None):
        raise ObjektKartenFehler("lat und lng gemeinsam angeben oder weglassen")
    d["rotation"] = _karten_rotation(d.get("rotation", 0))
    d["geometry_json"] = _json(d.pop("geometry", d.get("geometry_json")))
    if d["geometry_json"]:
        geom = json.loads(d["geometry_json"])
        if not isinstance(geom, dict) or geom.get("type") not in {
            "Point", "LineString", "Polygon", "MultiPolygon", "MultiLineString", "MultiPoint"
        }:
            raise ObjektKartenFehler("GeoJSON-Geometrie erforderlich")
        def koordinaten(werte: Any) -> None:
            if not isinstance(werte, list) or not werte:
                raise ObjektKartenFehler("Ungueltige Geometriekoordinaten")
            if isinstance(werte[0], list):
                for teil in werte:
                    koordinaten(teil)
            else:
                if len(werte) != 2:
                    raise ObjektKartenFehler("Koordinatenpaar erforderlich")
                _zahl(werte[0], "lng", -180, 180)
                _zahl(werte[1], "lat", -90, 90)
        koordinaten(geom.get("coordinates"))
    status = d.get("position_status", "vorgeschlagen")
    if not isinstance(status, str) or status not in {"vorgeschlagen", "pruefung_erforderlich"}:
        raise ObjektKartenFehler("Positionsbestaetigung erfolgt separat")
    d["position_status"] = status if d.get("lat") is not None or d["geometry_json"] else "pruefung_erforderlich"
    for feld, laenge in (("label", 100), ("begruendung", 10000)):
        if d.get(feld) is not None and (not isinstance(d[feld], str) or len(d[feld]) > laenge):
            raise ObjektKartenFehler(f"{feld}: ungueltiger Text")
    d["label"] = (d.get("label") or "").strip() or None
    if d.get("quelle_ausschnitt") is not None:
        bbox = json.loads(_json(d["quelle_ausschnitt"]) or "null")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ObjektKartenFehler("quelle_ausschnitt: normierte Bbox [x0,y0,x1,y1] erforderlich")
        bbox = [_zahl(w, "Bbox", 0, 1) for w in bbox]
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ObjektKartenFehler("Leere Bbox")
        d["quelle_ausschnitt"] = _json(bbox)
    if d.get("quelle_dokument_hash") is not None:
        wert = d["quelle_dokument_hash"]
        if not isinstance(wert, str) or len(wert) != 64 or any(c not in "0123456789abcdef" for c in wert):
            raise ObjektKartenFehler("SHA-256 als 64 Hexzeichen erforderlich")
    if d.get("quelle_dokument_id"):
        dokument = _dokument(db, objekt, d["quelle_dokument_id"])
        if not d.get("quelle_dokument_hash"):
            d["quelle_dokument_hash"] = _hash(dokument)
    return d


def _dublette(code: str, label: str | None, lat: float | None, lng: float | None,
              code2: str, label2: str | None, lat2: float | None, lng2: float | None) -> bool:
    return code == code2 and (bool(label and label == label2) or _abstand(lat, lng, lat2, lng2) < DUBLETTEN_ABSTAND_M)


def vorschlag_speichern(db: Session, objekt: Objekt, daten: dict, *, user_id: int,
                       vorschlag_id: int | None = None, audit_quelle: str = "mcp") -> dict:
    daten = dict(daten)
    richtung = daten.pop("bild_richtung", None)
    if richtung is not None:
        if "rotation" in daten:
            raise ObjektKartenFehler("rotation und bild_richtung gleichzeitig sind nicht erlaubt")
        if not isinstance(richtung, dict) or set(richtung) != {"dx", "dy"}:
            raise ObjektKartenFehler("bild_richtung: dx und dy erforderlich")
        dx, dy = _zahl(richtung["dx"], "dx", -1, 1), _zahl(richtung["dy"], "dy", -1, 1)
        if math.hypot(dx, dy) <= 1e-6:
            raise ObjektKartenFehler("bild_richtung: Vektor zu kurz")
        daten.update(bild_richtung_x=dx, bild_richtung_y=dy)
    if objekt.entwurf_von_id is not None:
        raise ObjektKartenFehler("Vorschlaege nur am Basisobjekt anlegen")
    alle = vorschlaege_auflisten(db, objekt, "offen")
    v = next((v for v in alle if v.id == vorschlag_id), None) if vorschlag_id is not None else None
    if vorschlag_id is not None and v is None:
        raise ObjektKartenFehler("Offener Vorschlag nicht gefunden", 404)
    vorher = vorschlag_dict(v) if v else None
    bestand = {k: getattr(v, k) for k in _FELDER} if v else {}
    if "rotation" in daten or daten.get("georef_id", bestand.get("georef_id")) is None:
        bestand.update(bild_richtung_x=None, bild_richtung_y=None)
    if "quelle_dokument_id" in daten and daten["quelle_dokument_id"] != bestand.get("quelle_dokument_id"):
        bestand["quelle_dokument_hash"] = None
    if daten.get("georef_id") is not None and any(
        daten.get(k) is not None for k in ("lat", "lng", "geometry", "geometry_json")
    ):
        raise ObjektKartenFehler("georef_id und Geokoordinaten gleichzeitig sind nicht erlaubt")
    if daten.get("georef_id", bestand.get("georef_id")) is not None and any(
        daten.get(k) is not None for k in ("lat", "lng", "geometry", "geometry_json")
    ):
        raise ObjektKartenFehler("Georeferenz vor direkter Koordinateneingabe entfernen")
    if daten.get("georef_id", bestand.get("georef_id")) is not None:
        bestand.update(lat=None, lng=None, geometry_json=None)
    # Die alternative geometry-Eingabe ersetzt auch bestehende Geometrie.
    d = _validiere(db, objekt, bestand | daten)
    hat_richtung = d.get("bild_richtung_x") is not None and d.get("bild_richtung_y") is not None
    if hat_richtung:
        if d.get("georef_id") is None or d.get("bild_x") is None or d.get("bild_y") is None:
            raise ObjektKartenFehler("bild_richtung benoetigt georef_id und bild_x/bild_y")
        d["rotation"] = 0
    georef_warnung = None
    if d.get("georef_id") is not None:
        from app.models.objekt import ObjektDokumentGeoref
        from app.services.objekt_georef_service import (
            MAX_POSITIONSUNSICHERHEIT_M,
            bild_richtung_zu_peilung,
            bild_zu_geo,
            georef_pruefen,
        )
        if type(d["georef_id"]) is not int or d["georef_id"] < 1:
            raise ObjektKartenFehler("georef_id: positive Ganzzahl erforderlich")
        g = db.query(ObjektDokumentGeoref).filter_by(
            id=d["georef_id"], org_id=objekt.org_id, objekt_id=objekt.id).first()
        if g is None:
            raise ObjektKartenFehler("Georeferenz nicht gefunden")
        x = _zahl(d.get("bild_x"), "bild_x", 0, 1)
        y = _zahl(d.get("bild_y"), "bild_y", 0, 1)
        for feld, wert in (("quelle_dokument_id", g.dokument_id), ("quelle_seite", g.seite)):
            if d.get(feld) is not None and d[feld] != wert:
                raise ObjektKartenFehler("Georeferenz und Quelldokument/Seite widersprechen sich")
            d[feld] = wert
        d.update(lat=None, lng=None, genauigkeit_m=None, position_status="pruefung_erforderlich",
                 position_methode="plan_georeferenzierung", quelle_dokument_hash=g.dokument_hash)
        status = georef_pruefen(db, objekt, g)
        if status == "gueltig":
            d["lat"], d["lng"], d["genauigkeit_m"] = bild_zu_geo(g, x, y)
            if hat_richtung:
                peilung = bild_richtung_zu_peilung(g, x, y, d["bild_richtung_x"], d["bild_richtung_y"])
                d["rotation"] = round(peilung - symbol_basisrichtung(db, objekt.org_id, d["symbol_code"])) % 360
            if d["genauigkeit_m"] <= MAX_POSITIONSUNSICHERHEIT_M:
                d["position_status"] = "vorgeschlagen"
            else:
                georef_warnung = "georef_ungenau"
        else:
            georef_warnung = "georef_" + status
    elif d.get("bild_x") is not None or d.get("bild_y") is not None:
        raise ObjektKartenFehler("Bildkoordinaten benoetigen georef_id")
    if v is None and d.get("georef_id") is not None:
        for marker in karten_objekte_lesen(db, objekt):
            if (marker.typ == d["symbol_code"] and marker.quelle_dokument_hash == d["quelle_dokument_hash"]
                    and marker.quelle_seite == d["quelle_seite"]
                    and _abstand(marker.lat, marker.lng, d.get("lat"), d.get("lng")) < DUBLETTEN_ABSTAND_M):
                return {"ergebnis": "unveraendert", "marker_id": marker.id, "symbol_code": marker.typ,
                        "lat": marker.lat, "lng": marker.lng}
        v = next((a for a in alle
                  if a.quelle_dokument_hash == d["quelle_dokument_hash"] and a.quelle_seite == d["quelle_seite"]
                  and a.symbol_code == d["symbol_code"] and a.operation == d["operation"]
                  and a.bild_x is not None and a.bild_y is not None
                  and math.hypot(d["bild_x"] - a.bild_x, d["bild_y"] - a.bild_y) < BILD_TOLERANZ), None)
        if v is not None:
            return vorschlag_speichern(db, objekt, daten, user_id=user_id,
                                      vorschlag_id=v.id, audit_quelle=audit_quelle)
    ergebnis = "aktualisiert" if v is not None else "neu"
    if v is None:
        schluessel: tuple[str, ...] = (
            "georef_id", "bild_x", "bild_y", "quelle", "quelle_dokument_id", "quelle_seite", "quelle_ausschnitt",
            "quelle_dokument_hash", "symbol_code", "lat", "lng", "geometry_json", "operation", "basis_marker_id",
            "expected_revision",
        )
        # Unverortete Kandidaten mit verschiedenen Labels bleiben unterscheidbar.
        if d.get("lat") is None and d["geometry_json"] is None:
            schluessel += ("label",)
        identisch = next((a for a in alle if all(getattr(a, k) == d.get(k) for k in schluessel)), None)
        if identisch:
            if "batch_id" in d:
                identisch.batch_id = d["batch_id"]
                db.flush()
            return vorschlag_dict(identisch, db, objekt) | {"ergebnis": "unveraendert_offen"}
    warnungen: list[dict[str, Any]] = [{"code": georef_warnung}] if georef_warnung else []
    if hat_richtung and georef_warnung and d.get("lat") is None:
        warnungen.append({"code": "richtung_ohne_georef"})
    if _abstand(d.get("lat"), d.get("lng"), objekt.lat, objekt.lng) > MAX_ABSTAND_OBJEKT_M and all(
        w is not None for w in (d.get("lat"), d.get("lng"), objekt.lat, objekt.lng)
    ):
        warnungen.append({"code": "weit_vom_objekt"})
    code = d["symbol_code"]
    if code in EINSATZKRITISCHE_SYMBOLTYPEN or code.startswith(("gefahr_", "hydrant_")):
        warnungen.append({"code": "einsatzkritisch_freigabe_erforderlich"})
    for m in karten_objekte_lesen(db, objekt):
        if m.id != d.get("basis_marker_id") and _dublette(
            code, d.get("label"), d.get("lat"), d.get("lng"), m.typ, m.label, m.lat, m.lng
        ):
            warnungen.append({"code": "moegliche_dublette", "marker_id": m.id})
    for a in alle:
        if a is not v and _dublette(
            code, d.get("label"), d.get("lat"), d.get("lng"), a.symbol_code, a.label, a.lat, a.lng
        ):
            warnungen.append({"code": "moegliche_dublette", "vorschlag_id": a.id})
    warnungen_json = json.dumps(warnungen)
    if v is not None:
        def gleich(alt: Any, neu: Any) -> bool:
            if isinstance(alt, float) and isinstance(neu, (float, int)):
                return math.isclose(alt, neu, rel_tol=0, abs_tol=1e-9)
            return bool(alt == neu)

        if all(gleich(getattr(v, k), d.get(k)) for k in _FELDER - {"batch_id"}) and v.warnungen_json == warnungen_json:
            if "batch_id" in d and v.batch_id != d["batch_id"]:
                v.batch_id = d["batch_id"]
                db.flush()
            return vorschlag_dict(v, db, objekt) | {"ergebnis": "unveraendert_offen"}
    if v is None:
        v = ObjektKartenVorschlag(objekt_id=objekt.id, org_id=objekt.org_id, erstellt_von=user_id)
        db.add(v)
    for k, wert in d.items():
        setattr(v, k, wert)
    v.warnungen_json = warnungen_json
    db.flush()
    antwort = vorschlag_dict(v)
    write_objekt_change(db, objekt.id, objekt.org_id, "karte", "vorschlag_geaendert" if vorher else "vorschlag_neu",
                        before=vorher, after=antwort, user_id=user_id, quelle=audit_quelle)
    db.flush()
    return antwort | {"ergebnis": ergebnis}


def vorschlag_verwerfen(db: Session, objekt: Objekt, vorschlag_id: int, *, user_id: int) -> dict:
    v = next((v for v in vorschlaege_auflisten(db, objekt, "offen") if v.id == vorschlag_id), None)
    if v is None:
        raise ObjektKartenFehler("Offener Vorschlag nicht gefunden", 404)
    vorher = vorschlag_dict(v)
    v.freigabe_status = "abgelehnt"
    write_objekt_change(db, objekt.id, objekt.org_id, "karte", "vorschlag_verworfen", before=vorher,
                        after=vorschlag_dict(v), user_id=user_id, quelle="mcp")
    db.flush()
    return vorschlag_dict(v, db, objekt)


def pruefe_karte(db: Session, objekt: Objekt) -> dict:
    """Nur lesen: effektiver Status veraltet wird in der Antwort markiert, nie persistiert."""
    marker = karten_objekte_lesen(db, objekt)
    dubletten = [{"marker_id": a.id, "referenz_marker_id": b.id, "code": "moegliche_dublette"}
                 for i, a in enumerate(marker) for b in marker[i + 1:]
                 if _dublette(a.typ, a.label, a.lat, a.lng, b.typ, b.label, b.lat, b.lng)]
    vorschlaege = []
    for v in vorschlaege_auflisten(db, objekt, "offen"):
        daten = vorschlag_dict(v, db, objekt)
        if v.georef_id is None and _veraltet(db, objekt, v):
            daten["position_status"] = "veraltet"
        vorschlaege.append(daten)
    return {"dubletten": dubletten, "marker_ohne_koordinaten": [m.id for m in marker if m.lat is None or m.lng is None],
            "anzahl_ungeprueft": sum(m.position_status == "ungeprueft" for m in marker)
            + sum(v["position_status"] == "ungeprueft" for v in vorschlaege),
            "luecken": ["bmz"] if objekt.bma is not None and not any(m.typ == "bmz" for m in marker) else [],
            "vorschlaege": vorschlaege}
