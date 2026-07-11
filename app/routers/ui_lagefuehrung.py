"""Lageführung-Modul (Phase 1 / MVP): einsatzbezogene Lagekarte.

Alle Routen brauchen require_lagefuehrung_enabled (HTTP 404 wenn Modul inaktiv).
Kein fester Prefix — Pfade folgen dem Muster /einsatz/{incident_id}/lagefuehrung/...
wie die übrigen Einsatz-Unterseiten in ui_incident.py.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import can_access_incident, has_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, IncidentVehicle
from app.models.lagefuehrung import (
    LAGEFUEHRUNG_FEATURE_TYPEN,
    LagefuehrungBerechtigung,
    LagefuehrungEvent,
    LagefuehrungFeature,
    LagefuehrungSnapshot,
)
from app.models.user import User
from app.services.broadcast import manager

router = APIRouter(tags=["lagefuehrung"])

# Statusfarbe je unit_status (vereinfachtes FMS-Ampelschema, Muster Konzept F03).
_UNIT_STATUS_COLOR = {
    "Einsatz übernommen": "#f6ad55",  # gelb – anrückend
    "Am Einsatzort": "#e53e3e",       # rot – vor Ort
    "Einsatzbereit": "#48bb78",       # grün – einsatzbereit
}
_DEFAULT_STATUS_COLOR = "#a0aec0"

_EDIT_ROLES = ("incident_leader", "admin", "org_admin", "recorder")


# ── Guard ──────────────────────────────────────────────────────────────────────

def require_lagefuehrung_enabled(request: Request) -> None:
    """Guard-Dependency: HTTP 404 wenn Lageführung-Modul nicht effektiv aktiv (System+Org)."""
    if not getattr(request.state, "lagefuehrung_modul_aktiv", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _incident_or_404(incident_id: int, db: Session) -> Incident:
    inc = db.get(Incident, incident_id)
    if not inc:
        raise HTTPException(404, "Einsatz nicht gefunden")
    return inc


def _current_user_or_401(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Nicht angemeldet")
    return user


def _check_access(user: User, incident: Incident) -> None:
    if not can_access_incident(user, incident):
        raise HTTPException(403, "Kein Zugriff auf diesen Einsatz")


def _has_granted_edit_access(db: Session, incident_id: int, user_id: int) -> bool:
    """Vom Lageführer explizit vergebene Editor-Rechte (Phase 2, ergänzt _EDIT_ROLES)."""
    return (
        db.query(LagefuehrungBerechtigung)
        .filter(
            LagefuehrungBerechtigung.incident_id == incident_id,
            LagefuehrungBerechtigung.user_id == user_id,
        )
        .first()
        is not None
    )


def _check_edit_access(user: User, incident: Incident, db: Session) -> None:
    _check_access(user, incident)
    if has_role(user, *_EDIT_ROLES):
        return
    if _has_granted_edit_access(db, incident.id, user.id):
        return
    raise HTTPException(403, "Keine Berechtigung zum Bearbeiten der Lagekarte")


def _log_event(
    db: Session, incident: Incident, user: User | None,
    event_typ: str, ref_typ: str | None, ref_id: int | None, payload: dict | None,
) -> None:
    db.add(LagefuehrungEvent(
        org_id=incident.primary_org_id,
        incident_id=incident.id,
        user_id=user.id if user else None,
        event_typ=event_typ,
        ref_typ=ref_typ,
        ref_id=ref_id,
        payload=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
    ))


def _feature_dict(f: LagefuehrungFeature) -> dict:
    try:
        geometry = json.loads(f.geometry)
    except (ValueError, TypeError):
        geometry = None
    try:
        props = json.loads(f.props) if f.props else None
    except (ValueError, TypeError):
        props = None
    return {
        "id": f.id,
        "typ": f.typ,
        "zeichen_key": f.zeichen_key,
        "geometry": geometry,
        "rotation": f.rotation,
        "scale": float(f.scale),
        "label": f.label,
        "props": props,
        "layer_gruppe": f.layer_gruppe,
        "version": f.version,
        "created_by": f.created_by,
    }


def _event_dict(e: LagefuehrungEvent) -> dict:
    try:
        payload = json.loads(e.payload) if e.payload else None
    except (ValueError, TypeError):
        payload = None
    return {
        "id": e.id,
        "ts": e.ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_id": e.user_id,
        "event_typ": e.event_typ,
        "ref_typ": e.ref_typ,
        "ref_id": e.ref_id,
        "payload": payload,
    }


def _feature_query(db: Session, incident_id: int):
    return db.query(LagefuehrungFeature).filter(
        LagefuehrungFeature.incident_id == incident_id,
        LagefuehrungFeature.deleted_at.is_(None),
    )


# ── Seite ──────────────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung", response_class=HTMLResponse)
async def lagefuehrung_seite(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    fuehrer_name = None
    if incident.lagefuehrung_fuehrer_user_id:
        fuehrer = db.get(User, incident.lagefuehrung_fuehrer_user_id)
        fuehrer_name = fuehrer.display_name if fuehrer else None

    can_edit = has_role(user, *_EDIT_ROLES) or _has_granted_edit_access(db, incident_id, user.id)
    is_fuehrer = incident.lagefuehrung_fuehrer_user_id == user.id
    granted_user_ids = [
        row.user_id for row in
        db.query(LagefuehrungBerechtigung).filter(LagefuehrungBerechtigung.incident_id == incident_id).all()
    ]

    return templates.TemplateResponse(request, "incident/lagefuehrung.html", {
        "user": user,
        "incident": incident,
        "can_edit": can_edit,
        "is_fuehrer": is_fuehrer,
        "fuehrer_name": fuehrer_name,
        "granted_user_ids": granted_user_ids,
        "objekt_enabled": bool(getattr(request.state, "objekt_enabled", False)),
    })


# ── Druck: WYSIWYG-Kartendruck (Muster incident_major/karte_druck.html) ─────────
# Frisches, eigenständiges Leaflet-Rendering statt statischem staticmap-PNG, damit
# exakt das gedruckt wird, was auf der interaktiven Karte sichtbar ist (aktueller
# Kartenausschnitt + eingeschaltete Layer). Kein Journal/Kräfteübersicht — nur
# Karte, Legende der verwendeten Zeichen und Zeitstempel (Nutzer-Vorgabe).

_LFT_DRUCK_VALID_FMTS = {"A4 portrait", "A4 landscape", "A3 portrait", "A3 landscape"}
_LFT_DRUCK_VALID_LAYERS = {"einsatzort", "fahrzeuge", "objekt", "wasserstellen", "zeichnung", "beschriftung"}


@router.get("/einsatz/{incident_id}/lagefuehrung/karte/druck", response_class=HTMLResponse)
async def lagefuehrung_karte_druck(
    incident_id: int,
    request: Request,
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
    fmt: str = "A4 landscape",
    layers: str = "",
    baselayer: str = "osm",
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    if fmt not in _LFT_DRUCK_VALID_FMTS:
        fmt = "A4 landscape"
    active_layers = [t for t in layers.split(",") if t in _LFT_DRUCK_VALID_LAYERS]
    if not active_layers:
        active_layers = sorted(_LFT_DRUCK_VALID_LAYERS)

    return templates.TemplateResponse(request, "incident/lagefuehrung_druck.html", {
        "incident": incident,
        "min_lat": min_lat, "min_lng": min_lng, "max_lat": max_lat, "max_lng": max_lng,
        "fmt": fmt,
        "active_layers_json": json.dumps(active_layers),
        "api_base": f"/einsatz/{incident_id}/lagefuehrung",
        "baselayer": "ortho" if baselayer == "ortho" else "osm",
    })


# ── Auto-Layer: Fahrzeuge ───────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/vehicles.json")
async def lagefuehrung_vehicles(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    from app.models.major_incident import VehiclePosition

    vehicles = (
        db.query(IncidentVehicle)
        .filter(IncidentVehicle.incident_id == incident_id, IncidentVehicle.removed_at.is_(None))
        .all()
    )

    vehicle_master_ids = [v.vehicle_master_id for v in vehicles]
    latest_position: dict[int, VehiclePosition] = {}
    if vehicle_master_ids and incident.primary_org_id:
        rows = (
            db.query(VehiclePosition)
            .filter(
                VehiclePosition.vehicle_id.in_(vehicle_master_ids),
                VehiclePosition.org_id == incident.primary_org_id,
            )
            .order_by(VehiclePosition.vehicle_id, VehiclePosition.received_at.desc())
            .all()
        )
        for row in rows:
            if row.vehicle_id is not None:
                latest_position.setdefault(row.vehicle_id, row)

    out = []
    for v in vehicles:
        pos = latest_position.get(v.vehicle_master_id)
        out.append({
            "id": v.id,
            "label": v.vehicle_master.display_label if v.vehicle_master else None,
            "kennzeichen": v.vehicle_master.kennzeichen if v.vehicle_master else None,
            "unit_status": v.unit_status,
            "color": _UNIT_STATUS_COLOR.get(v.unit_status, _DEFAULT_STATUS_COLOR),
            "zeichen_key": v.vehicle_master.taktisches_zeichen if v.vehicle_master else None,
            "lat": pos.lat if pos else None,
            "lng": pos.lon if pos else None,
            "position_source": pos.source if pos else None,
            "position_at": pos.received_at.strftime("%Y-%m-%dT%H:%M:%SZ") if pos else None,
        })
    return JSONResponse(out)


@router.post("/einsatz/{incident_id}/lagefuehrung/vehicles/{iv_id}/pin")
async def lagefuehrung_vehicle_pin(
    incident_id: int,
    iv_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    """Manuelle Position für ein Board-Fahrzeug ohne (aktuelle) GPS-Koordinate setzen.

    Muster `vehicle_manual_pin` (app/routers/ui_major_incident.py) — dort für die
    Großschadenslage, hier für den Einzeleinsatz. `VehiclePosition.incident_id`
    bleibt bewusst NULL: die Spalte ist strukturell nur an `major_incident`
    gebunden (siehe Phase-1-Recherche), normale Einsätze korrelieren Positionen
    ausschließlich über vehicle_id + org_id + Aktualität.
    """
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_edit_access(user, incident, db)

    iv = (
        db.query(IncidentVehicle)
        .filter(IncidentVehicle.id == iv_id, IncidentVehicle.incident_id == incident_id)
        .first()
    )
    if not iv:
        raise HTTPException(404, "Fahrzeug nicht gefunden")

    data = await request.json()
    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "lat/lng fehlen")

    from app.models.major_incident import VehiclePosition

    now = datetime.now(UTC)
    db.add(VehiclePosition(
        incident_id=None,
        org_id=incident.primary_org_id,
        vehicle_id=iv.vehicle_master_id,
        resource_label=iv.vehicle_master.display_label if iv.vehicle_master else None,
        lat=lat,
        lon=lng,
        source="manual",
        recorded_at=now,
        received_at=now,
        reported_by=user.id,
    ))
    _log_event(db, incident, user, "vehicle.pinned", "vehicle", iv.id, {"lat": lat, "lng": lng})
    db.commit()

    await manager.broadcast(incident_id, {"type": "lagefuehrung.vehicle.pinned", "iv_id": iv.id})
    return JSONResponse({"ok": True})


# ── Auto-Layer: Objekt ──────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/objekte.json")
async def lagefuehrung_objekte(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    if not getattr(request.state, "objekt_enabled", False):
        return JSONResponse([])

    from app.models.objekt import (
        GEFAHR_PIKTOGRAMME,
        OBJEKT_EINSATZ_BESTAETIGT,
        OBJEKT_SYMBOL_TYPEN,
        Objekt,
        ObjektEinsatz,
        ObjektGefahr,
        parse_karten_geometry,
    )

    verknuepfungen = (
        db.query(ObjektEinsatz)
        .options(
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.gefahren).selectinload(ObjektGefahr.gefahr),
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte),
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.karten_objekte),
            selectinload(ObjektEinsatz.objekt).selectinload(Objekt.bma),
        )
        .filter(
            ObjektEinsatz.incident_id == incident_id,
            ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
        )
        .all()
    )
    out = []
    for ov in verknuepfungen:
        o = ov.objekt
        if not o or o.lat is None or o.lng is None:
            continue
        gefahren = []
        for g in o.gefahren:
            piktogramm, name = "⚠️", (g.gefahr.name if g.gefahr else "Gefahr")
            if g.gefahr:
                roh = GEFAHR_PIKTOGRAMME.get(g.gefahr.piktogramm_typ, "⚠️")
                teile = roh.split(" ", 1)
                piktogramm = teile[0]
            gefahren.append({
                "name": name, "piktogramm": piktogramm,
                "un_nummer": g.un_nummer, "stoffname": g.stoffname,
            })
        kontakte = [{
            "art": k.art, "name": k.name,
            "telefone": k.telefone, "erreichbarkeit": k.erreichbarkeit,
        } for k in o.kontakte]
        # Hinterlegte Geometrien (Zufahrten, Sammelplaetze, ...) aus der
        # Objekt-Lagekarte (objekt_karten_objekt) — Punkte via lat/lng, Linien/
        # Flaechen via geometry_json, s. objekt_karte.js fuer das Analogon.
        kartenobjekte = []
        for k in o.karten_objekte:
            geometry = parse_karten_geometry(k.geometry_json)
            if geometry is None and (k.lat is None or k.lng is None):
                continue
            kartenobjekte.append({
                "id": k.id,
                "typ": k.typ,
                "typ_label": OBJEKT_SYMBOL_TYPEN.get(k.typ, k.typ),
                "lat": k.lat,
                "lng": k.lng,
                "geometry": geometry,
                "label": k.label,
            })
        out.append({
            "objekt_id": o.id,
            "name": o.name,
            "vulgoname": o.vulgoname,
            "adresse": o.adresse_zeile,
            "bma_nummer": o.bma.bma_nummer if o.bma else None,
            "lat": o.lat,
            "lng": o.lng,
            "url": f"/objekte/{o.id}",
            "informationen": o.informationen,
            "anfahrtsweg": o.anfahrtsweg,
            "gefahren": gefahren,
            "kontakte": kontakte,
            "kartenobjekte": kartenobjekte,
        })
    return JSONResponse(out)


# ── Auto-Layer: Wasserstellen ────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/wasserstellen.json")
async def lagefuehrung_wasserstellen(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    if not incident.primary_org_id or incident.lat is None or incident.lng is None:
        return JSONResponse([])

    from app.config import settings
    from app.services.wasserstelle_service import lade_wasserstellen_im_umkreis

    stellen = lade_wasserstellen_im_umkreis(
        db, incident.primary_org_id, incident.lat, incident.lng,
        radius_m=settings.HYDRANT_RADIUS_EINSATZINFO_M,
    )
    return JSONResponse(stellen)


# ── Windrichtung (Phase 3, F-Wind): GeoSphere-Vorbelegung für das Windrichtung-Symbol ──

@router.get("/einsatz/{incident_id}/lagefuehrung/wind.json")
async def lagefuehrung_wind(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    """Aktuelle Windrichtung am Einsatzort (weather_service, Kachelmann/GeoSphere/Open-Meteo
    ohne Konfigurationspflicht, siehe app/services/weather_service.py::get_current).
    Leeres Objekt bei fehlenden Koordinaten oder wenn keine Quelle liefert — der Client
    platziert das Symbol dann mit rotation=0 und lässt es manuell drehen.
    """
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    if incident.lat is None or incident.lng is None:
        return JSONResponse({})

    from app.services import weather_service

    current = await weather_service.get_current(incident.lat, incident.lng, org_id=incident.primary_org_id)
    if not current:
        return JSONResponse({})
    return JSONResponse({
        "wind_direction_deg": current.wind_direction_deg,
        "wind_speed_ms": current.wind_speed_ms,
        "source": current.source,
    })


# ── Chronologie ──────────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/events.json")
async def lagefuehrung_events(
    incident_id: int,
    request: Request,
    limit: int = 50,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    # limit ist standardmäßig 50 (Chronologie-Anzeige), das Replay (Phase 3, lagefuehrung.js
    # enterReplay()) fragt mit einem hohen Limit die volle Historie ab, um daraus den
    # Kartenzustand zu jedem Zeitpunkt zu rekonstruieren.
    limit = max(1, min(limit, 2000))
    events = (
        db.query(LagefuehrungEvent)
        .filter(LagefuehrungEvent.incident_id == incident_id)
        .order_by(LagefuehrungEvent.ts.desc())
        .limit(limit)
        .all()
    )
    return JSONResponse([_event_dict(e) for e in events])


# ── Manuelle Features (Zeichnungen/Marker/Text) ─────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/features.json")
async def lagefuehrung_features_list(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    feats = _feature_query(db, incident_id).order_by(LagefuehrungFeature.created_at).all()
    return JSONResponse([_feature_dict(f) for f in feats])


@router.post("/einsatz/{incident_id}/lagefuehrung/features")
async def lagefuehrung_feature_create(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_edit_access(user, incident, db)

    data = await request.json()
    typ = (data.get("typ") or "zeichnung").strip()
    if typ not in LAGEFUEHRUNG_FEATURE_TYPEN:
        raise HTTPException(400, "Unbekannter Feature-Typ")
    geometry = data.get("geometry")
    if not geometry:
        raise HTTPException(400, "geometry fehlt")

    feature = LagefuehrungFeature(
        org_id=incident.primary_org_id,
        incident_id=incident_id,
        typ=typ,
        zeichen_key=(data.get("zeichen_key") or "").strip() or None,
        geometry=json.dumps(geometry, ensure_ascii=False),
        rotation=int(data.get("rotation") or 0),
        scale=float(data.get("scale") or 1.0),
        label=(data.get("label") or "").strip() or None,
        props=json.dumps(data["props"], ensure_ascii=False) if data.get("props") else None,
        layer_gruppe=(data.get("layer_gruppe") or "zeichnung").strip(),
        created_by=user.id,
    )
    db.add(feature)
    db.flush()
    # Voller Feature-Snapshot im Payload (nicht nur {"typ": typ}) — Grundlage für das
    # Lage-Replay (Phase 3): der Kartenzustand zu einem Zeitpunkt wird ausschließlich aus
    # den Event-Payloads rekonstruiert (lagefuehrung.js::replayStateAt).
    _log_event(db, incident, user, "feature.created", "feature", feature.id, _feature_dict(feature))
    db.commit()

    out = _feature_dict(feature)
    await manager.broadcast(incident_id, {"type": "lagefuehrung.feature.created", "feature": out})
    return JSONResponse(out, status_code=201)


@router.patch("/einsatz/{incident_id}/lagefuehrung/features/{feature_id}")
async def lagefuehrung_feature_update(
    incident_id: int,
    feature_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_edit_access(user, incident, db)

    feature = _feature_query(db, incident_id).filter(LagefuehrungFeature.id == feature_id).first()
    if not feature:
        raise HTTPException(404, "Feature nicht gefunden")

    data = await request.json()
    expected_version = data.get("version")
    if expected_version is not None and int(expected_version) != feature.version:
        raise HTTPException(409, "Feature wurde zwischenzeitlich von einem anderen Nutzer geändert")

    if "geometry" in data and data["geometry"]:
        feature.geometry = json.dumps(data["geometry"], ensure_ascii=False)
    if "label" in data:
        feature.label = (data["label"] or "").strip() or None
    if "rotation" in data:
        feature.rotation = int(data["rotation"])
    if "scale" in data:
        feature.scale = float(data["scale"])
    if "props" in data:
        feature.props = json.dumps(data["props"], ensure_ascii=False) if data["props"] else None
    feature.version += 1
    feature.updated_at = datetime.now(UTC)

    # Voller Post-Update-Snapshot statt nur {"version": ...} — Replay-Fundament (siehe
    # feature.created oben).
    _log_event(db, incident, user, "feature.updated", "feature", feature.id, _feature_dict(feature))
    db.commit()

    out = _feature_dict(feature)
    await manager.broadcast(incident_id, {"type": "lagefuehrung.feature.updated", "feature": out})
    return JSONResponse(out)


@router.delete("/einsatz/{incident_id}/lagefuehrung/features/{feature_id}")
async def lagefuehrung_feature_delete(
    incident_id: int,
    feature_id: int,
    request: Request,
    version: int | None = None,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_edit_access(user, incident, db)

    feature = _feature_query(db, incident_id).filter(LagefuehrungFeature.id == feature_id).first()
    if not feature:
        raise HTTPException(404, "Feature nicht gefunden")
    if version is not None and version != feature.version:
        raise HTTPException(409, "Feature wurde zwischenzeitlich von einem anderen Nutzer geändert")

    # Snapshot VOR dem Löschen einfangen (für Chronologie/Audit) — die eigentliche
    # Replay-Rekonstruktion braucht das payload hier nicht (event_typ="feature.deleted"
    # entfernt die feature_id unabhängig vom Inhalt aus dem rekonstruierten Zustand).
    vorher = _feature_dict(feature)
    feature.deleted_at = datetime.now(UTC)
    _log_event(db, incident, user, "feature.deleted", "feature", feature.id, vorher)
    db.commit()

    await manager.broadcast(incident_id, {"type": "lagefuehrung.feature.deleted", "feature_id": feature.id})
    return Response(status_code=204)


# ── Rollen: Lageführung übernehmen ──────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/lagefuehrung/uebernehmen")
async def lagefuehrung_uebernehmen(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_edit_access(user, incident, db)

    alt_fuehrer_id = incident.lagefuehrung_fuehrer_user_id
    incident.lagefuehrung_fuehrer_user_id = user.id
    _log_event(db, incident, user, "fuehrer.changed", "incident", incident.id, {
        "alt": alt_fuehrer_id, "neu": user.id, "neu_name": user.display_name,
    })
    db.commit()

    await manager.broadcast(incident_id, {
        "type": "lagefuehrung.fuehrer_changed",
        "user_id": user.id,
        "name": user.display_name,
    })
    return RedirectResponse(f"/einsatz/{incident_id}/lagefuehrung", status_code=303)


# ── Rechtevergabe durch den Lageführer ──────────────────────────────────────────

def _require_fuehrer(user: User, incident: Incident) -> None:
    if incident.lagefuehrung_fuehrer_user_id != user.id:
        raise HTTPException(403, "Nur der aktuelle Lageführer kann Editor-Rechte vergeben")


@router.post("/einsatz/{incident_id}/lagefuehrung/berechtigung/{target_user_id}")
async def lagefuehrung_berechtigung_erteilen(
    incident_id: int,
    target_user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)
    _require_fuehrer(user, incident)

    target = db.get(User, target_user_id)
    if not target:
        raise HTTPException(404, "Nutzer nicht gefunden")

    if not _has_granted_edit_access(db, incident_id, target_user_id):
        db.add(LagefuehrungBerechtigung(
            org_id=incident.primary_org_id,
            incident_id=incident_id,
            user_id=target_user_id,
            granted_by_user_id=user.id,
        ))
        _log_event(db, incident, user, "berechtigung.erteilt", "user", target_user_id, {
            "name": target.display_name,
        })
        db.commit()

    await manager.broadcast(incident_id, {
        "type": "lagefuehrung.berechtigung.changed",
        "user_id": target_user_id, "granted": True,
    })
    return JSONResponse({"user_id": target_user_id, "granted": True})


@router.delete("/einsatz/{incident_id}/lagefuehrung/berechtigung/{target_user_id}")
async def lagefuehrung_berechtigung_entziehen(
    incident_id: int,
    target_user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)
    _require_fuehrer(user, incident)

    eintrag = (
        db.query(LagefuehrungBerechtigung)
        .filter(
            LagefuehrungBerechtigung.incident_id == incident_id,
            LagefuehrungBerechtigung.user_id == target_user_id,
        )
        .first()
    )
    if eintrag:
        db.delete(eintrag)
        _log_event(db, incident, user, "berechtigung.entzogen", "user", target_user_id, None)
        db.commit()

    await manager.broadcast(incident_id, {
        "type": "lagefuehrung.berechtigung.changed",
        "user_id": target_user_id, "granted": False,
    })
    return Response(status_code=204)


# ── Momentaufnahme ("Lage einfrieren", Phase 3, F-Snapshot) ─────────────────────

@router.post("/einsatz/{incident_id}/lagefuehrung/momentaufnahme")
async def lagefuehrung_momentaufnahme_erstellen(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_edit_access(user, incident, db)

    if incident.lat is None or incident.lng is None:
        raise HTTPException(400, "Einsatz ohne Koordinaten — keine Momentaufnahme möglich")
    if not incident.primary_org_id:
        raise HTTPException(400, "Einsatz ohne Organisation — keine Momentaufnahme möglich")
    org_id: int = incident.primary_org_id

    try:
        data = await request.json()
    except Exception:
        data = {}
    label = (data.get("label") or "").strip() or None

    from app.services.lagefuehrung_pdf_service import gather_map_render_context, render_lagefuehrung_map_png
    from app.services.lagefuehrung_snapshot_service import save_snapshot_png
    from app.services.storage_service import reserve_storage

    # Kartenrendern (staticmap/requests) ist blockierendes Netzwerk-I/O — wie beim
    # PDF-Lagebericht in einem Thread ausführen, damit der Event-Loop nicht blockiert.
    def _render() -> bytes | None:
        ctx = gather_map_render_context(incident, db)
        return render_lagefuehrung_map_png(
            incident, ctx["features"], ctx["vehicles_positions"], ctx["objekt_marker"],
        )

    png_bytes = await asyncio.to_thread(_render)
    if not png_bytes:
        raise HTTPException(400, "Momentaufnahme konnte nicht gerendert werden")

    stored_filename = save_snapshot_png(org_id, incident_id, png_bytes)
    reserve_storage(db, org_id, len(png_bytes))

    snapshot = LagefuehrungSnapshot(
        org_id=org_id,
        incident_id=incident_id,
        stored_filename=stored_filename,
        bytes=len(png_bytes),
        label=label,
        created_by=user.id,
    )
    db.add(snapshot)
    db.flush()
    _log_event(db, incident, user, "snapshot.erstellt", "snapshot", snapshot.id, {"label": label})
    db.commit()

    await manager.broadcast(incident_id, {"type": "lagefuehrung.chronologie_changed"})
    return JSONResponse({
        "id": snapshot.id,
        "label": snapshot.label,
        "created_at": snapshot.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, status_code=201)


@router.get("/einsatz/{incident_id}/lagefuehrung/momentaufnahme/{snapshot_id}/bild")
async def lagefuehrung_momentaufnahme_bild(
    incident_id: int,
    snapshot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    snapshot = (
        db.query(LagefuehrungSnapshot)
        .filter(LagefuehrungSnapshot.id == snapshot_id, LagefuehrungSnapshot.incident_id == incident_id)
        .first()
    )
    if not snapshot:
        raise HTTPException(404, "Momentaufnahme nicht gefunden")

    from app.services.lagefuehrung_snapshot_service import snapshot_path

    path = snapshot_path(snapshot)
    if not path.exists():
        raise HTTPException(404, "Bilddatei nicht gefunden")
    return FileResponse(path, media_type="image/png")


# ── PDF-Lagebericht ──────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/pdf")
async def lagefuehrung_pdf(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    from app.services.lagefuehrung_pdf_service import render_lagefuehrung_pdf

    # render_lagefuehrung_pdf rendert die Kartenkachel synchron via staticmap/requests —
    # in einem Thread ausfuehren, damit der Event-Loop nicht blockiert (Muster
    # staticmap_service.py-Docstring).
    pdf_bytes = await asyncio.to_thread(render_lagefuehrung_pdf, incident, db, str(request.base_url))
    filename = f"lagebericht_einsatz_{incident.id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
