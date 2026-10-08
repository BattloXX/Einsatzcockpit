"""Serverseitige Verwaltung der organisationsweiten Strassensperren."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import STRASSENSPERREN_LESE_ROLLEN, has_role, require_role
from app.core.templating import templates
from app.core.timezones import local_date_to_utc, local_input_to_utc, org_tz
from app.db import get_db
from app.models.master import FireDept
from app.models.road_closure import (
    CLOSURE_STATUS,
    DIRECTIONS,
    GEOMETRY_QUALITY,
    PRIORITIES,
    RESTRICTION_TYPES,
    RoadClosureChange,
)
from app.models.user import User
from app.services import road_closure_service
from app.services.road_closure_section_service import resolve_section

router = APIRouter(prefix="/strassensperren", tags=["strassensperren"])
_LESE_ROLLEN = STRASSENSPERREN_LESE_ROLLEN


def require_strassensperren_enabled(request: Request) -> None:
    if not getattr(request.state, "strassensperren_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _org(user: User):
    if user.org_id is None or user.org is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewaehlt")
    return user.org


def _closure_or_404(db: Session, user: User, closure_id: int, *, writable: bool):
    try:
        return road_closure_service.get_closure_for_org(db, _org(user).id, closure_id, writable=writable)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Nicht gefunden") from exc


def _filters(status: str, zeitraum: str, scope: str, q: str, restriction_type: str, org):
    if status not in {"current", "active", "planned", "all"}:
        status = "current"
    if scope not in {"all", "own", "shared"}:
        scope = "all"
    if zeitraum not in {"", "heute", "7tage"}:
        zeitraum = ""
    von = bis = None
    if zeitraum:
        # local_date_to_utc deliberately receives org: DB uses naive UTC.
        local_now = datetime.now(UTC).astimezone(org_tz(org))
        start = local_now.date()
        end = start + timedelta(days=6 if zeitraum == "7tage" else 0)
        von, bis = local_date_to_utc(start.isoformat(), org=org), local_date_to_utc(end.isoformat(), end=True, org=org)
    return (
        (None if status == "all" else status),
        von,
        bis,
        scope,
        q.strip(),
        restriction_type if restriction_type in RESTRICTION_TYPES else "",
    )


def _closures(
    db: Session, user: User, status: str, zeitraum: str, scope: str, q: str, restriction_type: str, geometrie: str
):
    selected_status, von, bis, selected_scope, text, selected_type = _filters(
        status, zeitraum, scope, q, restriction_type, _org(user)
    )
    items = road_closure_service.list_closures(
        db,
        _org(user).id,
        status=selected_status,
        von=von,
        bis=bis,
        text=text,
        restriction_type=selected_type or None,
        scope=selected_scope,
        geometrie=geometrie if geometrie in {"", "pruefen", "ok", "fehlt"} else "",
    )
    return items, {
        "status": status if status in {"current", "active", "planned", "all"} else "current",
        "zeitraum": zeitraum,
        "scope": selected_scope,
        "q": text,
        "restriction_type": selected_type,
        "geometrie": geometrie if geometrie in {"", "pruefen", "ok", "fehlt"} else "",
    }


def _org_names(db: Session, closures) -> dict[int, str]:
    ids = {closure.org_id for closure in closures}
    return {row.id: row.name for row in db.query(FireDept).filter(FireDept.id.in_(ids)).all()}


def _color(closure) -> str:
    status = road_closure_service.compute_status(closure)
    if status in {"planned", "expired", "cancelled"}:
        return "grey"
    if closure.restriction_type == "closed":
        return "red"
    if closure.restriction_type in {"partial", "one_way", "weight_limit", "height_limit", "width_limit"}:
        return "orange"
    return "yellow"


def _feature(closure, *, viewer_org_id: int | None = None, public: bool = False) -> dict | None:
    """Build the map representation shared by the collection and detail endpoints."""
    if not closure.geometry_geojson:
        return None
    try:
        geometry = json.loads(closure.geometry_geojson)
    except json.JSONDecodeError:
        return None

    def iso(value):
        return value.isoformat() + "Z" if value else None

    state = road_closure_service.compute_status(closure)
    feature = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id": closure.id,
            "title": closure.title,
            "street": closure.street,
            "from_text": closure.from_text,
            "to_text": closure.to_text,
            "restriction_type": closure.restriction_type,
            "restriction_label": closure.restriction_label,
            "status": state,
            "status_label": CLOSURE_STATUS[state],
            "color": _color(closure),
            "valid_from": iso(closure.valid_from),
            "valid_until": iso(closure.valid_until),
            "description": closure.description,
            "source": closure.source,
            "geometry_status": closure.geometry_status,
            "own": closure.org_id == viewer_org_id,
        },
    }
    if not public:
        feature["properties"]["url"] = f"/strassensperren/{closure.id}"
    return feature


def _status_data(db: Session, org_id: int):
    """Aktuelle Sperren und Kennzahlen fuer die Vollbild-Statusansicht."""
    closures = road_closure_service.list_closures(db, org_id, status="current")
    closures.sort(key=lambda closure: (road_closure_service.compute_status(closure) != "active", closure.valid_from))
    active = sum(road_closure_service.compute_status(closure) == "active" for closure in closures)
    planned = sum(road_closure_service.compute_status(closure) == "planned" for closure in closures)
    return closures, active, planned


def _form_data(**values):
    return {key: (value.strip() if isinstance(value, str) else value) for key, value in values.items()}


def _save_data(org, values: dict):
    def text(name):
        return values.get(name) or None

    def number(name):
        raw = (values.get(name) or "").strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"Ungültiger Wert für {name}.")

    valid_from = local_input_to_utc(values.get("valid_from", ""), org)
    if valid_from is None:
        raise ValueError("Beginn ist erforderlich.")
    valid_until_raw = values.get("valid_until", "")
    valid_until = local_input_to_utc(valid_until_raw, org) if valid_until_raw else None
    if valid_until_raw and valid_until is None:
        raise ValueError("Ungültiges Ende.")
    geometry = values.get("geometry_geojson") or None
    quality = values.get("geometry_quality") if values.get("geometry_quality") in GEOMETRY_QUALITY else None
    meta = values.get("geometry_meta_json") or None
    if meta:
        try:
            if len(meta) >= 20000:
                raise ValueError
            json.loads(meta)
        except (ValueError, json.JSONDecodeError):
            meta = None
    return {
        "title": values.get("title", ""),
        "description": text("description"),
        "city": text("city"),
        "reference_number": text("reference_number"),
        "exceptions": text("exceptions"),
        "authority": text("authority"),
        "street": text("street"),
        "from_text": text("from_text"),
        "to_text": text("to_text"),
        "direction": text("direction"),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "restriction_type": values.get("restriction_type", ""),
        "priority": values.get("priority", "normal"),
        "max_weight_t": number("max_weight_t"),
        "max_height_m": number("max_height_m"),
        "max_width_m": number("max_width_m"),
        "max_length_m": number("max_length_m"),
        "geometry_geojson": geometry,
        "geometry_status": "ok"
        if geometry and values.get("geometry_checked")
        else ("needs_review" if geometry else "missing"),
        "geometry_quality": quality,
        "geometry_meta_json": meta,
        "source": text("source"),
        "source_url": text("source_url"),
    }


def _edit_page(request, db, user, closure=None, form_data=None, error=None, status_code=200):
    shared_ids = (
        form_data.get("freigabe_org_ids", [])
        if form_data is not None
        else (road_closure_service.shared_org_ids(db, closure) if closure is not None else [])
    )
    return templates.TemplateResponse(
        request,
        "road_closure/edit.html",
        {
            "user": user,
            "closure": closure,
            "form_data": form_data or {},
            "error": error,
            "restriction_types": RESTRICTION_TYPES,
            "directions": DIRECTIONS,
            "priorities": PRIORITIES,
            "partner_orgs": road_closure_service.partner_orgs_for(db, _org(user).id),
            "shared_org_ids": {int(org_id) for org_id in shared_ids},
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
    status: str = "current",
    zeitraum: str = "",
    scope: str = "all",
    q: str = "",
    restriction_type: str = "",
    geometrie: str = "",
):
    items, filters = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie)
    active = len(road_closure_service.list_closures(db, _org(user).id, status="active"))
    planned = len(road_closure_service.list_closures(db, _org(user).id, status="planned"))
    ungeprueft = len(
        road_closure_service.list_closures(
            db, _org(user).id, status="current", scope="own", geometrie="pruefen"
        )
    )
    return templates.TemplateResponse(
        request,
        "road_closure/index.html",
        {
            "user": user,
            "closures": items,
            "filters": filters,
            "restriction_types": RESTRICTION_TYPES,
            "active_count": active,
            "planned_count": planned,
            "ungeprueft_count": ungeprueft,
            "status_labels": CLOSURE_STATUS,
            "color": _color,
            "closure_status": road_closure_service.compute_status,
            "can_edit": has_role(user, "objekt_verwalter"),
            "org_names": _org_names(db, items),
        },
    )


@router.get("/liste", response_class=HTMLResponse)
def liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
    status: str = "current",
    zeitraum: str = "",
    scope: str = "all",
    q: str = "",
    restriction_type: str = "",
    geometrie: str = "",
):
    items, filters = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie)
    return templates.TemplateResponse(
        request,
        "road_closure/_liste.html",
        {
            "user": user,
            "closures": items,
            "filters": filters,
            "status_labels": CLOSURE_STATUS,
            "color": _color,
            "closure_status": road_closure_service.compute_status,
            "org_names": _org_names(db, items),
        },
    )


@router.get("/karte.json")
def karte(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
    status: str = "current",
    zeitraum: str = "",
    scope: str = "all",
    q: str = "",
    restriction_type: str = "",
    geometrie: str = "",
):
    items, _ = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie)
    features = [
        feature for item in items if (feature := _feature(item, viewer_org_id=user.org_id)) is not None
    ]
    return JSONResponse({"type": "FeatureCollection", "features": features})


@router.get("/status", response_class=HTMLResponse)
def statusansicht(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closures, active, planned = _status_data(db, _org(user).id)
    return templates.TemplateResponse(
        request,
        "road_closure/status.html",
        {
            "user": user,
            "org": _org(user),
            "closures": closures,
            "active_count": active,
            "planned_count": planned,
            "color": _color,
            "closure_status": road_closure_service.compute_status,
            "public": False,
            "geojson_url": "/strassensperren/karte.json?status=current",
        },
    )


@router.post("/abschnitt")
async def abschnitt_aus_adresse(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    """Ermittelt eine pruefpflichtige Geometrie fuer einen Adressabschnitt."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail={"fehler": "Ungültige Anfrage."}) from None
    try:
        result = await resolve_section(
            db, _org(user), str(data.get("street") or "").strip(),
            str(data.get("from_text") or "").strip() or None, str(data.get("to_text") or "").strip() or None,
            str(data.get("city") or "").strip() or _org(user).city,
        )
    except ValueError as exc:
        return JSONResponse({"fehler": str(exc)}, status_code=422)
    if result.geometry is None:
        return JSONResponse({"fehler": "; ".join(result.hinweise)}, status_code=422)
    return JSONResponse(result.to_dict())


@router.get("/neu", response_class=HTMLResponse)
def neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    return _edit_page(request, db, user)


@router.post("/neu")
def neu_speichern(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    city: str = Form(""),
    reference_number: str = Form(""),
    exceptions: str = Form(""),
    authority: str = Form(""),
    street: str = Form(""),
    from_text: str = Form(""),
    to_text: str = Form(""),
    direction: str = Form(""),
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    restriction_type: str = Form(""),
    priority: str = Form("normal"),
    max_weight_t: str = Form(""),
    max_height_m: str = Form(""),
    max_width_m: str = Form(""),
    max_length_m: str = Form(""),
    source: str = Form(""),
    source_url: str = Form(""),
    geometry_geojson: str = Form(""),
    geometry_quality: str = Form(""),
    geometry_meta_json: str = Form(""),
    geometry_checked: str | None = Form(None),
    freigabe_org_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    values = _form_data(**locals())
    try:
        closure = road_closure_service.create_closure(db, _org(user).id, user.id, _save_data(_org(user), values))
        road_closure_service.set_shares(db, closure, freigabe_org_ids, user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _edit_page(request, db, user, form_data=values, error=str(exc), status_code=422)
    return RedirectResponse(f"/strassensperren/{closure.id}", status_code=303)


@router.get("/{closure_id}/geometrie.json")
def geometrie(
    closure_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=False)
    feature = _feature(closure, viewer_org_id=user.org_id)
    if feature is None:
        raise HTTPException(status_code=404, detail="Keine Geometrie vorhanden")
    return JSONResponse(feature)


@router.get("/{closure_id}", response_class=HTMLResponse)
def detail(
    closure_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=False)
    owner_org = db.get(FireDept, closure.org_id) if closure.org_id != user.org_id else None
    shared_org_names = []
    if closure.org_id == user.org_id:
        shared_org_names = [
            org.name
            for org in road_closure_service.partner_orgs_for(db, closure.org_id)
            if org.id in road_closure_service.shared_org_ids(db, closure)
        ]
    changes = (
        db.query(RoadClosureChange)
        .filter(RoadClosureChange.road_closure_id == closure.id)
        .order_by(RoadClosureChange.created_at.desc())
        .all()
        if closure.org_id == user.org_id
        else []
    )
    return templates.TemplateResponse(
        request,
        "road_closure/detail.html",
        {
            "user": user,
            "closure": closure,
            "changes": changes,
            "status": road_closure_service.compute_status(closure),
            "status_labels": CLOSURE_STATUS,
            "color": _color(closure),
            "directions": DIRECTIONS,
            "geometry_quality_labels": GEOMETRY_QUALITY,
            "can_edit": has_role(user, "objekt_verwalter"),
            "can_delete": has_role(user, "org_admin"),
            "owner_org_name": owner_org.name if owner_org is not None else None,
            "shared_org_names": shared_org_names,
        },
    )


@router.post("/{closure_id}/geometrie-bestaetigen")
def geometrie_bestaetigen(
    closure_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    try:
        road_closure_service.confirm_geometry(db, closure, user.id, source="ui")
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f"/strassensperren/{closure.id}", status_code=303)


@router.get("/{closure_id}/bearbeiten", response_class=HTMLResponse)
def bearbeiten(
    closure_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    return _edit_page(request, db, user, _closure_or_404(db, user, closure_id, writable=True))


@router.post("/{closure_id}/bearbeiten")
def bearbeiten_speichern(
    closure_id: int,
    request: Request,
    version: int = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    city: str = Form(""),
    reference_number: str = Form(""),
    exceptions: str = Form(""),
    authority: str = Form(""),
    street: str = Form(""),
    from_text: str = Form(""),
    to_text: str = Form(""),
    direction: str = Form(""),
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    restriction_type: str = Form(""),
    priority: str = Form("normal"),
    max_weight_t: str = Form(""),
    max_height_m: str = Form(""),
    max_width_m: str = Form(""),
    max_length_m: str = Form(""),
    source: str = Form(""),
    source_url: str = Form(""),
    geometry_geojson: str = Form(""),
    geometry_quality: str = Form(""),
    geometry_meta_json: str = Form(""),
    geometry_checked: str | None = Form(None),
    freigabe_org_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    values = _form_data(**locals())
    try:
        road_closure_service.update_closure(
            db, closure, user.id, _save_data(_org(user), values), expected_version=version
        )
        road_closure_service.set_shares(db, closure, freigabe_org_ids, user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _edit_page(request, db, user, closure, values, str(exc), 422)
    return RedirectResponse(f"/strassensperren/{closure_id}", status_code=303)


@router.post("/{closure_id}/deaktivieren")
def deaktivieren(
    closure_id: int,
    request: Request,
    grund: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    try:
        road_closure_service.deactivate_closure(db, closure, user.id, grund.strip())
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f"/strassensperren/{closure_id}", status_code=303)


@router.post("/{closure_id}/reaktivieren")
def reaktivieren(
    closure_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    try:
        road_closure_service.reactivate_closure(db, closure, user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f"/strassensperren/{closure_id}", status_code=303)


@router.post("/{closure_id}/loeschen")
def loeschen(
    closure_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    road_closure_service.delete_closure(db, closure, user.id)
    db.commit()
    return RedirectResponse("/strassensperren", status_code=303)
