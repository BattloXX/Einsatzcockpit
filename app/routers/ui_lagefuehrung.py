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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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

    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz

    verknuepfungen = (
        db.query(ObjektEinsatz)
        .options(selectinload(ObjektEinsatz.objekt))
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
        out.append({
            "objekt_id": o.id,
            "name": o.name,
            "vulgoname": o.vulgoname,
            "lat": o.lat,
            "lng": o.lng,
            "url": f"/objekte/{o.id}",
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


# ── Chronologie ──────────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/lagefuehrung/events.json")
async def lagefuehrung_events(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_lagefuehrung_enabled),
):
    user = _current_user_or_401(request)
    incident = _incident_or_404(incident_id, db)
    _check_access(user, incident)

    events = (
        db.query(LagefuehrungEvent)
        .filter(LagefuehrungEvent.incident_id == incident_id)
        .order_by(LagefuehrungEvent.ts.desc())
        .limit(50)
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
    _log_event(db, incident, user, "feature.created", "feature", feature.id, {"typ": typ})
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

    _log_event(db, incident, user, "feature.updated", "feature", feature.id, {"version": feature.version})
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

    feature.deleted_at = datetime.now(UTC)
    _log_event(db, incident, user, "feature.deleted", "feature", feature.id, None)
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
