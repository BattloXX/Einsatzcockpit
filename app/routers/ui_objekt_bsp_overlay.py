"""BSP-Overlay-Verwaltung am Objekt, geschuetzte Raster und Editor-Daten."""
from __future__ import annotations

import hashlib
import json
import math
from io import BytesIO

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from PIL import Image
from sqlalchemy.orm import Session

from app.core.permissions import EINSATZ_LESE_ROLLEN, OBJEKT_VERWALTER_ROLES, require_role
from app.db import get_db
from app.models.objekt import ObjektBspOverlay, ObjektDokument, ObjektDokumentGeoref, ObjektDokumentSeite
from app.models.user import User
from app.routers.ui_objekt import _objekt_or_404, require_objekt_enabled
from app.services import objekt_bsp_overlay_service as bsp
from app.services import objekt_georef_service as geo
from app.services.objekt_karte_service import ObjektKartenFehler
from app.services.objekt_service import write_objekt_change

router = APIRouter(tags=["objekt-bsp-overlay"], dependencies=[Depends(require_objekt_enabled)])


def _overlay(db: Session, overlay_id: int, user: User, objekt_id: int | None = None) -> ObjektBspOverlay:
    overlay = db.query(ObjektBspOverlay).filter_by(id=overlay_id).first()
    if (overlay is None or (not user.is_system_admin and overlay.org_id != user.org_id)
            or (objekt_id is not None and overlay.objekt_id != objekt_id)):
        raise HTTPException(404, "Overlay nicht gefunden")
    _objekt_or_404(db, overlay.objekt_id, user)
    return overlay


def _seite(db: Session, objekt_id: int, seite_id: int, org_id: int | None) -> ObjektDokumentSeite:
    seite = db.query(ObjektDokumentSeite).filter_by(
        id=seite_id, objekt_id=objekt_id, org_id=org_id).first()
    if seite is None:
        raise HTTPException(404, "Seite nicht gefunden")
    return seite


def _drehung_pruefen(daten: dict, seite: ObjektDokumentSeite) -> dict:
    daten = dict(daten)
    if "expected_rotation" in daten:
        rotation = daten.pop("expected_rotation")
        if type(rotation) is not int or rotation not in {0, 90, 180, 270}:
            raise HTTPException(422, "Ungültige erwartete Drehung")
        if rotation != seite.rotation:
            raise HTTPException(409, "Dokumentseite wurde inzwischen gedreht; Editor neu öffnen")
    return daten


def _daten(db: Session, overlay: ObjektBspOverlay) -> dict:
    auslieferbar = bsp.overlay_ist_auslieferbar(db, overlay)
    status = "veraltet" if overlay.status == "freigegeben" and not auslieferbar else overlay.status
    dokument = db.query(ObjektDokument).filter_by(
        id=overlay.dokument_id, org_id=overlay.org_id, objekt_id=overlay.objekt_id).one()
    g = db.query(ObjektDokumentGeoref).filter_by(
        org_id=overlay.org_id, objekt_id=overlay.objekt_id, dokument_id=overlay.dokument_id,
        seite=overlay.seite).order_by(ObjektDokumentGeoref.revision.desc()).first()
    warnungen: list[str] = []
    gueltig = False
    try:
        _, g = bsp._stand(db, overlay)
        warnungen = bsp.freigabe_warnungen(db, overlay)
        gueltig = True
    except ObjektKartenFehler:
        pass  # Ungueltige Georef wird als gesperrter Bereich angezeigt.
    return {
        **{feld: getattr(overlay, feld) for feld in (
            "id", "name", "plan_art", "bereich", "geschoss", "standard_sichtbar", "standard_deckkraft",
            "empfohlen", "autodruck", "druck_deckkraft", "freigabe_nr", "georef_revision")},
        "status": status, "hinweis": "Veraltet, bitte neu prüfen und rastern." if status == "veraltet" else "",
        "dokument_version": dokument.versionsnummer,
        "ausschnitt": json.loads(overlay.ausschnitt_json),
        "bounds": json.loads(overlay.raster_bounds_json or "null"),
        "raster_url": f"/objekt-medien/bsp-overlay/{overlay.id}/raster?vorschau=1&v={overlay.raster_sha256}"
        if overlay.raster_pfad else None,
        "georef_gueltig": gueltig, "warnungen": warnungen,
        "qualitaet": {"rmse_m": g.rmse_m, "punktzahl": len(json.loads(g.referenzpunkte_json)),
                      "modell": g.modell, "status": "gueltig" if gueltig else "ungueltig",
                      "revision": g.revision} if g else None,
    }


@router.get("/objekte/{objekt_id}/bsp-overlays")
def overlays_liste(objekt_id: int, seite_id: int | None = None, db: Session = Depends(get_db),
                   user: User = Depends(require_role(*EINSATZ_LESE_ROLLEN))):
    objekt = _objekt_or_404(db, objekt_id, user)
    query = db.query(ObjektBspOverlay).filter_by(objekt_id=objekt.id, org_id=objekt.org_id)
    gueltig = False
    if seite_id is not None:
        seite = _seite(db, objekt.id, seite_id, objekt.org_id)
        query = query.filter_by(dokument_id=seite.dokument_id, seite=seite.seiten_nr)
        g = db.query(ObjektDokumentGeoref).filter_by(
            objekt_id=objekt.id, org_id=objekt.org_id, dokument_id=seite.dokument_id,
            seite=seite.seiten_nr).order_by(ObjektDokumentGeoref.revision.desc()).first()
        gueltig = bool(g and geo.georef_pruefen(db, objekt, g) == "gueltig"
                       and seite.dokument.ist_aktuelle_version
                       and seite.dokument.freigabe_status == "freigegeben")
    return {"overlays": [_daten(db, o) for o in query.order_by(ObjektBspOverlay.id).all()],
            "georef_gueltig": gueltig}


@router.post("/objekte/{objekt_id}/bsp-overlays")
def overlay_anlegen(objekt_id: int, daten: dict = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(require_role(*OBJEKT_VERWALTER_ROLES))):
    objekt = _objekt_or_404(db, objekt_id, user)
    seite_id = daten.get("seite_id")
    if type(seite_id) is not int:
        raise HTTPException(422, "Seiten-ID erforderlich")
    seite = _seite(db, objekt.id, seite_id, objekt.org_id)
    daten = _drehung_pruefen(daten, seite)
    try:
        overlay = bsp.entwurf_anlegen(db, objekt.id, seite.dokument_id, seite.seiten_nr,
                                      {k: v for k, v in daten.items() if k != "seite_id"}, user.id)
        db.commit()
        return _daten(db, overlay)
    except ObjektKartenFehler as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/objekte/{objekt_id}/bsp-overlays/{overlay_id}/{aktion}")
def overlay_aktion(objekt_id: int, overlay_id: int, aktion: str, daten: dict = Body(...),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role(*OBJEKT_VERWALTER_ROLES))):
    overlay = _overlay(db, overlay_id, user, objekt_id)
    try:
        if aktion == "aendern":
            seite = geo._seite(db, _objekt_or_404(db, objekt_id, user), overlay.dokument_id, overlay.seite)
            daten = _drehung_pruefen(daten, seite)
            bsp.entwurf_aendern(db, overlay, daten, user.id)
        elif aktion == "rastern":
            bsp.raster_erzeugen(db, overlay, user.id)
        elif aktion == "freigeben":
            warnungen = daten.get("bestaetigte_warnungen", [])
            if not isinstance(warnungen, list) or any(not isinstance(w, str) for w in warnungen):
                raise HTTPException(422, "Warnungscodes als Liste erforderlich")
            bsp.freigeben(db, overlay, user, warnungen)
        elif aktion == "zurueckziehen":
            bsp.zurueckziehen(db, overlay, user.id)
        elif aktion == "loeschen":
            if overlay.status != "entwurf":
                raise HTTPException(409, "Nur Entwürfe können gelöscht werden")
            write_objekt_change(db, overlay.objekt_id, overlay.org_id, "karte",
                                f"bsp_overlay.{overlay.id}.geloescht", overlay.name, None, user.id)
            db.delete(overlay)
            db.commit()
            return {"geloescht": True}
        else:
            raise HTTPException(404, "Unbekannte Overlay-Aktion")
        db.commit()
        return _daten(db, overlay)
    except ObjektKartenFehler as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/objekt-medien/bsp-overlay/{overlay_id}/raster")
def overlay_raster(overlay_id: int, request: Request, vorschau: bool = False,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role(*EINSATZ_LESE_ROLLEN))):
    overlay = _overlay(db, overlay_id, user)
    if vorschau and not {r.code for r in user.roles} & OBJEKT_VERWALTER_ROLES:
        raise HTTPException(403, "Vorschau nur für Objektverwalter")
    if not vorschau and not bsp.overlay_ist_auslieferbar(db, overlay):
        raise HTTPException(404, "Raster nicht verfügbar")
    try:
        inhalt = bsp._pfad(overlay.raster_pfad).read_bytes()
    except (ObjektKartenFehler, OSError) as exc:
        raise HTTPException(404, "Raster nicht verfügbar") from exc
    if hashlib.sha256(inhalt).hexdigest() != overlay.raster_sha256:
        raise HTTPException(404, "Raster nicht verfügbar")
    etag = f'"{overlay.raster_sha256}"'
    headers = {"Cache-Control": "private, max-age=0, must-revalidate", "ETag": etag}
    kandidaten = [wert.strip().removeprefix("W/") for wert in request.headers.get("if-none-match", "").split(",")]
    if etag in kandidaten or "*" in kandidaten:
        return Response(status_code=304, headers=headers)
    return Response(inhalt, media_type="image/png", headers=headers)


@router.get("/objekt-medien/bsp-overlay/{overlay_id}/ausschnitt")
def overlay_ausschnitt(overlay_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_role(*EINSATZ_LESE_ROLLEN))):
    """Unverzerrter Ausschnitt der gedrehten Originalseite, nur im freigegebenen Stand."""
    overlay = _overlay(db, overlay_id, user)
    if not bsp.overlay_ist_auslieferbar(db, overlay):
        raise HTTPException(404, "Ausschnitt nicht verfügbar")
    try:
        seite = db.query(ObjektDokumentSeite).filter_by(
            org_id=overlay.org_id, objekt_id=overlay.objekt_id,
            dokument_id=overlay.dokument_id, seiten_nr=overlay.seite).one()
        ausschnitt = bsp.ausschnitt_validieren(json.loads(overlay.ausschnitt_json))
        with Image.open(bsp._pfad(seite.bild_pfad)) as original:
            bild = original.convert("RGBA").rotate(-seite.rotation, expand=True)
        box = (math.floor(ausschnitt["x0"] * bild.width), math.floor(ausschnitt["y0"] * bild.height),
               math.ceil(ausschnitt["x1"] * bild.width), math.ceil(ausschnitt["y1"] * bild.height))
        ausgabe = BytesIO()
        bild.crop(box).save(ausgabe, format="PNG")
        inhalt = ausgabe.getvalue()
    except (ObjektKartenFehler, OSError, ValueError) as exc:
        raise HTTPException(404, "Ausschnitt nicht verfügbar") from exc
    etag = '"' + hashlib.sha256(inhalt).hexdigest() + '"'
    headers = {"Cache-Control": "private, max-age=0, must-revalidate", "ETag": etag}
    kandidaten = [wert.strip().removeprefix("W/") for wert in request.headers.get("if-none-match", "").split(",")]
    if etag in kandidaten or "*" in kandidaten:
        return Response(status_code=304, headers=headers)
    return Response(inhalt, media_type="image/png", headers=headers)
