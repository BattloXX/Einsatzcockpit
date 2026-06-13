"""UI-Router: Großschadenslage – Phasen-Board, Stellen-CRUD, Abschluss."""
import logging
import random
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role, require_role, same_org_or_system_admin
from app.core.security import get_author_name
from app.core.templating import templates
from app.db import get_db
from app.core.html_utils import sanitize_html
from app.models.major_incident import (
    CROSS_MARKER_STATUS_COLOR,
    CROSS_MARKER_STATUS_LABEL,
    CROSS_MARKER_TYPE_ICON,
    CROSS_MARKER_TYPE_LABEL,
    CrossSiteMarker,
    JOURNAL_CATEGORIES,
    JOURNAL_CATEGORY_COLOR,
    JOURNAL_TEMPLATES,
    SITE_PRIORITY_COLOR,
    SITE_PRIORITY_LABEL,
    STAFF_FUNCTION_LABEL,
    CitizenReport,
    CommLogEntry,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    LageJournalMedia,
    MajorIncident,
    MajorIncidentStatus,
    Sector,
    SiteLogEntry,
    SitePhase,
    SitePriority,
    SiteResourceAssignment,
    StaffAssignment,
    StaffFunction,
)
from app.models.master import FireDept, Member, VehicleMaster
from app.services.ai_service import is_enabled as ai_is_enabled
from app.services.broadcast import broadcast_lage, manager
from app.services.major_incident_service import (
    close_lage,
    create_lage,
    create_site,
    get_active_lage,
)

router = APIRouter()

logger = logging.getLogger("einsatzleiter.major_incident")

# Pending phone verifications: verify_token → {pin, expires_at, ...}
_pending_verifications: dict[str, dict] = {}

_MI_FEATURE_KEYS: frozenset[str] = frozenset({
    "mi_feature_stab", "mi_feature_funkjournal", "mi_feature_meldungen",
    "mi_feature_sektoren", "mi_feature_karte", "mi_feature_zeitreise", "mi_feature_ressourcen",
    "mi_feature_uebergreifend",
})


def _get_mi_features(db: Session) -> dict[str, bool]:
    from app.models.master import SystemSettings as _SS
    rows = db.query(_SS).filter(_SS.key.in_(_MI_FEATURE_KEYS)).all()
    cfg = {r.key: r.value for r in rows}
    return {
        "stab":           cfg.get("mi_feature_stab",           "true") != "false",
        "funkjournal":    cfg.get("mi_feature_funkjournal",     "true") != "false",
        "meldungen":      cfg.get("mi_feature_meldungen",       "true") != "false",
        "sektoren":       cfg.get("mi_feature_sektoren",        "true") != "false",
        "karte":          cfg.get("mi_feature_karte",           "true") != "false",
        "zeitreise":      cfg.get("mi_feature_zeitreise",       "true") != "false",
        "ressourcen":     cfg.get("mi_feature_ressourcen",      "true") != "false",
        "uebergreifend":  cfg.get("mi_feature_uebergreifend",   "true") != "false",
    }


PHASE_ORDER = [
    SitePhase.eingegangen,
    SitePhase.erkundung,
    SitePhase.bewertet,
    SitePhase.disponiert,
    SitePhase.in_arbeit,
    SitePhase.erledigt,
]

PHASE_LABELS = {
    SitePhase.eingegangen: "Eingegangen",
    SitePhase.erkundung:   "Erkundung",
    SitePhase.bewertet:    "Bewertet",
    SitePhase.disponiert:  "Disponiert",
    SitePhase.in_arbeit:   "In Arbeit",
    SitePhase.erledigt:    "Erledigt",
    SitePhase.abgebrochen: "Abgebrochen",
}


async def _apply_ai_prio(site: IncidentSite, db: Session, org_id: int | None = None) -> None:
    """Automatically suggest priority via AI. Never raises.

    Only sets priority when none exists — never overwrites a manually set value.
    """
    text = site.einsatzgrund or site.bezeichnung
    if site.priority or not ai_is_enabled() or not text:
        return
    try:
        from app.services.ai_service import analyze_site_reconnaissance
        result = await analyze_site_reconnaissance(
            text,
            {"bezeichnung": site.bezeichnung, "ort": site.ort or "", "strasse": site.strasse or ""},
            org_id=org_id,
        )
        if result and result.get("prio_vorschlag"):
            site.priority = SitePriority(result["prio_vorschlag"])
            site.danger_score = result.get("danger_score")
            site.urgency_score = result.get("urgency_score")
    except Exception:
        pass


def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org_access(user, lage: MajorIncident) -> None:
    if not same_org_or_system_admin(user, lage.org_id):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Lage")


def _sites_by_phase(lage: MajorIncident) -> dict:
    grouped: dict[SitePhase, list[IncidentSite]] = {p: [] for p in PHASE_ORDER}
    grouped[SitePhase.abgebrochen] = []
    for site in sorted(lage.sites, key=lambda s: (s.sort_index, s.id)):
        grouped.setdefault(site.phase, []).append(site)
    return grouped


async def _geocode_site(site: IncidentSite) -> None:
    """Try to fill site.lat/lng from its address. Silently ignores failures."""
    if site.lat and site.lng:
        return
    if not (site.strasse or site.ort):
        return
    try:
        from app.services.geocoding import geocode_address
        geo = await geocode_address(site.strasse, site.hausnr, site.ort)
        if geo:
            site.lat, site.lng = geo.lat, geo.lng
    except Exception:
        pass


def _can_edit(user) -> bool:
    return has_role(user, "incident_leader", "admin", "org_admin", "recorder")


def _can_manage(user) -> bool:
    return has_role(user, "incident_leader", "admin", "org_admin")


def _nav_counts(lage_id: int, lage: MajorIncident, db: Session) -> dict:
    """Returns open_count and new_meldungen_count for topbar nav badges."""
    open_count = sum(
        1 for s in lage.sites
        if s.phase not in (SitePhase.erledigt, SitePhase.abgebrochen)
    )
    new_meldungen_count = (
        db.query(CitizenReport)
        .filter(CitizenReport.major_incident_id == lage_id, CitizenReport.status == "new")
        .count()
    )
    return {"open_count": open_count, "new_meldungen_count": new_meldungen_count}


# ── Navigation: aktive Lage der Org ─────────────────────────────────────────

@router.get("/lage", response_class=HTMLResponse)
async def lage_overview(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    org_id = getattr(user, "org_id", None)
    is_sysadmin = "system_admin" in {r.code for r in user.roles}

    if not org_id and not is_sysadmin:
        raise HTTPException(status_code=403)

    if org_id:
        active = get_active_lage(db, org_id)
        if active:
            return RedirectResponse(f"/lage/{active.id}", status_code=302)
        all_lage = (
            db.query(MajorIncident)
            .filter(MajorIncident.org_id == org_id)
            .order_by(MajorIncident.started_at.desc())
            .limit(20)
            .all()
        )
    else:
        # system_admin ohne Org: alle aktiven Lagen aller Orgs
        all_lage = (
            db.query(MajorIncident)
            .order_by(MajorIncident.started_at.desc())
            .limit(50)
            .all()
        )
        active_all = [li for li in all_lage if li.status == MajorIncidentStatus.active]
        if len(active_all) == 1:
            return RedirectResponse(f"/lage/{active_all[0].id}", status_code=302)

    return templates.TemplateResponse(request, "incident_major/lage_overview.html", {
        "user": user,
        "lage_list": all_lage,
        "can_manage": _can_manage(user),
        "phase_labels": PHASE_LABELS,
    })


# ── Lage manuell starten ─────────────────────────────────────────────────────

@router.get("/lage/neu", response_class=HTMLResponse)
async def lage_neu_form(
    request: Request,
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    return templates.TemplateResponse(request, "incident_major/lage_start.html", {
        "user": request.state.user,
    })


@router.post("/lage/neu")
async def lage_neu_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    is_exercise: bool = Form(False),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    user = request.state.user
    org_id = getattr(user, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=403)

    lage = create_lage(
        db, org_id, name.strip(),
        description=description.strip() or None,
        is_exercise=is_exercise,
        started_by_user_id=user.id,
    )
    write_audit(db, "major_incident.created", user_id=user.id,
                payload={"name": lage.name, "lage_id": lage.id})
    db.commit()
    return RedirectResponse(f"/lage/{lage.id}", status_code=303)


# ── Board ────────────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}", response_class=HTMLResponse)
async def lage_board(
    request: Request,
    lage_id: int,
    show_abgebrochen: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sites_by_phase = _sites_by_phase(lage)

    open_count = sum(
        len(sites_by_phase[p])
        for p in [SitePhase.eingegangen, SitePhase.erkundung, SitePhase.bewertet,
                  SitePhase.disponiert, SitePhase.in_arbeit]
    )
    done_count = len(sites_by_phase[SitePhase.erledigt])

    abgebrochen_sites = sites_by_phase.get(SitePhase.abgebrochen, [])
    sectors = sorted(lage.sectors, key=lambda s: s.id)
    sectors_by_id = {s.id: s for s in sectors}
    cross_markers = sorted(lage.cross_site_markers, key=lambda m: (m.sort_index, m.id))

    new_meldungen_count = (
        db.query(CitizenReport)
        .filter(CitizenReport.major_incident_id == lage_id, CitizenReport.status == "new")
        .count()
    )

    from app.config import settings as _cfg
    from app.models.master import OrgSettings as _OS, FireDept as _FD
    _org_s = db.query(_OS).filter(_OS.org_id == lage.org_id).first() if lage.org_id else None
    _weather_enabled = (
        _cfg.WEATHER_ENABLED
        and (
            _org_s is None
            or _org_s.weather_enabled is None
            or bool(_org_s.weather_enabled)
        )
    )
    _org = db.get(_FD, lage.org_id) if lage.org_id else None
    _org_lat = getattr(_org, "fallback_lat", None) or 47.41
    _org_lng = getattr(_org, "fallback_lng", None) or 9.74

    return templates.TemplateResponse(request, "incident_major/board.html", {
        "user": user,
        "lage": lage,
        "sites_by_phase": sites_by_phase,
        "phase_order": PHASE_ORDER,
        "phase_labels": PHASE_LABELS,
        "prio_color": SITE_PRIORITY_COLOR,
        "prio_label": SITE_PRIORITY_LABEL,
        "site_phases": [p.value for p in SitePhase],
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "open_count": open_count,
        "done_count": done_count,
        "new_meldungen_count": new_meldungen_count,
        "show_abgebrochen": show_abgebrochen,
        "abgebrochen_sites": abgebrochen_sites,
        "sectors": sectors,
        "sectors_by_id": sectors_by_id,
        "cross_markers": cross_markers,
        "cross_marker_type_label": CROSS_MARKER_TYPE_LABEL,
        "cross_marker_type_icon": CROSS_MARKER_TYPE_ICON,
        "cross_marker_status_label": CROSS_MARKER_STATUS_LABEL,
        "cross_marker_status_color": CROSS_MARKER_STATUS_COLOR,
        "now": datetime.now(UTC),
        "mi_features": _get_mi_features(db),
        "weather_enabled": _weather_enabled,
        "org_lat": _org_lat,
        "org_lng": _org_lng,
    })


# ── Einsatzstelle anlegen ────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/neu", response_class=HTMLResponse)
async def site_create(
    request: Request,
    lage_id: int,
    bezeichnung: str = Form(...),
    einsatzgrund: str = Form(""),
    ort: str = Form(""),
    strasse: str = Form(""),
    hausnr: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if lage.status != MajorIncidentStatus.active:
        raise HTTPException(status_code=400, detail="Lage nicht aktiv")

    try:
        site = create_site(
            db, lage,
            bezeichnung=bezeichnung.strip(),
            einsatzgrund=einsatzgrund.strip() or None,
            ort=ort.strip() or None,
            strasse=strasse.strip() or None,
            hausnr=hausnr.strip() or None,
            created_by=user.id,
        )
        await _geocode_site(site)
        await _apply_ai_prio(site, db, org_id=lage.org_id)
        db.add(SiteLogEntry(
            incident_site_id=site.id,
            kind="status",
            text="Einsatzstelle angelegt",
            user_id=user.id,
            author_name=get_author_name(request),
        ))
        write_audit(db, "major_incident.site.created", user_id=user.id,
                    payload={"lage_id": lage_id, "site_id": site.id, "bezeichnung": site.bezeichnung})
        # Auto-Abschnitts-Zuweisung nach Geocoding
        from app.services.geo_service import auto_assign_section
        auto_assign_section(db, site)
        db.commit()
    except Exception:
        logger.exception("Fehler beim Anlegen der Einsatzstelle lage_id=%s", lage_id)
        db.rollback()
        raise
    await broadcast_lage(lage_id, {"type": "site_created", "reload_board": True})
    return RedirectResponse(f"/lage/{lage_id}", status_code=303)


@router.post("/lage/{lage_id}/stellen/via-karte")
async def site_create_via_karte(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    """Erstellt eine Einsatzstelle aus einem Karten-Pin (JSON-Body)."""
    from fastapi.responses import JSONResponse
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if lage.status != MajorIncidentStatus.active:
        raise HTTPException(status_code=400, detail="Lage nicht aktiv")

    data = await request.json()
    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat/lng fehlen")

    einsatzgrund = (data.get("einsatzgrund") or "").strip()[:200] or None
    strasse = (data.get("strasse") or "").strip()[:120] or None
    hausnr = (data.get("hausnr") or "").strip()[:20] or None
    ort = (data.get("ort") or "").strip()[:120] or None
    bezeichnung = einsatzgrund or "Einsatzstelle"

    try:
        site = create_site(
            db, lage,
            bezeichnung=bezeichnung,
            einsatzgrund=einsatzgrund,
            strasse=strasse,
            hausnr=hausnr,
            ort=ort,
            lat=lat,
            lng=lng,
            source="manual",
            created_by=user.id,
        )
        db.add(SiteLogEntry(
            incident_site_id=site.id,
            kind="status",
            text="Einsatzstelle via Karten-Pin angelegt",
            user_id=user.id,
            author_name=get_author_name(request),
        ))
        from app.services.geo_service import auto_assign_section
        auto_assign_section(db, site)
        db.commit()
    except Exception:
        logger.exception("Fehler beim Anlegen via Karte lage_id=%s", lage_id)
        db.rollback()
        raise
    await broadcast_lage(lage_id, {"type": "site_created", "reload_board": True})
    return JSONResponse({"id": site.id, "bezeichnung": site.bezeichnung,
                         "lat": site.lat, "lng": site.lng})


# ── Phase ändern (Drag & Drop) ───────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/phase")
async def site_phase_change(
    request: Request,
    lage_id: int,
    site_id: int,
    phase: str = Form(...),
    sort_index: int = Form(0),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    try:
        new_phase = SitePhase(phase)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unbekannte Phase: {phase}")

    old_phase = site.phase
    if old_phase == new_phase and site.sort_index == sort_index:
        return Response(status_code=204)

    site.phase = new_phase
    site.sort_index = sort_index

    if old_phase != new_phase:
        db.add(SiteLogEntry(
            incident_site_id=site_id,
            kind="status",
            text=f"Phase: {PHASE_LABELS[old_phase]} → {PHASE_LABELS[new_phase]}",
            user_id=user.id,
            author_name=get_author_name(request),
        ))
        write_audit(db, "major_incident.site.phase_changed", user_id=user.id,
                    payload={"lage_id": lage_id, "site_id": site_id,
                             "from": old_phase.value, "to": new_phase.value})
    db.commit()
    await broadcast_lage(lage_id, {
        "type": "site_phase_changed",
        "site_id": site_id,
        "phase": new_phase.value,
        "reload_board": True,
    })
    return Response(status_code=204)


# ── Priorität setzen ─────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/prio")
async def site_prio_change(
    request: Request,
    lage_id: int,
    site_id: int,
    priority: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    if not priority.strip():
        old_prio = site.priority
        site.priority = None
        if old_prio:
            old_label = SITE_PRIORITY_LABEL.get(old_prio, "–")
            db.add(SiteLogEntry(
                incident_site_id=site_id,
                kind="prio",
                text=f"Priorität: {old_label} → –",
                user_id=user.id,
                author_name=get_author_name(request),
            ))
        db.commit()
        await broadcast_lage(lage_id, {"type": "site_prio_changed", "site_id": site_id, "reload_board": True})
        return Response(status_code=204)

    try:
        new_prio = SitePriority(int(priority))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400)

    old_prio = site.priority
    site.priority = new_prio

    if old_prio != new_prio:
        old_label = SITE_PRIORITY_LABEL.get(old_prio, "–") if old_prio else "–"
        db.add(SiteLogEntry(
            incident_site_id=site_id,
            kind="prio",
            text=f"Priorität: {old_label} → {SITE_PRIORITY_LABEL[new_prio]}",
            user_id=user.id,
            author_name=get_author_name(request),
        ))
    db.commit()
    await broadcast_lage(lage_id, {"type": "site_prio_changed", "site_id": site_id, "reload_board": True})
    return Response(status_code=204)


# ── Stellendetail (HTMX-Partial) ────────────────────────────────────────────

@router.get("/lage/{lage_id}/stellen/{site_id}", response_class=HTMLResponse)
async def site_detail(
    request: Request,
    lage_id: int,
    site_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    vehicles = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == lage.org_id, VehicleMaster.active == True)  # noqa: E712
        .order_by(VehicleMaster.display_order, VehicleMaster.code)
        .all()
    )

    sectors = sorted(lage.sectors, key=lambda s: s.id)

    citizen_report = None
    if site.source == "buerger":
        citizen_report = (
            db.query(CitizenReport)
            .filter(CitizenReport.site_id == site.id)
            .first()
        )

    return templates.TemplateResponse(request, "incident_major/_site_detail.html", {
        "user": user,
        "lage": lage,
        "site": site,
        "phase_labels": PHASE_LABELS,
        "prio_label": SITE_PRIORITY_LABEL,
        "prio_color": SITE_PRIORITY_COLOR,
        "can_edit": _can_edit(user),
        "vehicles": vehicles,
        "sectors": sectors,
        "now": datetime.now(UTC),
        "citizen_report": citizen_report,
    })


# ── Einzeldruck (Einsatzstelle) ─────────────────────────────────────────────

@router.get("/lage/{lage_id}/stellen/{site_id}/druck", response_class=HTMLResponse)
async def site_druck(
    request: Request,
    lage_id: int,
    site_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "incident_major/_site_druck.html", {
        "lage": lage,
        "site": site,
        "phase_labels": PHASE_LABELS,
        "prio_label": SITE_PRIORITY_LABEL,
    })


# ── Log-Eintrag hinzufügen ──────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/log")
async def site_log_add(
    request: Request,
    lage_id: int,
    site_id: int,
    text: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    db.add(SiteLogEntry(
        incident_site_id=site_id,
        kind="note",
        text=text.strip(),
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    db.commit()
    return Response(status_code=204)


# ── Ressource zuweisen ──────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/ressourcen")
async def site_resource_assign(
    request: Request,
    lage_id: int,
    site_id: int,
    vehicle_id: int | None = Form(None),
    label: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    resource_type = "vehicle" if vehicle_id else "free_text"
    actual_label = label.strip() or None
    if vehicle_id and not actual_label:
        vehicle = db.get(VehicleMaster, vehicle_id)
        if vehicle:
            actual_label = vehicle.display_label

    db.add(SiteResourceAssignment(
        incident_site_id=site_id,
        resource_type=resource_type,
        vehicle_id=vehicle_id,
        label=actual_label,
    ))
    db.add(SiteLogEntry(
        incident_site_id=site_id,
        kind="resource",
        text=f"Ressource zugewiesen: {actual_label or resource_type}",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    db.commit()
    return Response(status_code=204)


# ── Ressource vor Ort bestätigen ─────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/ressourcen/{res_id}/commit")
async def site_resource_commit(
    request: Request,
    lage_id: int,
    site_id: int,
    res_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    res = db.get(SiteResourceAssignment, res_id)
    if not res or res.incident_site_id != site_id:
        raise HTTPException(status_code=404)

    res.committed_at = datetime.now(UTC)
    db.add(SiteLogEntry(
        incident_site_id=site_id,
        kind="resource",
        text=f"Ressource vor Ort: {res.label or res.resource_type}",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    db.commit()
    return Response(status_code=204)


# ── Ressource freigeben ──────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/ressourcen/{res_id}/freigeben")
async def site_resource_release(
    request: Request,
    lage_id: int,
    site_id: int,
    res_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    res = db.get(SiteResourceAssignment, res_id)
    if not res or res.incident_site_id != site_id:
        raise HTTPException(status_code=404)

    res.released_at = datetime.now(UTC)
    db.add(SiteLogEntry(
        incident_site_id=site_id,
        kind="resource",
        text=f"Ressource freigegeben: {res.label or res.resource_type}",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    db.commit()
    return Response(status_code=204)


# ── Einsatzstelle bearbeiten ────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/bearbeiten")
async def site_edit(
    request: Request,
    lage_id: int,
    site_id: int,
    bezeichnung: str = Form(...),
    einsatzgrund: str = Form(""),
    ort: str = Form(""),
    strasse: str = Form(""),
    hausnr: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    old_bezeichnung = site.bezeichnung
    site.bezeichnung  = bezeichnung.strip()
    site.einsatzgrund = einsatzgrund.strip() or None
    site.ort          = ort.strip() or None
    site.strasse      = strasse.strip() or None
    site.hausnr       = hausnr.strip() or None

    if site.bezeichnung != old_bezeichnung:
        db.add(SiteLogEntry(
            incident_site_id=site_id,
            kind="status",
            text=f"Bezeichnung geändert: {old_bezeichnung} → {site.bezeichnung}",
            user_id=user.id,
            author_name=get_author_name(request),
        ))
    await _geocode_site(site)
    db.commit()
    await broadcast_lage(lage_id, {"type": "site_updated", "site_id": site_id, "reload_board": True})
    return Response(status_code=204)


# ── Koordinaten per Pin setzen ───────────────────────────────────────────────

@router.get("/lage/{lage_id}/stellen/{site_id}/pin", response_class=HTMLResponse)
async def site_pin_form(
    request: Request,
    lage_id: int,
    site_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    from app.models.master import FireDept as _FD
    org = db.get(_FD, lage.org_id) if lage.org_id else None
    return templates.TemplateResponse(request, "incident_major/_site_pin.html", {
        "user": user,
        "lage": lage,
        "site": site,
        "org": org,
    })


@router.post("/lage/{lage_id}/stellen/{site_id}/pin", response_class=HTMLResponse)
async def site_pin_save(
    request: Request,
    lage_id: int,
    site_id: int,
    lat: float = Form(...),
    lng: float = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    site.lat = lat
    site.lng = lng
    db.add(SiteLogEntry(
        incident_site_id=site_id,
        kind="status",
        text=f"Koordinaten manuell gesetzt: {lat:.5f}, {lng:.5f}",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    # Auto-Abschnitts-Zuweisung nach Pin-Setzen
    from app.services.geo_service import auto_assign_section
    auto_assign_section(db, site)
    db.commit()
    await broadcast_lage(lage_id, {"type": "site_updated", "site_id": site_id, "reload_board": True})

    vehicles = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == lage.org_id, VehicleMaster.active == True)  # noqa: E712
        .order_by(VehicleMaster.display_order, VehicleMaster.code)
        .all()
    )
    sectors = sorted(lage.sectors, key=lambda s: s.id)
    citizen_report = None
    if site.source == "buerger":
        citizen_report = (
            db.query(CitizenReport)
            .filter(CitizenReport.site_id == site.id)
            .first()
        )
    return templates.TemplateResponse(request, "incident_major/_site_detail.html", {
        "user": user,
        "lage": lage,
        "site": site,
        "phase_labels": PHASE_LABELS,
        "prio_label": SITE_PRIORITY_LABEL,
        "prio_color": SITE_PRIORITY_COLOR,
        "can_edit": _can_edit(user),
        "vehicles": vehicles,
        "sectors": sectors,
        "now": datetime.now(UTC),
        "citizen_report": citizen_report,
    })


# ── Foto hochladen ───────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/medien")
async def site_media_upload(
    request: Request,
    lage_id: int,
    site_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    from app.services.lage_media_service import upload_site_media
    media = await upload_site_media(
        file, site_id,
        org_id=lage.org_id,
        user_id=user.id,
        author_name=get_author_name(request),
        db=db,
    )
    db.add(media)
    db.add(SiteLogEntry(
        incident_site_id=site_id,
        kind="media",
        text=f"Foto hochgeladen: {media.original_filename}",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    db.commit()
    return Response(status_code=204)


# ── Foto ausliefern ──────────────────────────────────────────────────────────

@router.get("/lage-medien/{media_id}")
async def lage_media_serve(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    from app.models.major_incident import SiteMedia as _SiteMedia
    from app.services.lage_media_service import site_media_path
    user = request.state.user
    media = db.get(_SiteMedia, media_id)
    if not media:
        raise HTTPException(status_code=404)
    site = db.get(IncidentSite, media.incident_site_id)
    if not site:
        raise HTTPException(status_code=404)
    lage = db.get(MajorIncident, site.major_incident_id)
    if not lage or not same_org_or_system_admin(user, lage.org_id):
        raise HTTPException(status_code=403)
    path = site_media_path(media)
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/jpeg")


@router.get("/lage-medien/thumb/{media_id}")
async def lage_media_thumb(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    from app.models.major_incident import SiteMedia as _SiteMedia
    from app.services.lage_media_service import site_media_path, site_thumb_path
    user = request.state.user
    media = db.get(_SiteMedia, media_id)
    if not media:
        raise HTTPException(status_code=404)
    site = db.get(IncidentSite, media.incident_site_id)
    if not site:
        raise HTTPException(status_code=404)
    lage = db.get(MajorIncident, site.major_incident_id)
    if not lage or not same_org_or_system_admin(user, lage.org_id):
        raise HTTPException(status_code=403)
    thumb = site_thumb_path(media)
    path = thumb if thumb.exists() else site_media_path(media)
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/jpeg")


# ── Lage beenden (nur manuell) ───────────────────────────────────────────────

@router.post("/lage/{lage_id}/beenden")
async def lage_beenden(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if lage.status == MajorIncidentStatus.closed:
        return RedirectResponse(f"/lage/{lage_id}", status_code=303)

    close_lage(db, lage, closed_by_user_id=user.id)

    from app.models.incident import Incident as _Incident
    from app.services.incident_service import close_incident as _close_incident

    linked_incident_ids = [
        site.incident_id for site in lage.sites if site.incident_id is not None
    ]
    closed_incident_ids: list[int] = []
    if linked_incident_ids:
        active_incidents = (
            db.query(_Incident)
            .filter(
                _Incident.id.in_(linked_incident_ids),
                _Incident.status == "active",
            )
            .all()
        )
        for inc in active_incidents:
            _close_incident(db, inc, user_id=user.id)
            closed_incident_ids.append(inc.id)

    write_audit(db, "major_incident.closed", user_id=user.id,
                payload={"lage_id": lage_id, "name": lage.name,
                         "closed_incidents": len(closed_incident_ids)})
    db.commit()
    await broadcast_lage(lage_id, {"type": "lage_closed", "reload_board": True})
    for iid in closed_incident_ids:
        await manager.broadcast(iid, {"type": "incident_closed"})
    return RedirectResponse(f"/lage/{lage_id}", status_code=303)


# ── Lage editieren ───────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/bearbeiten", response_class=HTMLResponse)
async def lage_bearbeiten_form(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    return templates.TemplateResponse(request, "incident_major/lage_bearbeiten.html", {
        "user": user,
        "lage": lage,
        "can_manage": _can_manage(user),
    })


@router.post("/lage/{lage_id}/bearbeiten")
async def lage_bearbeiten_save(
    request: Request,
    lage_id: int,
    name: str = Form(...),
    description: str = Form(""),
    is_exercise: bool = Form(False),
    auto_adopt: bool = Form(True),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    lage.name = name.strip()[:160]
    lage.description = description.strip() or None
    lage.is_exercise = is_exercise
    lage.auto_adopt = auto_adopt
    db.commit()
    write_audit(db, "major_incident.edited", user_id=user.id,
                payload={"lage_id": lage_id, "name": lage.name})
    await broadcast_lage(lage_id, {"type": "lage_updated", "reload_board": True})
    return RedirectResponse(f"/lage/{lage_id}", status_code=303)


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/dashboard", response_class=HTMLResponse)
async def lage_dashboard(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    import json
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sites_by_phase = _sites_by_phase(lage)
    phase_stats = {p: len(v) for p, v in sites_by_phase.items()}

    prio_stats = {pk: 0 for pk in SitePriority}
    for site in lage.sites:
        if site.phase not in (SitePhase.abgebrochen, SitePhase.erledigt) and site.priority:
            prio_stats[site.priority] += 1

    pending_reports = (
        db.query(CitizenReport)
        .filter(
            CitizenReport.major_incident_id == lage_id,
            CitizenReport.status == "new",
        )
        .count()
    )

    site_logs_raw = (
        db.query(SiteLogEntry, IncidentSite)
        .join(IncidentSite, SiteLogEntry.incident_site_id == IncidentSite.id)
        .filter(IncidentSite.major_incident_id == lage_id)
        .order_by(SiteLogEntry.ts.desc())
        .limit(30)
        .all()
    )

    journal_logs_raw = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts.desc())
        .limit(20)
        .all()
    )

    activity_feed = sorted(
        [{"kind": "site_log", "ts": e.ts, "text": e.text,
          "site": s.bezeichnung, "category": None} for e, s in site_logs_raw]
        + [{"kind": "journal", "ts": e.ts, "text": e.text,
            "site": None, "category": e.category} for e in journal_logs_raw],
        key=lambda x: x["ts"],
        reverse=True,
    )[:40]

    map_sites_json = json.dumps([
        {
            "id": s.id,
            "bezeichnung": s.bezeichnung,
            "lat": s.lat,
            "lng": s.lng,
            "phase": s.phase.value,
            "color": (
                "#ef4444" if SITE_PRIORITY_COLOR.get(s.priority) == "red"
                else "#f97316" if SITE_PRIORITY_COLOR.get(s.priority) == "orange"
                else "#eab308" if SITE_PRIORITY_COLOR.get(s.priority) == "yellow"
                else "#6b7280"
            ),
        }
        for s in lage.sites
        if s.lat and s.lng and s.phase != SitePhase.abgebrochen
    ])

    open_count = sum(
        phase_stats[p]
        for p in [SitePhase.eingegangen, SitePhase.erkundung, SitePhase.bewertet,
                  SitePhase.disponiert, SitePhase.in_arbeit]
    )
    active_res = sum(
        sum(1 for r in s.resources if not r.released_at)
        for s in lage.sites
    )

    sectors_json = json.dumps([{
        "id": s.id,
        "name": s.name,
        "color": s.color or "#6b7280",
        "geometry": json.loads(s.geometry) if s.geometry else None,
    } for s in sorted(lage.sectors, key=lambda s: s.id)])

    return templates.TemplateResponse(request, "incident_major/dashboard.html", {
        "user": user,
        "lage": lage,
        "phase_stats": phase_stats,
        "phase_labels": PHASE_LABELS,
        "prio_stats": prio_stats,
        "prio_label": SITE_PRIORITY_LABEL,
        "prio_color": SITE_PRIORITY_COLOR,
        "activity_feed": activity_feed,
        "map_sites_json": map_sites_json,
        "sectors_json": sectors_json,
        "open_count": open_count,
        "done_count": phase_stats[SitePhase.erledigt],
        "active_res": active_res,
        "total_sites": len(lage.sites),
        "pending_reports": pending_reports,
        "new_meldungen_count": pending_reports,
        "journal_categories": JOURNAL_CATEGORIES,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
    })


# ── Stab ─────────────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/stab", response_class=HTMLResponse)
async def lage_stab(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    from app.services.gsl_staff_service import get_roles, soll_check
    check = soll_check(db, lage_id, lage.org_id)
    roles = get_roles(db, lage.org_id)

    journal_entries = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts.desc())
        .all()
    )

    return templates.TemplateResponse(request, "incident_major/stab.html", {
        "user": user,
        "lage": lage,
        "check": check,
        "roles": roles,
        "journal_entries": journal_entries,
        "journal_categories": JOURNAL_CATEGORIES,
        "journal_category_color": JOURNAL_CATEGORY_COLOR,
        "journal_templates_json": __import__("json").dumps(JOURNAL_TEMPLATES),
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
        **_nav_counts(lage_id, lage, db),
    })


@router.post("/lage/{lage_id}/stab/zuweisen")
async def stab_assign(
    request: Request,
    lage_id: int,
    function: str = Form(...),
    label: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if lage.status != MajorIncidentStatus.active:
        raise HTTPException(status_code=400, detail="Lage nicht aktiv")

    try:
        fn = StaffFunction(function)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unbekannte Funktion: {function}")

    db.add(StaffAssignment(
        major_incident_id=lage_id,
        function=fn,
        label=label.strip(),
        user_id=user.id,
    ))
    db.commit()
    return Response(status_code=204)


@router.post("/lage/{lage_id}/stab/{asgn_id}/freigeben")
async def stab_release(
    request: Request,
    lage_id: int,
    asgn_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    asgn = db.get(StaffAssignment, asgn_id)
    if not asgn or asgn.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    asgn.released_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)


# ── Lage-Journal ──────────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/journal")
async def lage_journal_add(
    request: Request,
    lage_id: int,
    text: str = Form(...),
    category: str = Form("sonstiges"),
    body_html: str = Form(""),
    partner_from: str = Form(""),
    partner_to: str = Form(""),
    measure: str = Form(""),
    media: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if not _can_edit(user):
        raise HTTPException(status_code=403)
    if category not in JOURNAL_CATEGORIES:
        category = "sonstiges"
    entry = LageJournalEntry(
        major_incident_id=lage_id,
        category=category,
        text=text.strip()[:2000],
        body_html=sanitize_html(body_html),
        partner_from=partner_from.strip()[:120] or None,
        partner_to=partner_to.strip()[:120] or None,
        measure=measure.strip()[:500] or None,
        author_name=get_author_name(request),
        user_id=getattr(user, "id", None),
    )
    db.add(entry)
    db.flush()  # get entry.id for media FK
    if media:
        from app.services.lage_media_service import upload_journal_media
        for f in media:
            if not f.filename:
                continue
            m = await upload_journal_media(
                f, entry.id,
                org_id=lage.org_id,
                user_id=getattr(user, "id", None),
                author_name=get_author_name(request),
                db=db,
            )
            db.add(m)
    db.commit()
    await broadcast_lage(lage_id, {"type": "journal_updated"})
    return Response(status_code=204)


@router.get("/lage/{lage_id}/journal/{entry_id}/medien/{media_id}/bild")
async def lage_journal_media_image(
    request: Request,
    lage_id: int,
    entry_id: int,
    media_id: int,
    thumb: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    m = db.get(LageJournalMedia, media_id)
    if not m or m.journal_entry_id != entry_id:
        raise HTTPException(status_code=404)
    from app.services.lage_media_service import journal_media_path, journal_thumb_path
    p = journal_thumb_path(m) if thumb else journal_media_path(m)
    if not p.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(p), media_type="image/jpeg")


@router.post("/lage/{lage_id}/journal/{entry_id}/loeschen")
async def lage_journal_delete(
    request: Request,
    lage_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    entry = db.get(LageJournalEntry, entry_id)
    if not entry or entry.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    db.delete(entry)
    db.commit()
    return Response(status_code=204)


# ── Übergreifende Meldungen (CrossSiteMarker) ────────────────────────────────

@router.post("/lage/{lage_id}/uebergreifend")
async def cross_marker_create(
    request: Request,
    lage_id: int,
    title: str = Form(...),
    marker_type: str = Form("sonstiges"),
    status: str = Form("gemeldet"),
    description: str = Form(""),
    strasse: str = Form(""),
    hausnr: str = Form(""),
    ort: str = Form(""),
    lat: float | None = Form(None),
    lng: float | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if status not in CROSS_MARKER_STATUS_LABEL:
        status = "gemeldet"
    if marker_type not in CROSS_MARKER_TYPE_LABEL:
        marker_type = "sonstiges"
    m = CrossSiteMarker(
        major_incident_id=lage_id,
        org_id=lage.org_id,
        title=title.strip()[:160],
        marker_type=marker_type,
        status=status,
        description=description.strip() or None,
        strasse=strasse.strip()[:160] or None,
        hausnr=hausnr.strip()[:20] or None,
        ort=ort.strip()[:120] or None,
        lat=lat,
        lng=lng,
        created_by=getattr(user, "id", None),
        author_name=get_author_name(request),
    )
    db.add(m)
    db.commit()
    await broadcast_lage(lage_id, {"type": "cross_marker:changed", "marker_id": m.id, "reload_board": False})
    return Response(status_code=204)


@router.get("/lage/{lage_id}/uebergreifend/board-col", response_class=HTMLResponse)
async def cross_marker_board_col(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    can_edit = _can_edit(user)
    markers = sorted(lage.cross_site_markers, key=lambda m: (m.sort_index, m.id))
    return templates.TemplateResponse(request, "incident_major/_cross_marker_col_body.html", {
        "lage": lage,
        "cross_markers": markers,
        "status_label": CROSS_MARKER_STATUS_LABEL,
        "can_edit": can_edit,
    })


@router.post("/lage/{lage_id}/uebergreifend/{mid}/status")
async def cross_marker_set_status(
    request: Request,
    lage_id: int,
    mid: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    m = db.get(CrossSiteMarker, mid)
    if not m or m.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    if status in CROSS_MARKER_STATUS_LABEL:
        m.status = status
        db.commit()
    await broadcast_lage(lage_id, {"type": "cross_marker:changed", "marker_id": mid, "reload_board": False})
    return Response(status_code=204)


@router.post("/lage/{lage_id}/uebergreifend/{mid}/bearbeiten")
async def cross_marker_update(
    request: Request,
    lage_id: int,
    mid: int,
    title: str = Form(...),
    marker_type: str = Form("sonstiges"),
    status: str = Form("gemeldet"),
    description: str = Form(""),
    strasse: str = Form(""),
    hausnr: str = Form(""),
    ort: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    m = db.get(CrossSiteMarker, mid)
    if not m or m.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    m.title = title.strip()[:160]
    if marker_type in CROSS_MARKER_TYPE_LABEL:
        m.marker_type = marker_type
    if status in CROSS_MARKER_STATUS_LABEL:
        m.status = status
    m.description = description.strip() or None
    m.strasse = strasse.strip()[:160] or None
    m.hausnr = hausnr.strip()[:20] or None
    m.ort = ort.strip()[:120] or None
    db.commit()
    await broadcast_lage(lage_id, {"type": "cross_marker:changed", "marker_id": mid, "reload_board": False})
    return Response(status_code=204)


@router.post("/lage/{lage_id}/uebergreifend/{mid}/pin")
async def cross_marker_set_pin(
    request: Request,
    lage_id: int,
    mid: int,
    lat: float = Form(...),
    lng: float = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    m = db.get(CrossSiteMarker, mid)
    if not m or m.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    m.lat = lat
    m.lng = lng
    db.commit()
    await broadcast_lage(lage_id, {"type": "cross_marker:changed", "marker_id": mid, "reload_board": False})
    return Response(status_code=204)


@router.post("/lage/{lage_id}/uebergreifend/{mid}/loeschen")
async def cross_marker_delete(
    request: Request,
    lage_id: int,
    mid: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    m = db.get(CrossSiteMarker, mid)
    if not m or m.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    db.delete(m)
    db.commit()
    await broadcast_lage(lage_id, {"type": "cross_marker:changed", "marker_id": mid, "deleted": True, "reload_board": False})
    return Response(status_code=204)


@router.get("/lage/{lage_id}/uebergreifend/{mid}/panel", response_class=HTMLResponse)
async def cross_marker_panel(
    request: Request,
    lage_id: int,
    mid: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    m = db.get(CrossSiteMarker, mid)
    if not m or m.major_incident_id != lage_id:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "incident_major/_cross_marker_panel.html", {
        "lage": lage,
        "marker": m,
        "type_label": CROSS_MARKER_TYPE_LABEL,
        "type_icon": CROSS_MARKER_TYPE_ICON,
        "status_label": CROSS_MARKER_STATUS_LABEL,
        "status_color": CROSS_MARKER_STATUS_COLOR,
        "can_edit": _can_edit(user),
    })


@router.get("/lage/{lage_id}/karte-cross-markers")
async def lage_karte_cross_markers(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """JSON-API: gibt übergreifende Meldungen für Live-Update der Lagekarte zurück."""
    from fastapi.responses import JSONResponse
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    return JSONResponse([{
        "id": m.id, "title": m.title,
        "marker_type": m.marker_type, "type_icon": m.type_icon, "type_label": m.type_label,
        "status": m.status, "status_label": m.status_label, "status_color": m.status_color,
        "lat": m.lat, "lng": m.lng,
        "ort": m.ort or "", "description": m.description or "",
        "address_line": m.address_line,
    } for m in lage.cross_site_markers if m.lat and m.lng])


# ── Funkjournal ───────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/funkjournal", response_class=HTMLResponse)
async def lage_funkjournal(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    comms = (
        db.query(CommLogEntry)
        .filter(CommLogEntry.major_incident_id == lage_id)
        .order_by(CommLogEntry.ts.desc())
        .limit(200)
        .all()
    )

    sites = [s for s in lage.sites if s.phase != SitePhase.abgebrochen]
    sites_by_id = {s.id: s for s in lage.sites}
    open_requests = sum(1 for c in comms if c.is_request and not c.handled)

    return templates.TemplateResponse(request, "incident_major/funkjournal.html", {
        "user": user,
        "lage": lage,
        "comms": comms,
        "sites": sites,
        "sites_by_id": sites_by_id,
        "open_requests": open_requests,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
        **_nav_counts(lage_id, lage, db),
    })


@router.post("/lage/{lage_id}/funkjournal")
async def funkjournal_add(
    request: Request,
    lage_id: int,
    direction: str = Form(...),
    channel: str = Form(""),
    partner: str = Form(""),
    message: str = Form(...),
    is_request: bool = Form(False),
    related_site_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    if direction not in ("in", "out", "int"):
        raise HTTPException(status_code=400, detail="Ungültige Richtung")

    site_id = related_site_id or None
    db.add(CommLogEntry(
        major_incident_id=lage_id,
        direction=direction,
        channel=channel.strip() or None,
        partner=partner.strip() or None,
        message=message.strip(),
        is_request=is_request,
        related_site_id=site_id,
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    if site_id:
        dir_label = {"in": "↓ Eingehend", "out": "↑ Ausgehend", "int": "↔ Intern"}.get(direction, direction)
        parts = [f"Funkjournal ({dir_label})"]
        if channel.strip():
            parts.append(f"Kanal: {channel.strip()}")
        if partner.strip():
            parts.append(f"Von/An: {partner.strip()}")
        parts.append(message.strip())
        db.add(SiteLogEntry(
            incident_site_id=site_id,
            kind="note",
            text=" – ".join(parts),
            user_id=user.id,
            author_name=get_author_name(request),
        ))
    db.commit()
    return Response(status_code=204)


@router.get("/lage/{lage_id}/meldungen/qr")
async def meldungen_qr(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    import io

    import qrcode  # type: ignore
    import qrcode.image.svg  # type: ignore

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    if not lage.public_token:
        raise HTTPException(status_code=404, detail="Kein Portal-Link aktiv")

    url = str(request.base_url).rstrip("/") + f"/melden/{lage.public_token}"
    factory = qrcode.image.svg.SvgPathImage
    qr = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(image_factory=factory)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@router.post("/lage/{lage_id}/funkjournal/{entry_id}/erledigt")
async def funkjournal_handled(
    request: Request,
    lage_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    entry = db.get(CommLogEntry, entry_id)
    if not entry or entry.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    entry.handled = not entry.handled
    db.commit()
    return Response(status_code=204)


# ── Bürgermeldeportal – Hilfsfunktion ────────────────────────────────────────

async def _save_citizen_photo(file: UploadFile) -> str | None:
    import io
    import uuid
    from pathlib import Path
    data = await file.read()
    if not data:
        return None
    try:
        from PIL import Image, ImageOps  # type: ignore
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        dest = Path("app_storage/citizen_media")
        dest.mkdir(parents=True, exist_ok=True)
        fn = f"{uuid.uuid4().hex}.jpg"
        img.save(dest / fn, "JPEG", quality=85, optimize=True, progressive=True)
        return fn
    except Exception:
        return None


def _mask_phone(phone: str) -> str:
    if len(phone) >= 5:
        return phone[:-3] + "***"
    return "***"


def _cleanup_pending_verifications() -> None:
    now = datetime.now(UTC)
    expired = [k for k, v in _pending_verifications.items() if v["expires_at"] < now]
    for k in expired:
        _pending_verifications.pop(k, None)


# ── Bürgermeldeportal – öffentliche Seiten (kein Auth) ───────────────────────

@router.get("/melden/{token}", response_class=HTMLResponse)
async def buerger_portal(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)
    lage = (
        db.query(MajorIncident)
        .filter(
            MajorIncident.public_token == token,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .first()
    )
    if not lage:
        return HTMLResponse("<h2>Dieser Link ist ungültig oder abgelaufen.</h2>", status_code=404)
    if lage.public_token_expires_at and lage.public_token_expires_at < now:
        return HTMLResponse("<h2>Dieser Link ist abgelaufen.</h2>", status_code=410)

    org = db.get(FireDept, lage.org_id) if lage.org_id else None
    org_logo = (org.logo_path if org and org.logo_path else None) or "/static/img/Logo-rot.png"

    from app.routers.ws import is_sms_gateway_connected
    sms_available = bool(lage.org_id and is_sms_gateway_connected(lage.org_id))

    return templates.TemplateResponse(request, "incident_major/public_report.html", {
        "lage": lage,
        "token": token,
        "org_name": org.name if org else "Feuerwehr",
        "org_logo": org_logo,
        "sms_available": sms_available,
    })


@router.post("/melden/{token}")
async def buerger_submit(
    request: Request,
    token: str,
    description: str = Form(...),
    reporter_name: str = Form(""),
    reporter_contact: str = Form(...),
    ort: str = Form(""),
    strasse: str = Form(""),
    lat: float | None = Form(None),
    lng: float | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)
    lage = (
        db.query(MajorIncident)
        .filter(
            MajorIncident.public_token == token,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .first()
    )
    if not lage:
        raise HTTPException(status_code=404)
    if lage.public_token_expires_at and lage.public_token_expires_at < now:
        raise HTTPException(status_code=410, detail="Link abgelaufen")

    photo_fn: str | None = None
    if file and file.filename:
        photo_fn = await _save_citizen_photo(file)

    source_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.client.host if request.client else None
    )

    # SMS-Verifizierung wenn Gateway verbunden
    from app.routers.ws import is_sms_gateway_connected
    from app.services.sms_service import send_sms

    org = db.get(FireDept, lage.org_id) if lage.org_id else None
    phone = reporter_contact.strip()
    org_name_str = org.name if org else "Feuerwehr"

    gw_connected = bool(lage.org_id and is_sms_gateway_connected(lage.org_id))
    logger.info("Bürger-Portal submit: org_id=%s gateway=%s phone=%s", lage.org_id, gw_connected, _mask_phone(phone))

    if gw_connected:
        _cleanup_pending_verifications()
        pin = f"{random.randint(0, 9999):04d}"
        verify_token = secrets.token_urlsafe(24)
        _pending_verifications[verify_token] = {
            "pin": pin,
            "expires_at": datetime.now(UTC) + timedelta(minutes=10),
            "phone": phone,
            "form_data": {
                "description": description.strip(),
                "reporter_name": reporter_name.strip() or None,
                "reporter_contact": phone or None,
                "ort": ort.strip() or None,
                "strasse": strasse.strip() or None,
                "lat": lat,
                "lng": lng,
            },
            "photo_fn": photo_fn,
            "lage_id": lage.id,
            "source_ip": source_ip,
        }
        sms_ok = await send_sms(lage.org_id, phone, f"Feuerwehr {org_name_str}: Ihr Bestätigungscode lautet {pin}")
        logger.info("Bürger-Portal SMS-Ergebnis: ok=%s phone=%s", sms_ok, _mask_phone(phone))
        if sms_ok:
            return templates.TemplateResponse(request, "incident_major/public_verify.html", {
                "lage": lage,
                "token": token,
                "verify_token": verify_token,
                "phone_masked": _mask_phone(phone),
                "org_name": org_name_str,
            })
        # SMS fehlgeschlagen → Fehlerseite zeigen, nicht still durchfallen
        _pending_verifications.pop(verify_token, None)
        return templates.TemplateResponse(request, "incident_major/public_sms_error.html", {
            "lage": lage,
            "token": token,
            "org_name": org_name_str,
        }, status_code=200)

    db.add(CitizenReport(
        major_incident_id=lage.id,
        reporter_name=reporter_name.strip() or None,
        reporter_contact=phone or None,
        ort=ort.strip() or None,
        strasse=strasse.strip() or None,
        lat=lat,
        lng=lng,
        description=description.strip(),
        photo_filename=photo_fn,
        status="new",
        phone_verified=False,
        source_ip=source_ip,
    ))
    db.commit()
    return templates.TemplateResponse(request, "incident_major/public_danke.html",
                                      {"lage": lage}, status_code=200)


@router.get("/melden/{token}/verify/{verify_token}", response_class=HTMLResponse)
async def buerger_verify_get(
    request: Request,
    token: str,
    verify_token: str,
    db: Session = Depends(get_db),
):
    pending = _pending_verifications.get(verify_token)
    if not pending or pending["expires_at"] < datetime.now(UTC):
        return HTMLResponse("<h2>Dieser Verifizierungslink ist ungültig oder abgelaufen.</h2>", status_code=410)
    lage = db.get(MajorIncident, pending["lage_id"])
    if not lage:
        return HTMLResponse("<h2>Lage nicht gefunden.</h2>", status_code=404)
    return templates.TemplateResponse(request, "incident_major/public_verify.html", {
        "lage": lage,
        "token": token,
        "verify_token": verify_token,
        "phone_masked": _mask_phone(pending["phone"]),
        "org_name": "",
        "error": None,
    })


@router.post("/melden/{token}/verify/{verify_token}")
async def buerger_verify_post(
    request: Request,
    token: str,
    verify_token: str,
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)
    pending = _pending_verifications.get(verify_token)

    if not pending or pending["expires_at"] < now:
        return HTMLResponse(
            "<h2>Dieser Verifizierungslink ist abgelaufen. Bitte erneut versuchen.</h2>",
            status_code=410,
        )

    lage = db.get(MajorIncident, pending["lage_id"])
    if not lage:
        raise HTTPException(status_code=404)

    if pin.strip() != pending["pin"]:
        org_name = ""
        try:
            if lage.org_id:
                org = db.get(FireDept, lage.org_id)
                org_name = org.name if org else ""
        except Exception:
            pass
        return templates.TemplateResponse(request, "incident_major/public_verify.html", {
            "lage": lage,
            "token": token,
            "verify_token": verify_token,
            "phone_masked": _mask_phone(pending["phone"]),
            "org_name": org_name,
            "error": "Falscher PIN. Bitte prüfen Sie Ihre SMS und versuchen Sie es erneut.",
        }, status_code=200)

    # PIN korrekt → Meldung anlegen
    _pending_verifications.pop(verify_token, None)
    fd = pending["form_data"]
    db.add(CitizenReport(
        major_incident_id=lage.id,
        reporter_name=fd.get("reporter_name"),
        reporter_contact=fd.get("reporter_contact"),
        ort=fd.get("ort"),
        strasse=fd.get("strasse"),
        lat=fd.get("lat"),
        lng=fd.get("lng"),
        description=fd["description"],
        photo_filename=pending.get("photo_fn"),
        status="new",
        phone_verified=True,
        source_ip=pending.get("source_ip"),
    ))
    db.commit()
    return templates.TemplateResponse(request, "incident_major/public_danke.html",
                                      {"lage": lage}, status_code=200)


# ── Bürgermeldungen verwalten (Auth) ─────────────────────────────────────────

@router.get("/lage/{lage_id}/meldungen", response_class=HTMLResponse)
async def meldungen_list(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    reports = (
        db.query(CitizenReport)
        .filter(CitizenReport.major_incident_id == lage_id)
        .order_by(CitizenReport.created_at.desc())
        .all()
    )
    new_count = sum(1 for r in reports if r.status == "new")
    open_count = sum(
        1 for s in lage.sites
        if s.phase not in (SitePhase.erledigt, SitePhase.abgebrochen)
    )

    return templates.TemplateResponse(request, "incident_major/meldungen.html", {
        "user": user,
        "lage": lage,
        "reports": reports,
        "new_count": new_count,
        "open_count": open_count,
        "new_meldungen_count": new_count,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "portal_url": str(request.base_url).rstrip("/") + f"/melden/{lage.public_token}"
                      if lage.public_token else None,
        "mi_features": _get_mi_features(db),
    })


@router.post("/lage/{lage_id}/meldungen/token")
async def meldungen_token_gen(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    import secrets
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    lage.public_token = secrets.token_urlsafe(32)
    lage.public_token_expires_at = None  # kein Ablauf
    write_audit(db, "major_incident.token_generated", user_id=user.id,
                payload={"lage_id": lage_id})
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/meldungen", status_code=303)


@router.get("/lage/{lage_id}/meldungen/{report_id}/foto")
async def meldung_foto(
    request: Request,
    lage_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    from pathlib import Path
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    report = db.get(CitizenReport, report_id)
    if not report or report.major_incident_id != lage_id or not report.photo_filename:
        raise HTTPException(status_code=404)

    path = Path("app_storage/citizen_media") / report.photo_filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/jpeg")


@router.post("/lage/{lage_id}/meldungen/{report_id}/annehmen")
async def meldung_annehmen(
    request: Request,
    lage_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    report = db.get(CitizenReport, report_id)
    if not report or report.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    # AI-generierte Kurzbezeichnung aus der Bürgerbeschreibung
    bezeichnung = f"Bürgermeldung – {report.ort or report.strasse or 'unbekannt'}"
    if ai_is_enabled() and report.description:
        try:
            from app.services.ai_service import generate_site_bezeichnung
            ai_bez = await generate_site_bezeichnung(report.description, org_id=lage.org_id)
            if ai_bez:
                bezeichnung = ai_bez
        except Exception:
            pass

    try:
        site = create_site(
            db, lage,
            bezeichnung=bezeichnung,
            einsatzgrund=(report.description or "")[:160] or None,
            ort=report.ort,
            strasse=report.strasse,
            lat=report.lat,
            lng=report.lng,
            created_by=user.id,
            source="buerger",
        )
        await _geocode_site(site)
        await _apply_ai_prio(site, db, org_id=lage.org_id)
        db.add(SiteLogEntry(
            incident_site_id=site.id,
            kind="status",
            text=f"Aus Bürgermeldung #{report.id} erstellt",
            user_id=user.id,
            author_name=get_author_name(request),
        ))
        # Name/Telefon aus Bürgermeldung in Notizen übernehmen
        contact_parts: list[str] = []
        if report.reporter_name:
            contact_parts.append(f"Anrufer: {report.reporter_name.strip()}")
        if report.reporter_contact:
            contact_parts.append(f"Tel.: {report.reporter_contact.strip()}")
        if contact_parts:
            db.add(SiteLogEntry(
                incident_site_id=site.id,
                kind="note",
                text=" · ".join(contact_parts),
                user_id=user.id,
                author_name=get_author_name(request),
            ))
        # Abschnitts-Zuweisung anhand GPS-Koordinaten
        from app.services.geo_service import auto_assign_section
        auto_assign_section(db, site)

        # Bürgermeldungs-Foto auf Einsatzstelle übertragen
        if report.photo_filename:
            from pathlib import Path as _Path
            from app.services.lage_media_service import copy_citizen_photo_to_site
            citizen_photo = _Path("app_storage/citizen_media") / report.photo_filename
            media_obj = copy_citizen_photo_to_site(
                citizen_photo, site.id,
                org_id=lage.org_id,
                user_id=user.id,
                author_name=get_author_name(request),
                db=db,
            )
            if media_obj:
                db.add(media_obj)

        report.status = "accepted"
        report.site_id = site.id
        db.commit()
    except Exception:
        logger.exception("Fehler beim Annehmen der Bürgermeldung lage_id=%s report_id=%s",
                         lage_id, report_id)
        db.rollback()
        raise
    await broadcast_lage(lage_id, {"type": "site_created", "reload_board": True})
    return RedirectResponse(f"/lage/{lage_id}/meldungen", status_code=303)


@router.post("/lage/{lage_id}/meldungen/{report_id}/ablehnen")
async def meldung_ablehnen(
    request: Request,
    lage_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    report = db.get(CitizenReport, report_id)
    if not report or report.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    report.status = "rejected"
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/meldungen", status_code=303)


# ── KI-Lagebericht ────────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/lagebericht", response_class=HTMLResponse)
async def lage_ki_bericht(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    from app.services.ai_service import AIServiceError, generate_situation_brief, is_enabled

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    if not is_enabled():
        return HTMLResponse(
            '<p style="color:var(--text-muted);font-size:.85rem;">KI-Dienst nicht aktiviert.</p>'
        )

    sites_by_phase = _sites_by_phase(lage)
    active_sites = [
        {
            "bezeichnung": s.bezeichnung,
            "einsatzgrund": s.einsatzgrund,
            "phase": PHASE_LABELS[s.phase],
            "priority": SITE_PRIORITY_LABEL.get(s.priority) if s.priority else None,
            "ort": s.ort,
            "active_resources": sum(1 for r in s.resources if not r.released_at),
        }
        for s in lage.sites
        if s.phase not in (SitePhase.abgebrochen, SitePhase.erledigt)
    ]

    context = {
        "lage_name": lage.name,
        "lage_started": lage.started_at.isoformat(),
        "is_exercise": lage.is_exercise,
        "open_sites": len(active_sites),
        "done_sites": len(sites_by_phase[SitePhase.erledigt]),
        "total_active_resources": sum(
            sum(1 for r in s.resources if not r.released_at) for s in lage.sites
        ),
        "sites": active_sites,
    }

    try:
        text = await generate_situation_brief(context, org_id=lage.org_id)
    except AIServiceError as e:
        return HTMLResponse(
            f'<p style="color:#f87171;font-size:.85rem;">{e}</p>'
        )

    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return HTMLResponse(
        f'<p style="font-size:.88rem;line-height:1.6;white-space:pre-wrap;">{safe_text}</p>'
    )


# ── KI-Pressemeldung ─────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/pressemeldung", response_class=HTMLResponse)
async def lage_pressemeldung(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    from app.services.ai_service import generate_pressemeldung, is_enabled

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    if not is_enabled():
        return HTMLResponse(
            '<p style="color:var(--text-muted);font-size:.85rem;">KI-Dienst nicht aktiviert.</p>'
        )

    active_sites = [
        {
            "bezeichnung": s.bezeichnung,
            "einsatzgrund": s.einsatzgrund,
            "phase": PHASE_LABELS.get(s.phase, s.phase.value),
            "ort": s.ort,
        }
        for s in lage.sites
        if s.phase not in (SitePhase.abgebrochen,)
    ]
    context = {
        "lage_name": lage.name,
        "lage_started": lage.started_at.isoformat(),
        "is_exercise": lage.is_exercise,
        "total_sites": len(active_sites),
        "done_sites": sum(1 for s in lage.sites if s.phase == SitePhase.erledigt),
        "sites": active_sites,
    }
    text = await generate_pressemeldung(context, org_id=lage.org_id)
    if not text:
        return HTMLResponse(
            '<p style="color:#f87171;font-size:.85rem;">Pressemeldung konnte nicht erstellt werden.</p>'
        )
    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return HTMLResponse(
        f'<p style="font-size:.88rem;line-height:1.65;white-space:pre-wrap;">{safe_text}</p>'
    )


# ── Zeitreise ────────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/zeitreise", response_class=HTMLResponse)
async def lage_zeitreise(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site_logs = (
        db.query(SiteLogEntry, IncidentSite)
        .join(IncidentSite, SiteLogEntry.incident_site_id == IncidentSite.id)
        .filter(IncidentSite.major_incident_id == lage_id)
        .order_by(SiteLogEntry.ts)
        .all()
    )

    comms = (
        db.query(CommLogEntry)
        .filter(CommLogEntry.major_incident_id == lage_id)
        .order_by(CommLogEntry.ts)
        .all()
    )

    journal = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts)
        .all()
    )

    # Interleave all streams into a single chronological list
    timeline: list[dict] = []
    for entry, site in site_logs:
        timeline.append({
            "ts": entry.ts, "kind": "site_log",
            "log_kind": entry.kind,
            "text": entry.text,
            "site_bezeichnung": site.bezeichnung,
            "site_id": site.id,
            "author": entry.author_name,
        })
    for c in comms:
        label = "↓ Eingehend" if c.direction == "in" else "↑ Ausgehend" if c.direction == "out" else "↔ Intern"
        timeline.append({
            "ts": c.ts, "kind": "comm",
            "direction": c.direction,
            "label": label,
            "channel": c.channel,
            "partner": c.partner,
            "text": c.message,
            "author": c.author_name,
        })
    for j in journal:
        timeline.append({
            "ts": j.ts, "kind": "journal",
            "category": j.category,
            "text": j.text,
            "author": j.author_name,
            "entry_id": j.id,
        })
    timeline.sort(key=lambda x: x["ts"])

    return templates.TemplateResponse(request, "incident_major/zeitreise.html", {
        "user": user,
        "lage": lage,
        "timeline": timeline,
        "journal_categories": JOURNAL_CATEGORIES,
        "journal_category_color": JOURNAL_CATEGORY_COLOR,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
        **_nav_counts(lage_id, lage, db),
    })


# ── Druckansicht ─────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/druck", response_class=HTMLResponse)
async def lage_druck(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sites_by_phase = _sites_by_phase(lage)

    active_sites = [
        s for s in lage.sites
        if s.phase not in (SitePhase.abgebrochen, SitePhase.erledigt)
    ]
    active_sites.sort(key=lambda s: (s.priority.value if s.priority else 99, s.sort_index, s.id))

    done_sites = sites_by_phase.get(SitePhase.erledigt, [])

    active_staff = [a for a in lage.staff if not a.released_at]
    staff_by_fn: dict[StaffFunction, list] = {fn: [] for fn in StaffFunction}
    for a in active_staff:
        staff_by_fn[a.function].append(a)

    comms = (
        db.query(CommLogEntry)
        .filter(CommLogEntry.major_incident_id == lage_id)
        .order_by(CommLogEntry.ts.desc())
        .limit(50)
        .all()
    )

    journal_entries = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts)
        .all()
    )

    return templates.TemplateResponse(request, "incident_major/druck.html", {
        "user": user,
        "lage": lage,
        "active_sites": active_sites,
        "done_sites": done_sites,
        "staff_by_fn": staff_by_fn,
        "staff_fn_label": STAFF_FUNCTION_LABEL,
        "staff_functions": list(StaffFunction),
        "phase_labels": PHASE_LABELS,
        "prio_label": SITE_PRIORITY_LABEL,
        "prio_color": SITE_PRIORITY_COLOR,
        "comms": comms,
        "journal_entries": journal_entries,
        "journal_categories": JOURNAL_CATEGORIES,
        "now": datetime.now(UTC),
    })


# ── Abschnitte / Sektoren ────────────────────────────────────────────────────

SECTOR_COLORS = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#a855f7", "#6b7280"]


@router.get("/lage/{lage_id}/sektoren", response_class=HTMLResponse)
async def sektoren_view(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sectors = sorted(lage.sectors, key=lambda s: s.id)
    sites_by_sector: dict[int | None, list] = {None: []}
    for s in sectors:
        sites_by_sector[s.id] = []
    for site in lage.sites:
        if site.phase not in (SitePhase.abgebrochen,):
            sites_by_sector.setdefault(site.sector_id, []).append(site)

    return templates.TemplateResponse(request, "incident_major/sektoren.html", {
        "user": user,
        "lage": lage,
        "sectors": sectors,
        "sites_by_sector": sites_by_sector,
        "sector_colors": SECTOR_COLORS,
        "phase_labels": PHASE_LABELS,
        "prio_label": SITE_PRIORITY_LABEL,
        "prio_color": SITE_PRIORITY_COLOR,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
        **_nav_counts(lage_id, lage, db),
    })


@router.post("/lage/{lage_id}/sektoren")
async def sektor_create(
    request: Request,
    lage_id: int,
    name: str = Form(...),
    color: str = Form("#6b7280"),
    leader_label: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    safe_color = color[:7] if color.startswith("#") else "#6b7280"
    sector = Sector(
        major_incident_id=lage_id,
        name=name.strip()[:80],
        color=safe_color,
        leader_label=leader_label.strip()[:80] or None,
    )
    db.add(sector)
    db.commit()

    if request.headers.get("accept", "").startswith("application/json"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"id": sector.id, "name": sector.name, "color": sector.color})
    return RedirectResponse(f"/lage/{lage_id}/sektoren", status_code=303)


@router.post("/lage/{lage_id}/sektoren/{sektor_id}/bearbeiten")
async def sektor_edit(
    request: Request,
    lage_id: int,
    sektor_id: int,
    name: str = Form(...),
    color: str = Form("#6b7280"),
    leader_label: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sector = db.get(Sector, sektor_id)
    if not sector or sector.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    sector.name = name.strip()[:80]
    sector.color = color[:7] if color.startswith("#") else "#6b7280"
    sector.leader_label = leader_label.strip()[:80] or None
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/sektoren", status_code=303)


@router.post("/lage/{lage_id}/sektoren/{sektor_id}/loeschen")
async def sektor_delete(
    request: Request,
    lage_id: int,
    sektor_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sector = db.get(Sector, sektor_id)
    if not sector or sector.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    db.delete(sector)
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/sektoren", status_code=303)


@router.post("/lage/{lage_id}/stellen/{site_id}/sektor")
async def site_sektor_assign(
    request: Request,
    lage_id: int,
    site_id: int,
    sector_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    site.sector_id = sector_id or None
    site.section_assigned_mode = "manual"  # manuell → sticky
    db.commit()
    await broadcast_lage(lage_id, {
        "type": "site:sector_changed",
        "site_id": site_id,
        "sector_id": site.sector_id,
        "reload_board": False,
    })
    return Response(status_code=204)


# ── Abschnitt-Polygon-API ──────────────────────────────────────────────────────

@router.put("/lage/{lage_id}/sektoren/{sektor_id}/geometrie")
async def sektor_geometry_update(
    request: Request,
    lage_id: int,
    sektor_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    """Speichert GeoJSON-Polygon eines Abschnitts und löst Auto-Zuweisung aus."""
    import json as _json
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sector = db.get(Sector, sektor_id)
    if not sector or sector.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    data = await request.json()
    geometry = data.get("geometry")  # GeoJSON Polygon oder null

    if geometry is not None:
        from app.services.geo_service import validate_geojson_polygon
        err = validate_geojson_polygon(geometry)
        if err:
            raise HTTPException(status_code=400, detail=err)
        sector.geometry = _json.dumps(geometry)
    else:
        sector.geometry = None

    db.commit()

    # Auto-Zuweisung neu berechnen (inkl. manuell zugewiesener Stellen)
    from app.services.geo_service import bulk_reassign_section
    reassigned = bulk_reassign_section(db, lage_id, include_manual=True)
    if reassigned:
        db.commit()

    await broadcast_lage(lage_id, {
        "type": "section:changed",
        "sector_id": sektor_id,
        "reassigned": reassigned,
        "reload_board": False,
    })
    from fastapi.responses import JSONResponse
    return JSONResponse({"geometry": geometry, "reassigned": reassigned})


@router.delete("/lage/{lage_id}/sektoren/{sektor_id}/geometrie")
async def sektor_geometry_delete(
    request: Request,
    lage_id: int,
    sektor_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    sector = db.get(Sector, sektor_id)
    if not sector or sector.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    sector.geometry = None

    # Stellen ohne Polygon → "kein Abschnitt"
    from app.services.geo_service import bulk_reassign_section
    bulk_reassign_section(db, lage_id)
    db.commit()

    await broadcast_lage(lage_id, {"type": "section:changed", "sector_id": sektor_id, "reload_board": False})
    return Response(status_code=204)


# ── Einsatz-Detail-Panel (Karte) ───────────────────────────────────────────────

@router.get("/lage/{lage_id}/stellen/{site_id}/panel", response_class=HTMLResponse)
async def site_panel(
    request: Request,
    lage_id: int,
    site_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    sectors = sorted(lage.sectors, key=lambda s: s.id)

    return templates.TemplateResponse(request, "incident_major/_site_panel.html", {
        "user": user,
        "lage": lage,
        "site": site,
        "sectors": sectors,
        "phase_labels": PHASE_LABELS,
        "phase_order": PHASE_ORDER,
        "prio_color": SITE_PRIORITY_COLOR,
        "prio_label": SITE_PRIORITY_LABEL,
        "can_edit": _can_edit(user),
    })


# ── Fahrzeugpositionen (Karten-API) ───────────────────────────────────────────

@router.get("/lage/{lage_id}/fahrzeuge/positionen")
async def vehicle_positions(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """Gibt aktuelle Fahrzeugpositionen (letzte je Fahrzeug) als JSON zurück."""
    from datetime import timedelta

    from app.models.major_incident import VehiclePosition
    from app.models.master import OrgSettings, VehicleMaster

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    settings = db.query(OrgSettings).filter(OrgSettings.org_id == lage.org_id).first()
    stale_minutes = (settings.vehicle_stale_minutes if settings else 5) or 5

    now = datetime.now(UTC)
    stale_threshold = now - timedelta(minutes=stale_minutes)

    # Letzte Position je Fahrzeug aus der Historie
    from sqlalchemy import func
    subq = (
        db.query(
            VehiclePosition.vehicle_id,
            func.max(VehiclePosition.received_at).label("last_received"),
        )
        .filter(
            VehiclePosition.incident_id == lage_id,
            VehiclePosition.vehicle_id.isnot(None),
        )
        .group_by(VehiclePosition.vehicle_id)
        .subquery()
    )
    positions = (
        db.query(VehiclePosition)
        .join(subq, (VehiclePosition.vehicle_id == subq.c.vehicle_id) &
                    (VehiclePosition.received_at == subq.c.last_received))
        .all()
    )

    # Zusätzlich: manuelle Pins (source=manual) der letzten 24h ohne GPS-Duplikat
    manual_positions = (
        db.query(VehiclePosition)
        .filter(
            VehiclePosition.incident_id == lage_id,
            VehiclePosition.vehicle_id.is_(None),
            VehiclePosition.source == "manual",
        )
        .order_by(VehiclePosition.received_at.desc())
        .all()
    )

    result = []
    for p in positions:
        vehicle = db.get(VehicleMaster, p.vehicle_id) if p.vehicle_id else None
        ts = p.received_at.replace(tzinfo=UTC) if p.received_at.tzinfo is None else p.received_at
        is_stale = ts < stale_threshold
        result.append({
            "id": p.id,
            "vehicle_id": p.vehicle_id,
            "label": vehicle.code if vehicle else p.resource_label or "?",
            "name": vehicle.name if vehicle else (p.resource_label or ""),
            "lat": p.lat,
            "lng": p.lon,
            "source": p.source,
            "is_stale": is_stale,
            "ts": p.received_at.isoformat() if p.received_at else None,
        })

    for p in manual_positions:
        result.append({
            "id": p.id,
            "vehicle_id": None,
            "label": p.resource_label or "?",
            "name": p.resource_label or "",
            "lat": p.lat,
            "lng": p.lon,
            "source": "manual",
            "is_stale": False,
            "ts": p.received_at.isoformat() if p.received_at else None,
        })

    from fastapi.responses import JSONResponse
    return JSONResponse(result)


@router.post("/lage/{lage_id}/fahrzeuge/manuell")
async def vehicle_manual_pin(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    """Setzt einen manuellen Fahrzeug-Pin (Ressource ohne GPS)."""
    from app.models.major_incident import VehiclePosition

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    data = await request.json()
    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat/lng fehlen")

    label = (data.get("label") or "").strip()[:120] or None
    vehicle_id = data.get("vehicle_id")

    now = datetime.now(UTC)
    db.add(VehiclePosition(
        incident_id=lage_id,
        org_id=lage.org_id,
        vehicle_id=vehicle_id,
        resource_label=label,
        lat=lat,
        lon=lng,
        source="manual",
        recorded_at=now,
        received_at=now,
        reported_by=user.id,
    ))
    db.commit()

    await broadcast_lage(lage_id, {
        "type": "vehicle:position",
        "vehicle_id": vehicle_id,
        "label": label or str(vehicle_id or "?"),
        "lat": lat,
        "lng": lng,
        "source": "manual",
        "ts": now.isoformat(),
    })
    from fastapi.responses import JSONResponse
    return JSONResponse({"ok": True})


# ── Lagekarte ────────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/karte", response_class=HTMLResponse)
async def lage_karte(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    import json
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    def _site_color(s: IncidentSite) -> str:
        c = SITE_PRIORITY_COLOR.get(s.priority) if s.priority else None
        if c == "red":
            return "#ef4444"
        if c == "orange":
            return "#f97316"
        if c == "yellow":
            return "#eab308"
        if s.phase == SitePhase.erledigt:
            return "#22c55e"
        return "#6b7280"

    active_sites = [s for s in lage.sites if s.phase != SitePhase.abgebrochen]
    map_sites_json = json.dumps([{
        "id": s.id,
        "bezeichnung": s.bezeichnung,
        "einsatzgrund": s.einsatzgrund or "",
        "lat": s.lat,
        "lng": s.lng,
        "phase": s.phase.value,
        "phase_label": PHASE_LABELS.get(s.phase, s.phase.value),
        "priority_label": SITE_PRIORITY_LABEL.get(s.priority, "") if s.priority else "",
        "color": _site_color(s),
        "active_res": sum(1 for r in s.resources if not r.released_at),
        "sector_id": s.sector_id,
        "incident_id": s.incident_id,
    } for s in active_sites if s.lat and s.lng])

    sectors = sorted(lage.sectors, key=lambda s: s.id)
    sectors_by_id = {s.id: s for s in sectors}
    sectors_json = json.dumps([{
        "id": s.id,
        "name": s.name,
        "color": s.color or "#6b7280",
        "geometry": json.loads(s.geometry) if s.geometry else None,
    } for s in sectors])

    from app.core.timezones import format_local_datetime
    org = getattr(user, "org", None)
    citizen_reports_raw = (
        db.query(CitizenReport)
        .filter(
            CitizenReport.major_incident_id == lage_id,
            CitizenReport.lat.isnot(None),
            CitizenReport.lng.isnot(None),
            CitizenReport.status == "new",
        )
        .order_by(CitizenReport.created_at)
        .all()
    )
    citizen_reports_json = json.dumps([{
        "id": r.id,
        "lat": r.lat,
        "lng": r.lng,
        "ort": r.ort or r.strasse or "",
        "description": r.description[:120],
        "reporter": r.reporter_name or "Anonym",
        "ts": format_local_datetime(r.created_at, org),
    } for r in citizen_reports_raw])

    cross_markers_json = json.dumps([{
        "id": m.id, "title": m.title,
        "marker_type": m.marker_type, "type_icon": m.type_icon, "type_label": m.type_label,
        "status": m.status, "status_label": m.status_label, "status_color": m.status_color,
        "lat": m.lat, "lng": m.lng,
        "ort": m.ort or "", "description": m.description or "",
        "address_line": m.address_line,
    } for m in lage.cross_site_markers if m.lat and m.lng])

    return templates.TemplateResponse(request, "incident_major/karte.html", {
        "user": user,
        "lage": lage,
        "map_sites_json": map_sites_json,
        "sectors_json": sectors_json,
        "sectors": sectors,
        "sectors_by_id": sectors_by_id,
        "all_sites": active_sites,
        "citizen_reports_json": citizen_reports_json,
        "reports_count": len(citizen_reports_raw),
        "cross_markers_json": cross_markers_json,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
        **_nav_counts(lage_id, lage, db),
    })


@router.get("/lage/{lage_id}/karte-sektoren")
async def lage_karte_sektoren(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """JSON-API: gibt aktuelle Sektoren für Live-Update der Lagekarte zurück."""
    import json

    from fastapi.responses import JSONResponse
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    sectors = sorted(lage.sectors, key=lambda s: s.id)
    return JSONResponse([{
        "id": s.id,
        "name": s.name,
        "color": s.color or "#6b7280",
        "geometry": json.loads(s.geometry) if s.geometry else None,
    } for s in sectors])


@router.get("/lage/{lage_id}/karte-sites")
async def lage_karte_sites(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """JSON-API: gibt aktuelle Sektorzuordnungen der Einsatzstellen zurück."""
    from fastapi.responses import JSONResponse
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    return JSONResponse([{"id": s.id, "sector_id": s.sector_id} for s in lage.sites])


# ── Ressourcenübersicht ───────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/ressourcen", response_class=HTMLResponse)
async def lage_ressourcen(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    # Auto-populate Einheiten from org vehicles on first access
    if not lage.einheiten:
        org_vehicles = (
            db.query(VehicleMaster)
            .filter(VehicleMaster.dept_id == lage.org_id, VehicleMaster.active == True)  # noqa: E712
            .order_by(VehicleMaster.display_order, VehicleMaster.code)
            .all()
        )
        for v in org_vehicles:
            db.add(LageEinheit(
                lage_id=lage.id,
                vehicle_id=v.id,
                label=v.display_label,
                is_from_org=True,
            ))
        if org_vehicles:
            db.commit()
            db.refresh(lage)

    einheiten = sorted(lage.einheiten, key=lambda e: (
        0 if e.status == "verfuegbar" else 1 if e.status == "eingesetzt" else 2,
        e.label,
    ))

    active_sites = [s for s in lage.sites if s.phase != SitePhase.abgebrochen]

    all_res = [
        (r, site)
        for site in active_sites
        for r in site.resources
        if not r.released_at
    ]
    all_res.sort(key=lambda x: (0 if x[0].committed_at else 1, x[0].label or x[0].resource_type or ""))

    vid_list = [r.vehicle_id for r, _ in all_res if r.vehicle_id]
    conflict_vids = {vid for vid in vid_list if vid_list.count(vid) > 1}

    released = sorted(
        [(r, site) for site in lage.sites for r in site.resources if r.released_at],
        key=lambda x: x[0].released_at,
        reverse=True,
    )[:30]

    total_alarmed = sum(1 for r, _ in all_res if not r.committed_at)
    total_committed = sum(1 for r, _ in all_res if r.committed_at)

    extra_vehicles = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == lage.org_id, VehicleMaster.active == True)  # noqa: E712
        .order_by(VehicleMaster.display_order, VehicleMaster.code)
        .all()
    )

    org_members = (
        db.query(Member)
        .filter(Member.org_id == lage.org_id, Member.active == True)  # noqa: E712
        .order_by(Member.lastname, Member.firstname)
        .all()
    )

    return templates.TemplateResponse(request, "incident_major/ressourcen.html", {
        "user": user,
        "lage": lage,
        "einheiten": einheiten,
        "extra_vehicles": extra_vehicles,
        "org_members": org_members,
        "all_res": all_res,
        "conflict_vids": conflict_vids,
        "released": released,
        "total_alarmed": total_alarmed,
        "total_committed": total_committed,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": _get_mi_features(db),
        **_nav_counts(lage_id, lage, db),
    })


# ── Einheiten-Pool ────────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/einheiten")
async def lage_einheit_create(
    request: Request,
    lage_id: int,
    label: str = Form(...),
    vehicle_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    actual_label = label.strip()
    if vehicle_id and not actual_label:
        vehicle = db.get(VehicleMaster, vehicle_id)
        if vehicle:
            actual_label = vehicle.display_label
    if not actual_label:
        raise HTTPException(status_code=400, detail="Bezeichnung fehlt")

    db.add(LageEinheit(
        lage_id=lage_id,
        vehicle_id=vehicle_id,
        label=actual_label,
        is_from_org=False,
    ))
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/ressourcen", status_code=303)


@router.post("/lage/{lage_id}/einheiten/{einheit_id}/kommandant")
async def lage_einheit_kommandant(
    request: Request,
    lage_id: int,
    einheit_id: int,
    commander_label: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    einheit = db.get(LageEinheit, einheit_id)
    if not einheit or einheit.lage_id != lage_id:
        raise HTTPException(status_code=404)

    einheit.commander_label = commander_label.strip() or None
    db.commit()
    return Response(status_code=204)


@router.post("/lage/{lage_id}/einheiten/{einheit_id}/status")
async def lage_einheit_status(
    request: Request,
    lage_id: int,
    einheit_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    einheit = db.get(LageEinheit, einheit_id)
    if not einheit or einheit.lage_id != lage_id:
        raise HTTPException(status_code=404)

    if status not in ("verfuegbar", "eingesetzt", "abgezogen"):
        raise HTTPException(status_code=400)

    einheit.status = status
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/ressourcen", status_code=303)


@router.post("/lage/{lage_id}/einheiten/{einheit_id}/loeschen")
async def lage_einheit_delete(
    request: Request,
    lage_id: int,
    einheit_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    einheit = db.get(LageEinheit, einheit_id)
    if not einheit or einheit.lage_id != lage_id:
        raise HTTPException(status_code=404)

    db.delete(einheit)
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/ressourcen", status_code=303)


# ── Protokoll-Export ──────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/protokoll.txt")
async def lage_protokoll_export(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    from app.core.timezones import format_local_datetime
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    org = getattr(user, "org", None)

    site_logs = (
        db.query(SiteLogEntry, IncidentSite)
        .join(IncidentSite, SiteLogEntry.incident_site_id == IncidentSite.id)
        .filter(IncidentSite.major_incident_id == lage_id)
        .order_by(SiteLogEntry.ts)
        .all()
    )
    comms = (
        db.query(CommLogEntry)
        .filter(CommLogEntry.major_incident_id == lage_id)
        .order_by(CommLogEntry.ts)
        .all()
    )

    journal_exp = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts)
        .all()
    )

    direction_map = {"in": "Eingehend", "out": "Ausgehend", "int": "Intern"}
    entries: list[dict] = []
    for entry, site in site_logs:
        entries.append({
            "ts": entry.ts,
            "kind": "stelle",
            "site": site.bezeichnung,
            "text": entry.text,
            "author": entry.author_name,
        })
    for c in comms:
        entries.append({
            "ts": c.ts,
            "kind": "funk",
            "direction": direction_map.get(c.direction, c.direction),
            "channel": c.channel or "",
            "partner": c.partner or "",
            "text": c.message,
            "author": c.author_name,
        })
    for j in journal_exp:
        entries.append({
            "ts": j.ts,
            "kind": "journal",
            "category": JOURNAL_CATEGORIES.get(j.category, j.category),
            "text": j.text,
            "author": j.author_name,
        })
    entries.sort(key=lambda x: x["ts"])

    lines: list[str] = [
        f"EINSATZPROTOKOLL – {lage.name}",
        f"Exportiert: {format_local_datetime(datetime.now(UTC), org)}",
        "=" * 60,
        "",
    ]
    for e in entries:
        ts = format_local_datetime(e["ts"], org)
        author_tag = f"  [{e['author']}]" if e.get("author") else ""
        if e["kind"] == "stelle":
            lines.append(f"{ts}  [STELLE]  {e['site']}")
            lines.append(f"              {e['text']}{author_tag}")
        elif e["kind"] == "journal":
            lines.append(f"{ts}  [JOURNAL] {e['category']}")
            lines.append(f"              {e['text']}{author_tag}")
        else:
            parts = [e["direction"]]
            if e["channel"]:
                parts.append(e["channel"])
            if e["partner"]:
                parts.append(e["partner"])
            lines.append(f"{ts}  [FUNK]    {' · '.join(parts)}")
            lines.append(f"              {e['text']}{author_tag}")
        lines.append("")

    if not entries:
        lines.append("(Keine Ereignisse.)")

    text = "\n".join(lines)
    safe_name = lage.name[:40].replace(" ", "_").replace("/", "-")
    filename = f"protokoll_{safe_name}.txt"
    return PlainTextResponse(
        content=text,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
