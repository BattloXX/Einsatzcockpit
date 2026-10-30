"""Gepruefte BSP-Ausschnitte: Entwurf, Rektifizierung und sichere Auslieferung.

Der Aufrufer committet. Bildkoordinaten gelten auf der gedrehten Seite.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw
from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import OBJEKT_VERWALTER_ROLES, has_role
from app.models.objekt import (
    BspOverlayEinsatz,
    Objekt,
    ObjektBspOverlay,
    ObjektDokument,
    ObjektDokumentGeoref,
    ObjektDokumentSeite,
    ObjektEinsatz,
)
from app.models.user import User
from app.services import objekt_georef_service as geo
from app.services.objekt_karte_service import ObjektKartenFehler
from app.services.objekt_service import write_objekt_change

MIN_AUSSCHNITT = 0.001
PLAN_ARTEN = {"lageplan", "hallenbereich", "gefahrenzone", "geschoss", "sonstig"}


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ausschnitt_validieren(daten: Any) -> dict:
    if (not isinstance(daten, dict) or not isinstance(daten.get("art"), str)
            or daten.get("art") not in {"seite", "rechteck"}):
        raise ObjektKartenFehler("Ausschnitt: art seite oder rechteck erforderlich")
    if daten["art"] == "seite":
        daten = {"art": "seite", "x0": daten.get("x0", 0), "y0": daten.get("y0", 0),
                 "x1": daten.get("x1", 1), "y1": daten.get("y1", 1)}
    werte = {name: geo._zahl(daten.get(name), name, 0, 1) for name in ("x0", "y0", "x1", "y1")}
    if werte["x1"] - werte["x0"] < MIN_AUSSCHNITT or werte["y1"] - werte["y0"] < MIN_AUSSCHNITT:
        raise ObjektKartenFehler("Ausschnitt zu klein oder Eckreihenfolge ungueltig")
    if daten["art"] == "seite" and tuple(werte.values()) != (0, 0, 1, 1):
        raise ObjektKartenFehler("Ganze Seite verlangt die vollstaendigen Bildgrenzen")
    return {"art": daten["art"], **werte}


def _objekt(db: Session, objekt_id: int) -> Objekt:
    objekt = db.query(Objekt).filter_by(id=objekt_id).first()
    if objekt is None:
        raise ObjektKartenFehler("Objekt nicht gefunden", 404)
    return objekt


def _overlay(db: Session, overlay: ObjektBspOverlay) -> Objekt:
    if db.query(ObjektBspOverlay).filter_by(id=overlay.id, org_id=overlay.org_id).first() is None:
        raise ObjektKartenFehler("Overlay nicht gefunden", 404)
    return _objekt(db, overlay.objekt_id)


def _stand(db: Session, overlay: ObjektBspOverlay) -> tuple[ObjektDokument, ObjektDokumentGeoref]:
    objekt = _objekt(db, overlay.objekt_id)
    dokument = db.query(ObjektDokument).filter_by(
        id=overlay.dokument_id, objekt_id=objekt.id, org_id=objekt.org_id
    ).first()
    g = db.query(ObjektDokumentGeoref).filter_by(
        objekt_id=objekt.id, org_id=objekt.org_id, dokument_id=overlay.dokument_id, seite=overlay.seite
    ).order_by(ObjektDokumentGeoref.revision.desc()).first()
    if (dokument is None or not dokument.ist_aktuelle_version or dokument.freigabe_status != "freigegeben"
            or g is None or geo.georef_pruefen(db, objekt, g, dokument=dokument) != "gueltig"):
        raise ObjektKartenFehler("Aktuelles freigegebenes Dokument mit gueltiger Georeferenz erforderlich")
    return dokument, g


def _pfad(relativ: str | None) -> Path:
    root = Path(settings.OBJEKT_MEDIA_DIR).resolve()
    pfad = (root / (relativ or "")).resolve()
    if not relativ or not pfad.is_relative_to(root):
        raise ObjektKartenFehler("Ungueltiger Medienpfad")
    return pfad


def _daten(overlay: ObjektBspOverlay) -> dict:
    return {c.name: getattr(overlay, c.name) for c in overlay.__table__.columns}


def _audit(db: Session, overlay: ObjektBspOverlay, vorher: dict, user_id: int | None) -> None:
    overlay.aktualisiert_am = _jetzt()
    for feld, wert in _daten(overlay).items():
        if wert != vorher.get(feld):
            write_objekt_change(db, overlay.objekt_id, overlay.org_id, "karte",
                                f"bsp_overlay.{overlay.id}.{feld}", vorher.get(feld), wert, user_id)
    db.flush()


def _eingaben(daten: dict) -> dict:
    erlaubt = {"name", "plan_art", "bereich", "geschoss", "ausschnitt", "standard_sichtbar",
               "standard_deckkraft", "empfohlen", "autodruck", "druck_deckkraft", "quelle_text"}
    if set(daten) - erlaubt:
        raise ObjektKartenFehler("Unbekannte Overlay-Felder")
    neu = dict(daten)
    for name, maximum in (("name", 200), ("bereich", 200), ("geschoss", 100), ("quelle_text", 10000)):
        if name in neu and (not isinstance(neu[name], str) or len(neu[name]) > maximum
                            or (name == "name" and not neu[name].strip())):
            raise ObjektKartenFehler(f"{name}: ungueltiger Text")
    if "plan_art" in neu and (not isinstance(neu["plan_art"], str) or neu["plan_art"] not in PLAN_ARTEN):
        raise ObjektKartenFehler("Unbekannte Planart")
    for name in ("standard_sichtbar", "empfohlen", "autodruck"):
        if name in neu and type(neu[name]) is not bool:
            raise ObjektKartenFehler(f"{name}: Wahrheitswert erforderlich")
    for name in ("standard_deckkraft", "druck_deckkraft"):
        if name in neu:
            neu[name] = geo._zahl(neu[name], name, 0.1, 1)
    if "ausschnitt" in neu:
        neu["ausschnitt_json"] = json.dumps(ausschnitt_validieren(neu.pop("ausschnitt")))
    return neu


def _empfehlung(db: Session, overlay: ObjektBspOverlay, user_id: int | None) -> None:
    if overlay.empfohlen:
        for andere in db.query(ObjektBspOverlay).filter_by(
            org_id=overlay.org_id, objekt_id=overlay.objekt_id, empfohlen=True
        ).all():
            if andere.id != overlay.id:
                vorher = _daten(andere)
                andere.empfohlen = False
                _audit(db, andere, vorher, user_id)


def entwurf_anlegen(db: Session, objekt_id: int, dokument_id: int, seite: int,
                     daten: dict, user_id: int | None = None) -> ObjektBspOverlay:
    objekt = _objekt(db, objekt_id)
    if type(seite) is not int or seite < 1:
        raise ObjektKartenFehler("Positive Seitennummer erforderlich")
    neu = _eingaben(daten)
    if "name" not in neu:
        raise ObjektKartenFehler("Name erforderlich")
    geo._seite(db, objekt, dokument_id, seite)
    ausschnitt = neu.pop("ausschnitt_json", json.dumps(ausschnitt_validieren({"art": "seite"})))
    overlay = ObjektBspOverlay(org_id=objekt.org_id, objekt_id=objekt.id, dokument_id=dokument_id,
                               seite=seite, erstellt_von=user_id,
                               ausschnitt_json=ausschnitt,
                               **neu)
    db.add(overlay)
    db.flush()
    _empfehlung(db, overlay, user_id)
    _audit(db, overlay, {}, user_id)
    return overlay


def entwurf_aendern(db: Session, overlay: ObjektBspOverlay, daten: dict,
                    user_id: int | None = None) -> ObjektBspOverlay:
    _overlay(db, overlay)
    neu = _eingaben(daten)
    vorher = _daten(overlay)
    for name, wert in neu.items():
        setattr(overlay, name, wert)
    overlay.status = "entwurf"
    overlay.raster_pfad = overlay.raster_sha256 = overlay.raster_bounds_json = None
    overlay.raster_breite = overlay.raster_hoehe = None
    _empfehlung(db, overlay, user_id)
    _audit(db, overlay, vorher, user_id)
    return overlay


def _mal(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _raster_rechnung(p: dict, ausschnitt: dict, breite: int, hoehe: int) -> tuple[list, tuple[int, int], list]:
    """Inverse Homographie: Zielpixel -> lokale Meter -> gedrehte Quellpixel."""
    ecken = [geo._abbilden(p, x, y) for x, y in (
        (ausschnitt["x0"], ausschnitt["y0"]), (ausschnitt["x1"], ausschnitt["y0"]),
        (ausschnitt["x1"], ausschnitt["y1"]), (ausschnitt["x0"], ausschnitt["y1"]))]
    west, ost = min(e[0] for e in ecken), max(e[0] for e in ecken)
    sued, nord = min(e[1] for e in ecken), max(e[1] for e in ecken)
    faktor = min(2048 / max(ost - west, nord - sued), max(breite, hoehe) / max(ost - west, nord - sued))
    w = min(2048, max(1, math.ceil((ost - west) * faktor)))
    h = min(2048, max(1, math.ceil((nord - sued) * faktor)))
    a, b, c, d, e, f, g, z = p["matrix"]
    invers: list[list[float]] = [[e - f*z, c*z - b, b*f - c*e], [f*g - d, a - c*g, c*d - a*f],
              [d*z - e*g, b*g - a*z, a*e - b*d]]
    quelle: list[list[float]] = [[breite / p["seitenverhaeltnis"], 0, breite / 2], [0, -hoehe, hoehe / 2], [0, 0, 1]]
    ziel: list[list[float]] = [[(ost - west) / w, 0, west], [0, -(nord - sued) / h, nord], [0, 0, 1]]
    m = _mal(_mal(quelle, invers), ziel)
    if abs(m[2][2]) < 1e-12:
        raise ObjektKartenFehler("Nicht invertierbare Rasterabbildung")
    koeff = [wert / m[2][2] for zeile in m for wert in zeile][:8]
    fgrad = geo.ERDRADIUS_M * math.pi / 180
    cos = math.cos(math.radians(p["lat0"]))
    bounds = [[p["lat0"] + sued / fgrad, p["lng0"] + west / (fgrad*cos)],
              [p["lat0"] + nord / fgrad, p["lng0"] + ost / (fgrad*cos)]]
    return koeff, (w, h), bounds


def raster_erzeugen(db: Session, overlay: ObjektBspOverlay, user_id: int | None = None) -> ObjektBspOverlay:
    objekt = _overlay(db, overlay)
    _, g = _stand(db, overlay)
    ausschnitt = ausschnitt_validieren(json.loads(overlay.ausschnitt_json))
    seite = geo._seite(db, objekt, overlay.dokument_id, overlay.seite)
    try:
        with Image.open(_pfad(seite.bild_pfad)) as original:
            bild = original.convert("RGBA").rotate(-seite.rotation, expand=True)
        p = json.loads(g.parameter_json or "{}")
        koeff, groesse, bounds = _raster_rechnung(p, ausschnitt, *bild.size)
        maske = Image.new("L", bild.size, 0)
        ImageDraw.Draw(maske).rectangle(
            (math.ceil(ausschnitt["x0"] * bild.width), math.ceil(ausschnitt["y0"] * bild.height),
             math.ceil(ausschnitt["x1"] * bild.width) - 1, math.ceil(ausschnitt["y1"] * bild.height) - 1), fill=255)
        bild.putalpha(ImageChops.multiply(bild.getchannel("A"), maske))
        methode = Image.Transform.PERSPECTIVE if g.modell == "homographie" else Image.Transform.AFFINE
        raster = bild.transform(groesse, methode, koeff if methode == Image.Transform.PERSPECTIVE else koeff[:6],
                                resample=Image.Resampling.BICUBIC)
        from io import BytesIO

        ausgabe = BytesIO()
        raster.save(ausgabe, format="PNG")
        inhalt = ausgabe.getvalue()
        sha = hashlib.sha256(inhalt).hexdigest()
        relativ = f"bsp_overlay/{overlay.org_id}/{overlay.id}-{sha}.png"
        pfad = _pfad(relativ)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(inhalt)
    except (OSError, ValueError, KeyError, ZeroDivisionError) as exc:
        raise ObjektKartenFehler("Raster konnte nicht erzeugt werden") from exc
    vorher = _daten(overlay)
    overlay.status = "entwurf"
    overlay.georef_id, overlay.georef_revision, overlay.dokument_hash = g.id, g.revision, g.dokument_hash
    overlay.raster_pfad, overlay.raster_sha256 = relativ, sha
    overlay.raster_breite, overlay.raster_hoehe = groesse
    overlay.raster_bounds_json = json.dumps(bounds)
    _audit(db, overlay, vorher, user_id)
    return overlay


def freigabe_warnungen(db: Session, overlay: ObjektBspOverlay) -> list[str]:
    _overlay(db, overlay)
    _, g = _stand(db, overlay)
    return [h["code"] for h in geo._hinweise(json.loads(g.referenzpunkte_json), g.status, g.max_fehler_m)
            if h["code"] in {"wenige_punkte", "nur_freie_klicks"}]


def freigeben(db: Session, overlay: ObjektBspOverlay, user: User,
              bestaetigte_warnungen: list[str] | None = None) -> ObjektBspOverlay:
    _overlay(db, overlay)
    if (not {r.code for r in user.roles} & OBJEKT_VERWALTER_ROLES
            or (user.org_id != overlay.org_id and not any(r.code == "system_admin" for r in user.roles))):
        raise ObjektKartenFehler("Keine Berechtigung zur Overlay-Freigabe", 403)
    warnungen = freigabe_warnungen(db, overlay)
    offen = set(warnungen) - set(bestaetigte_warnungen or [])
    if offen:
        raise ObjektKartenFehler("Warnungen einzeln bestaetigen: " + ", ".join(sorted(offen)))
    raster_erzeugen(db, overlay, user.id)
    vorher = _daten(overlay)
    overlay.status = "freigegeben"
    overlay.freigabe_nr += 1
    overlay.freigegeben_von, overlay.freigegeben_am = user.id, _jetzt()
    _empfehlung(db, overlay, user.id)
    _audit(db, overlay, vorher, user.id)
    return overlay


def zurueckziehen(db: Session, overlay: ObjektBspOverlay, user_id: int | None = None) -> None:
    _overlay(db, overlay)
    vorher = _daten(overlay)
    overlay.status = "zurueckgezogen"
    _audit(db, overlay, vorher, user_id)


def overlay_ist_auslieferbar(db: Session, overlay: ObjektBspOverlay) -> bool:
    try:
        _overlay(db, overlay)
        if overlay.status != "freigegeben":
            return False
        _, g = _stand(db, overlay)
        if (overlay.georef_id, overlay.georef_revision, overlay.dokument_hash) != (g.id, g.revision, g.dokument_hash):
            return False
        return hashlib.sha256(_pfad(overlay.raster_pfad).read_bytes()).hexdigest() == overlay.raster_sha256
    except (ObjektKartenFehler, OSError, ValueError):
        return False


def overlays_fuer_einsatz(db: Session, incident_id: int, *, einsatzvorgabe: bool = True) -> list[dict]:
    zuordnungen = db.query(ObjektEinsatz).filter_by(incident_id=incident_id, status="bestaetigt").all()
    ergebnis = []
    for zuordnung in zuordnungen:
        for overlay in db.query(ObjektBspOverlay).filter_by(
            objekt_id=zuordnung.objekt_id, org_id=zuordnung.org_id
        ).order_by(ObjektBspOverlay.id).all():
            if not overlay_ist_auslieferbar(db, overlay):
                continue
            einstellung = db.query(BspOverlayEinsatz).filter_by(
                incident_id=incident_id, overlay_id=overlay.id, org_id=overlay.org_id
            ).first()
            sichtbarkeit = einstellung.sichtbarkeit if einstellung and einsatzvorgabe else "INHERIT"
            deckkraft = einstellung.deckkraft if einstellung and einsatzvorgabe else None
            ergebnis.append(_anzeige_daten(overlay, sichtbarkeit, deckkraft))
    return ergebnis


def _anzeige_daten(
    overlay: ObjektBspOverlay, sichtbarkeit: str = "INHERIT", deckkraft: float | None = None,
) -> dict:
    return {
        **_daten(overlay), "bounds": json.loads(overlay.raster_bounds_json or "[]"),
        "sichtbarkeit": sichtbarkeit, "deckkraft": deckkraft,
        "effektiv_sichtbar": sichtbarkeit == "ON" or (sichtbarkeit == "INHERIT" and overlay.standard_sichtbar),
        "effektiv_deckkraft": deckkraft if deckkraft is not None else overlay.standard_deckkraft,
    }


def objekt_overlays_json(db: Session, objekt: Objekt, user: User) -> list[dict]:
    """Auslieferbare Objekt-Ebenen ohne einsatzbezogene Einstellungen."""
    daten = [
        _anzeige_daten(overlay)
        for overlay in db.query(ObjektBspOverlay).filter_by(
            objekt_id=objekt.id, org_id=objekt.org_id
        ).order_by(ObjektBspOverlay.id).all()
        if overlay_ist_auslieferbar(db, overlay)
    ]
    return _overlays_json(db, daten, user)


def einsatz_overlays_json(
    db: Session, incident_id: int, user: User, *, kann_einstellen: bool = False,
    einsatzvorgabe: bool = True,
) -> list[dict]:
    """Gemeinsame, lesende Serialisierung fuer geschuetzte Einsatzkarten."""
    return _overlays_json(
        db, overlays_fuer_einsatz(db, incident_id, einsatzvorgabe=einsatzvorgabe), user,
        kann_einstellen=kann_einstellen,
    )


def _overlays_json(
    db: Session, overlays: list[dict], user: User, *, kann_einstellen: bool = False,
) -> list[dict]:
    liste = []
    for daten in overlays:
        objekt = db.get(Objekt, daten["objekt_id"])
        # Dieselbe Leseschranke wie bei der geschuetzten Rasterauslieferung.
        if not objekt or (objekt.status == "entwurf" and not has_role(user, "objekt_verwalter")):
            continue
        liste.append({
            **{feld: daten[feld] for feld in (
                "id", "name", "objekt_id", "plan_art", "geschoss", "bereich", "freigabe_nr",
                "bounds", "standard_sichtbar", "standard_deckkraft", "sichtbarkeit", "deckkraft",
                "effektiv_sichtbar", "effektiv_deckkraft",
            )},
            "objekt_name": objekt.name,
            "freigegeben_am": daten["freigegeben_am"].strftime("%Y-%m-%dT%H:%M:%SZ")
            if daten["freigegeben_am"] else None,
            "raster_url": f'/objekt-medien/bsp-overlay/{daten["id"]}/raster'
                          f'?v={daten["freigabe_nr"]}-{(daten["raster_sha256"] or "")[:8]}',
            "kann_einstellen": kann_einstellen,
        })
    return liste


def original_ausschnitt_png(db: Session, overlay: ObjektBspOverlay) -> bytes:
    """Unverzerrter Ausschnitt der gedrehten Originalseite für beide Druckwege."""
    _overlay(db, overlay)
    seite = db.query(ObjektDokumentSeite).filter_by(
        org_id=overlay.org_id, objekt_id=overlay.objekt_id,
        dokument_id=overlay.dokument_id, seiten_nr=overlay.seite).one()
    ausschnitt = ausschnitt_validieren(json.loads(overlay.ausschnitt_json))
    with Image.open(_pfad(seite.bild_pfad)) as original:
        bild = original.convert("RGBA").rotate(-seite.rotation, expand=True)
    box = (math.floor(ausschnitt["x0"] * bild.width), math.floor(ausschnitt["y0"] * bild.height),
           math.ceil(ausschnitt["x1"] * bild.width), math.ceil(ausschnitt["y1"] * bild.height))
    ausgabe = BytesIO()
    bild.crop(box).save(ausgabe, format="PNG")
    return ausgabe.getvalue()
