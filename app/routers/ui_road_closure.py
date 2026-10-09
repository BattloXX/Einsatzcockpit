"""Serverseitige Verwaltung der organisationsweiten Strassensperren."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import decrypt_secret, encrypt_secret
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
    RoadClosure,
    RoadClosureAccessToken,
    RoadClosureChange,
    RoadClosureDocument,
    RoadClosureNotification,
    RoadClosureTeamsConfig,
)
from app.models.user import User
from app.services import road_closure_service, road_closure_stats_service, road_closure_token_service
from app.services.address_autocomplete import suggest_addresses
from app.services.road_closure_document_service import absolute_path, delete_document, store_document
from app.services.road_closure_public_service import public_closure_dict
from app.services.road_closure_section_service import resolve_section, validate_address

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


def _filters(status: str, zeitraum: str, scope: str, q: str, restriction_type: str, org, von: str = "", bis: str = ""):
    if status not in {"current", "active", "planned", "all"}:
        status = "current"
    if scope not in {"all", "own", "shared"}:
        scope = "all"
    if zeitraum not in {"", "heute", "7tage", "30tage", "eigen"}:
        zeitraum = ""
    date_von: datetime | None = None
    date_bis: datetime | None = None
    if zeitraum in {"heute", "7tage", "30tage"}:
        # local_date_to_utc deliberately receives org: DB uses naive UTC.
        local_now = datetime.now(UTC).astimezone(org_tz(org))
        start = local_now.date()
        end = start + timedelta(days=29 if zeitraum == "30tage" else (6 if zeitraum == "7tage" else 0))
        date_von = local_date_to_utc(start.isoformat(), org=org)
        date_bis = local_date_to_utc(end.isoformat(), end=True, org=org)
    elif zeitraum == "eigen":
        try:
            date_von = local_date_to_utc(date.fromisoformat(von).isoformat(), org=org) if von else None
        except ValueError:
            date_von = None
        try:
            date_bis = local_date_to_utc(date.fromisoformat(bis).isoformat(), org=org, end=True) if bis else None
        except ValueError:
            date_bis = None
    return (
        (None if status == "all" else status),
        date_von,
        date_bis,
        scope,
        q.strip(),
        restriction_type if restriction_type in RESTRICTION_TYPES else "",
    )


def _closures(
    db: Session, user: User, status: str, zeitraum: str, scope: str, q: str, restriction_type: str, geometrie: str,
    von: str = "", bis: str = "",
):
    selected_status, filter_von, filter_bis, selected_scope, text, selected_type = _filters(
        status, zeitraum, scope, q, restriction_type, _org(user), von, bis
    )
    items = road_closure_service.list_closures(
        db,
        _org(user).id,
        status=selected_status,
        von=filter_von,
        bis=filter_bis,
        text=text,
        restriction_type=selected_type or None,
        scope=selected_scope,
        geometrie=geometrie if geometrie in {"", "pruefen", "ok", "fehlt"} else "",
    )
    return items, {
        "status": status if status in {"current", "active", "planned", "all"} else "current",
        "zeitraum": zeitraum if zeitraum in {"", "heute", "7tage", "30tage", "eigen"} else "",
        "scope": selected_scope,
        "q": text,
        "restriction_type": selected_type,
        "geometrie": geometrie if geometrie in {"", "pruefen", "ok", "fehlt"} else "",
        "von": von if zeitraum == "eigen" else "",
        "bis": bis if zeitraum == "eigen" else "",
    }


def _filter_query(filters: dict, drop: str | None = None) -> str:
    values = {key: value for key, value in filters.items() if key != drop}
    if drop == "zeitraum":
        values.pop("von", None)
        values.pop("bis", None)
    defaults = {
        "status": "current", "zeitraum": "", "scope": "all", "q": "",
        "restriction_type": "", "geometrie": "", "von": "", "bis": "",
    }
    return urlencode({key: value for key, value in values.items() if value != defaults.get(key, "")})


_FILTER_LABELS = {
    "status": {"active": "Aktiv", "planned": "Geplant", "all": "Alle Status"},
    "zeitraum": {"heute": "Heute", "7tage": "7 Tage", "30tage": "30 Tage", "eigen": "Eigener Zeitraum"},
    "scope": {"own": "Eigene", "shared": "Nachbarn"},
    "geometrie": {"pruefen": "Geometrie prüfen", "ok": "Geometrie geprüft", "fehlt": "Geometrie fehlt"},
}


def _active_filters(filters: dict) -> list[dict[str, str]]:
    """Pills for non-default filters; each link removes exactly that filter."""
    pills = []
    for key in ("status", "zeitraum", "scope", "geometrie", "restriction_type", "q"):
        value = filters.get(key) or ""
        if key == "status" and value == "current" or key == "scope" and value == "all" or not value:
            continue
        if key == "restriction_type":
            label = RESTRICTION_TYPES.get(value, value)
        elif key == "q":
            label = f"Suche: {value}"
        else:
            label = _FILTER_LABELS[key].get(value, value)
        rest = _filter_query(filters, drop=key)
        # Empty query would restore the remembered cookie filters, so reset explicitly.
        pills.append({"label": label, "href": "/strassensperren?" + (rest or "reset=1")})
    return pills


def _cookie_filters(request: Request, user: User) -> dict:
    raw = request.cookies.get("sperren_filter", "")
    values = {key: values[-1] for key, values in parse_qs(raw).items()}
    # Normalize without querying: _filters performs all validation used by routes.
    selected, _, _, scope, text, kind = _filters(
        values.get("status", "current"), values.get("zeitraum", ""), values.get("scope", "all"),
        values.get("q", ""), values.get("restriction_type", ""), _org(user),
        values.get("von", ""), values.get("bis", ""),
    )
    valid_periods = {"", "heute", "7tage", "30tage", "eigen"}
    valid_geometry = {"", "pruefen", "ok", "fehlt"}
    period = values.get("zeitraum", "")
    geometry = values.get("geometrie", "")
    return {
        "status": selected or "all", "zeitraum": period if period in valid_periods else "",
        "scope": scope, "q": text, "restriction_type": kind,
        "geometrie": geometry if geometry in valid_geometry else "",
        "von": values.get("von", ""), "bis": values.get("bis", ""),
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


def _infoscreen_payload(
    db: Session, org, closures, *, scope: str, refresh_sec: int = 60, rotation_sec: int = 0,
    permissions: dict | None = None, owner_names: dict[int, str] | None = None,
) -> dict:
    """The deliberately whitelisted data contract for all status displays."""
    permissions = permissions or {}
    show_planned = permissions.get("zeige_geplante", True)
    items = []
    for closure in closures:
        item = public_closure_dict(closure, org)
        if not show_planned and item["status"] == "planned":
            continue
        if not permissions.get("zeige_grund", True):
            item.pop("reason", None)
        if not permissions.get("zeige_einschraenkungen", True):
            item.pop("einschraenkungen", None)
            item.pop("exceptions", None)
        item["nachbar"] = (owner_names or {}).get(closure.org_id) if closure.org_id != org.id else None
        items.append(item)
    metrics = road_closure_stats_service.kennzahlen(db, org, scope=scope)
    if not show_planned:
        metrics["geplant"] = 0
    return {
        "stand": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z", "kennzahlen": metrics,
        "sperren": items, "refresh_sec": refresh_sec, "rotation_sec": rotation_sec, "zeige_geplante": show_planned,
    }


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
        "reason": text("reason"),
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
        "teams_melden": bool(values.get("teams_melden")),
    }


def _edit_page(request, db, user, closure=None, form_data=None, error=None, status_code=200, related=None):
    teams_config = _teams_config(db, _org(user).id)
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
            "org_city": _org(user).city or "",
            "related": related or [],
            "back_query": _filter_query(_cookie_filters(request, user)),
            "teams_default": bool(teams_config and teams_config.standard_melden),
            "teams_aktiv": bool(teams_config and teams_config.enabled and teams_config.webhook_url_enc),
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
    von: str = "",
    bis: str = "",
):
    if request.query_params.get("reset") == "1":
        status, zeitraum, scope, q, restriction_type, geometrie, von, bis = "current", "", "all", "", "", "", "", ""
    elif not request.query_params:
        stored = _cookie_filters(request, user)
        status, zeitraum, scope, q, restriction_type, geometrie, von, bis = (
            stored[key] for key in ("status", "zeitraum", "scope", "q", "restriction_type", "geometrie", "von", "bis")
        )
    items, filters = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie, von, bis)
    filter_query = _filter_query(filters)
    metrics = road_closure_stats_service.kennzahlen(db, _org(user), scope="all")
    context = {
        "user": user, "closures": items, "filters": filters, "filter_query": filter_query,
        "restriction_types": RESTRICTION_TYPES, "active_count": metrics["aktiv"], "planned_count": metrics["geplant"],
        "ungeprueft_count": metrics["geometrie_pruefen"], "status_labels": CLOSURE_STATUS, "color": _color,
        "closure_status": road_closure_service.compute_status, "can_edit": has_role(user, "objekt_verwalter"),
        "org_names": _org_names(db, items), "active_filters": _active_filters(filters),
        "oob": request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true",
    }
    template = "road_closure/_inhalt.html" if context["oob"] else "road_closure/index.html"
    response = templates.TemplateResponse(
        request,
        template, context,
    )
    if request.query_params.get("reset") == "1":
        response.delete_cookie("sperren_filter", path="/strassensperren")
    else:
        response.set_cookie(
            "sperren_filter", filter_query, httponly=True, secure=settings.COOKIE_SECURE,
            samesite="lax", path="/strassensperren", max_age=30 * 86400,
        )
    return response


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
    von: str = "",
    bis: str = "",
):
    items, filters = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie, von, bis)
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
    von: str = "",
    bis: str = "",
):
    items, _ = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie, von, bis)
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
    status: str = "current", zeitraum: str = "", scope: str = "all", q: str = "",
    restriction_type: str = "", geometrie: str = "", von: str = "", bis: str = "",
):
    closures, filters = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie, von, bis)
    data = _infoscreen_payload(db, _org(user), closures, scope="all", owner_names=_org_names(db, closures))
    return templates.TemplateResponse(
        request,
        "road_closure/infoscreen.html",
        {
            "user": user,
            "org": _org(user),
            "modus": "status", "external": False, "daten": data,
            "daten_url": "/strassensperren/status/daten?" + _filter_query(filters), "zeige_karte": True,
            "filter_query": _filter_query(filters),
            "ungeprueft_count": road_closure_stats_service.kennzahlen(db, _org(user))["geometrie_pruefen"],
        },
    )


@router.get("/status/daten")
def status_daten(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
    status: str = "current", zeitraum: str = "", scope: str = "all", q: str = "",
    restriction_type: str = "", geometrie: str = "", von: str = "", bis: str = "",
):
    closures, _ = _closures(db, user, status, zeitraum, scope, q, restriction_type, geometrie, von, bis)
    payload = _infoscreen_payload(db, _org(user), closures, scope="all", owner_names=_org_names(db, closures))
    return JSONResponse(payload)


def _intern_infoscreen_daten(db: Session, user: User) -> dict:
    closures = road_closure_service.list_closures(db, _org(user).id, status="current", scope="all")
    return _infoscreen_payload(db, _org(user), closures, scope="all", owner_names=_org_names(db, closures))


@router.get("/infoscreen", response_class=HTMLResponse)
def infoscreen_intern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
):
    return templates.TemplateResponse(
        request,
        "road_closure/infoscreen.html",
        {
            "user": user, "org": _org(user), "modus": "infoscreen", "external": False,
            "daten": _intern_infoscreen_daten(db, user), "daten_url": "/strassensperren/infoscreen/daten",
            "zeige_karte": True, "filter_query": "",
        },
    )


@router.get("/infoscreen/daten")
def infoscreen_intern_daten(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_strassensperren_enabled),
):
    return JSONResponse(_intern_infoscreen_daten(db, user))


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


@router.get("/adresse/vorschlaege")
async def address_suggestions(
    db: Session = Depends(get_db), user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled), q: str = "", field: str = "street",
    city: str = "", street: str = "",
):
    if field not in {"street", "house"} or not q.strip():
        return JSONResponse({"items": []})
    items = await suggest_addresses(
        db, q=q.strip(), field=field, city=city.strip() or _org(user).city, street=street.strip() or None,
        org_id=user.org_id, limit=settings.PHOTON_SUGGEST_LIMIT,
    )
    from dataclasses import asdict
    return JSONResponse({"items": [asdict(item) for item in items]})


@router.post("/adresse/pruefen")
async def address_validate(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail={"fehler": "Ungültige Anfrage."}) from None
    return JSONResponse(await validate_address(
        db, _org(user), str(data.get("street") or "").strip(), str(data.get("from_text") or "").strip(),
        str(data.get("to_text") or "").strip(), str(data.get("city") or "").strip() or _org(user).city,
    ))


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
    reason: str = Form(""),
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
    teams_melden: str | None = Form(None),
    freigabe_org_ids: list[int] = Form([]),
    als_neu_bestaetigt: str | None = Form(None),
    ersetzt_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    values = _form_data(**locals())
    try:
        data = road_closure_service.validate_closure_data(_save_data(_org(user), values), partial=False)
        related = road_closure_service.find_related(
            db,
            _org(user).id,
            street=data["street"],
            reference_number=data["reference_number"],
            valid_from=data["valid_from"],
            valid_until=data["valid_until"],
            geometry=json.loads(data["geometry_geojson"]) if data["geometry_geojson"] else None,
            from_text=data["from_text"],
            to_text=data["to_text"],
            title=data["title"],
            description=data["description"],
        )
        if related and not als_neu_bestaetigt and ersetzt_id is None:
            return _edit_page(request, db, user, form_data=values, status_code=409, related=related)
        old = _closure_or_404(db, user, ersetzt_id, writable=True) if ersetzt_id is not None else None
        closure = road_closure_service.create_closure(db, _org(user).id, user.id, data)
        road_closure_service.set_shares(db, closure, freigabe_org_ids, user.id)
        if old is not None:
            road_closure_service.supersede_closure(db, old, closure, user.id, "ui")
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


def _teams_config(db, org_id: int):
    return db.query(RoadClosureTeamsConfig).filter(RoadClosureTeamsConfig.org_id == org_id).first()


@router.get("/einstellungen", response_class=HTMLResponse)
def teams_einstellungen(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_role("objekt_verwalter", "org_admin")),
                         _guard: None = Depends(require_strassensperren_enabled)):
    config = _teams_config(db, _org(user).id)
    notifications = db.query(RoadClosureNotification).filter(RoadClosureNotification.org_id == _org(user).id).order_by(
        RoadClosureNotification.created_at.desc()).limit(20).all()
    webhook_host = None
    if config and config.webhook_url_enc:
        try:
            # Nur der Host wird angezeigt – Pfad und Signatur der Webhook-URL sind geheim.
            webhook_host = urlsplit(decrypt_secret(config.webhook_url_enc)).hostname or "konfiguriert"
        except Exception:
            webhook_host = "konfiguriert (nicht lesbar)"
    closure_titles = {
        row.id: row.title for row in db.query(RoadClosure).filter(
            RoadClosure.id.in_({item.road_closure_id for item in notifications})
        ).all()
    } if notifications else {}
    return templates.TemplateResponse(request, "road_closure/einstellungen.html", {
        "user": user, "config": config, "notifications": notifications, "webhook_host": webhook_host,
        "closure_titles": closure_titles, "error": request.query_params.get("fehler"),
        "hinweis": request.query_params.get("hinweis"),
    })


@router.post("/einstellungen")
def teams_einstellungen_speichern(
    request: Request, aktiv: str | None = Form(None), webhook_url: str = Form(""),
    webhook_entfernen: str | None = Form(None), auto_neu: str | None = Form(None),
    auto_aenderung: str | None = Form(None), auto_aufhebung: str | None = Form(None),
    standard_melden: str | None = Form(None), include_map: str | None = Form(None),
    db: Session = Depends(get_db), user: User = Depends(require_role("objekt_verwalter", "org_admin")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    url = webhook_url.strip()
    if url and (not url.startswith("https://") or len(url) > 1000):
        return RedirectResponse("/strassensperren/einstellungen?fehler=Ungültige+Webhook-URL", status_code=303)
    config = _teams_config(db, _org(user).id) or RoadClosureTeamsConfig(org_id=_org(user).id)
    changed = bool(url or webhook_entfernen)
    config.enabled, config.auto_neu, config.auto_aenderung = bool(aktiv), bool(auto_neu), bool(auto_aenderung)
    config.auto_aufhebung = bool(auto_aufhebung)
    config.standard_melden, config.include_map = bool(standard_melden), bool(include_map)
    if url:
        config.webhook_url_enc = encrypt_secret(url)
    elif webhook_entfernen:
        config.webhook_url_enc = None
    config.updated_at, config.updated_by_user_id = datetime.now(UTC).replace(tzinfo=None), user.id
    db.add(config)
    write_audit(db, "road_closure.teams_config_saved", org_id=_org(user).id, user_id=user.id,
        payload={"webhook_geaendert": changed})
    db.commit()
    return RedirectResponse("/strassensperren/einstellungen?hinweis=Gespeichert", status_code=303)


@router.post("/einstellungen/test")
async def teams_testnachricht(
    request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter", "org_admin")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    from app.services.road_closure_notify_service import build_test_card, send_payload

    config = _teams_config(db, _org(user).id)
    if not config or not config.webhook_url_enc:
        return RedirectResponse("/strassensperren/einstellungen?fehler=" + quote("Keine Webhook-URL hinterlegt"),
                                status_code=303)
    try:
        webhook = decrypt_secret(config.webhook_url_enc)
    except Exception:
        return RedirectResponse("/strassensperren/einstellungen?fehler=" + quote("Webhook-URL nicht lesbar"),
                                status_code=303)
    sent, _retryable, reason = await send_payload(webhook, build_test_card(_org(user).name))
    if sent:
        return RedirectResponse("/strassensperren/einstellungen?hinweis=" + quote("Testnachricht gesendet"),
                                status_code=303)
    return RedirectResponse(
        "/strassensperren/einstellungen?fehler=" + quote(f"Testnachricht fehlgeschlagen: {reason}"), status_code=303
    )


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
    documents = db.query(RoadClosureDocument).execution_options(include_all_tenants=True).filter(
        RoadClosureDocument.road_closure_id == closure.id
    ).order_by(RoadClosureDocument.created_at.desc()).all()
    superseded_by = db.get(RoadClosure, closure.superseded_by_id) if closure.superseded_by_id else None
    supersedes = db.query(RoadClosure).execution_options(include_all_tenants=True).filter(
        RoadClosure.superseded_by_id == closure.id
    ).first()
    freigabe_link = None
    teams_config = _teams_config(db, closure.org_id) if closure.org_id == user.org_id else None
    teams_notifications = (
        db.query(RoadClosureNotification).filter(
            RoadClosureNotification.org_id == closure.org_id,
            RoadClosureNotification.road_closure_id == closure.id,
        ).order_by(RoadClosureNotification.created_at.desc()).all()
        if closure.org_id == user.org_id else []
    )
    if closure.org_id == user.org_id and has_role(user, "objekt_verwalter"):
        token = road_closure_token_service.active_detail_token(db, closure)
        raw = road_closure_token_service.token_plain(token) if token else None
        if token and raw:
            freigabe_link = {
                "url": road_closure_token_service.public_url(raw, "detail"),
                "expires_at": token.expires_at, "created_at": token.created_at,
                "id": token.id, "last_used_at": token.last_used_at,
            }
    return templates.TemplateResponse(
        request,
        "road_closure/detail.html",
        {
            "user": user,
            "closure": closure,
            "changes": changes,
            "documents": documents,
            "fehler": request.query_params.get("fehler", ""),
            "status": road_closure_service.compute_status(closure),
            "status_labels": CLOSURE_STATUS,
            "color": _color(closure),
            "directions": DIRECTIONS,
            "geometry_quality_labels": GEOMETRY_QUALITY,
            "can_edit": has_role(user, "objekt_verwalter"),
            "can_delete": has_role(user, "org_admin"),
            "owner_org_name": owner_org.name if owner_org is not None else None,
            "shared_org_names": shared_org_names,
            "superseded_by": superseded_by,
            "supersedes": supersedes,
            "back_query": _filter_query(_cookie_filters(request, user)),
            "freigabe_link": freigabe_link,
            "teams_config": teams_config,
            "teams_notifications": teams_notifications,
        },
    )


@router.post("/{closure_id}/teams-senden")
def teams_senden(closure_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("objekt_verwalter")),
                 _guard: None = Depends(require_strassensperren_enabled)):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    from app.services.road_closure_notify_service import enqueue
    enqueue(db, closure, "manuell", user_id=user.id, manuell=True)
    db.commit()
    return RedirectResponse(f"/strassensperren/{closure_id}", status_code=303)


@router.post("/{closure_id}/teams/{notification_id}/erneut")
def teams_erneut(closure_id: int, notification_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("objekt_verwalter")),
                 _guard: None = Depends(require_strassensperren_enabled)):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    notification = db.query(RoadClosureNotification).filter(RoadClosureNotification.id == notification_id,
        RoadClosureNotification.org_id == closure.org_id, RoadClosureNotification.road_closure_id == closure.id).first()
    if notification is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    notification.status = "retry"
    notification.next_attempt_at = datetime.now(UTC).replace(tzinfo=None)
    notification.lease_until = None
    db.commit()
    return RedirectResponse(f"/strassensperren/{closure_id}", status_code=303)


@router.post("/{closure_id}/freigabelink")
def freigabelink_erzeugen(
    closure_id: int, gueltig_bis: str = Form(""), db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    expires_at = local_date_to_utc(gueltig_bis, end=True, org=_org(user)) if gueltig_bis else None
    if gueltig_bis and expires_at is None:
        raise HTTPException(422, "Ungültiges Ablaufdatum")
    if expires_at:
        active = road_closure_token_service.active_detail_token(db, closure)
        if active:
            road_closure_token_service.revoke_token(db, active, user.id)
        road_closure_token_service.create_token(
            db, closure.org_id, "detail", user.id, road_closure_id=closure.id, expires_at=expires_at
        )
    else:
        road_closure_token_service.get_or_create_detail_token(db, closure, user.id)
    db.commit()
    return RedirectResponse(f"/strassensperren/{closure.id}?freigabe=1#freigabelink", status_code=303)


@router.post("/{closure_id}/freigabelink/{token_id}/widerrufen")
def freigabelink_widerrufen(
    closure_id: int, token_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    token = db.query(RoadClosureAccessToken).execution_options(include_all_tenants=True).filter(
        RoadClosureAccessToken.id == token_id, RoadClosureAccessToken.road_closure_id == closure.id,
        RoadClosureAccessToken.org_id == closure.org_id,
    ).first()
    if token is None:
        raise HTTPException(404, "Nicht gefunden")
    road_closure_token_service.revoke_token(db, token, user.id)
    db.commit()
    return RedirectResponse(f"/strassensperren/{closure.id}#freigabelink", status_code=303)


@router.get("/{closure_id}/dokumente/{document_id}")
def dokument_anzeigen(
    closure_id: int, document_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)), _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=False)
    document = db.query(RoadClosureDocument).execution_options(include_all_tenants=True).filter(
        RoadClosureDocument.id == document_id, RoadClosureDocument.road_closure_id == closure.id
    ).first()
    if document is None:
        raise HTTPException(404, "Nicht gefunden")
    try:
        path = absolute_path(document)
    except ValueError as exc:
        raise HTTPException(404, "Nicht gefunden") from exc
    if not path.is_file():
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(
        path, media_type="application/pdf", filename=document.filename, content_disposition_type="inline"
    )


@router.post("/{closure_id}/dokumente")
async def dokument_hochladen(
    closure_id: int, datei: UploadFile = File(...), db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")), _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    try:
        store_document(db, closure, await datei.read(), datei.filename or "verordnung.pdf", user.id, "ui")
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/strassensperren/{closure.id}?fehler={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/strassensperren/{closure.id}", status_code=303)


@router.post("/{closure_id}/dokumente/{document_id}/loeschen")
def dokument_loeschen(
    closure_id: int, document_id: int, db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")), _guard: None = Depends(require_strassensperren_enabled),
):
    closure = _closure_or_404(db, user, closure_id, writable=True)
    document = db.query(RoadClosureDocument).execution_options(include_all_tenants=True).filter(
        RoadClosureDocument.id == document_id, RoadClosureDocument.road_closure_id == closure.id
    ).first()
    if document is None:
        raise HTTPException(404, "Nicht gefunden")
    delete_document(db, closure, document, user.id, "ui")
    db.commit()
    return RedirectResponse(f"/strassensperren/{closure.id}", status_code=303)


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
    closure = _closure_or_404(db, user, closure_id, writable=True)
    if not request.query_params.get("valid_until") and request.query_params.get("reference_number") is None:
        return _edit_page(request, db, user, closure)
    # Vorbelegung aus einer erkannten Verlängerung: übrige Felder und Freigaben unverändert übernehmen.
    values = {
        field: getattr(closure, field)
        for field in road_closure_service.EDITABLE_FIELDS | {"version", "geometry_status"}
    }
    values["freigabe_org_ids"] = list(road_closure_service.shared_org_ids(db, closure))
    valid_until = request.query_params.get("valid_until")
    if valid_until:
        try:
            if local_input_to_utc(valid_until, _org(user)) is not None:
                values["valid_until"] = valid_until
        except ValueError:
            pass
    reference_number = request.query_params.get("reference_number")
    if reference_number is not None and len(reference_number.strip()) <= 120:
        values["reference_number"] = reference_number.strip()
    return _edit_page(request, db, user, closure, values)


@router.post("/{closure_id}/bearbeiten")
def bearbeiten_speichern(
    closure_id: int,
    request: Request,
    version: int = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    reason: str = Form(""),
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
    teams_melden: str | None = Form(None),
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
