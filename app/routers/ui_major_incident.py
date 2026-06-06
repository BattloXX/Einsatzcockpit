"""UI-Router: Großschadenslage – Phasen-Board, Stellen-CRUD, Abschluss."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role, require_role, same_org_or_system_admin
from app.core.security import get_author_name
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import (
    CitizenReport,
    CommLogEntry,
    IncidentSite,
    LageJournalEntry,
    Sector,
    MajorIncident,
    MajorIncidentStatus,
    SiteLogEntry,
    SitePhase,
    SitePriority,
    SiteResourceAssignment,
    StaffAssignment,
    JOURNAL_CATEGORIES,
    JOURNAL_CATEGORY_COLOR,
    SITE_PRIORITY_COLOR,
    SITE_PRIORITY_LABEL,
    STAFF_FUNCTION_LABEL,
    StaffFunction,
)
from app.models.master import VehicleMaster
from app.services.broadcast import broadcast_lage
from app.services.major_incident_service import (
    close_lage,
    create_lage,
    create_site,
    get_active_lage,
)

router = APIRouter()

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


# ── Navigation: aktive Lage der Org ─────────────────────────────────────────

@router.get("/lage", response_class=HTMLResponse)
async def lage_overview(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    org_id = getattr(user, "org_id", None)
    if not org_id:
        raise HTTPException(status_code=403)

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
        "show_abgebrochen": show_abgebrochen,
        "abgebrochen_sites": abgebrochen_sites,
        "sectors": sectors,
        "sectors_by_id": sectors_by_id,
        "now": datetime.now(UTC),
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
    db.add(SiteLogEntry(
        incident_site_id=site.id,
        kind="status",
        text="Einsatzstelle angelegt",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    write_audit(db, "major_incident.site.created", user_id=user.id,
                payload={"lage_id": lage_id, "site_id": site.id, "bezeichnung": site.bezeichnung})
    db.commit()
    await broadcast_lage(lage_id, {"type": "site_created", "reload_board": True})
    return RedirectResponse(f"/lage/{lage_id}", status_code=303)


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
    priority: int = Form(...),
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
        new_prio = SitePriority(priority)
    except ValueError:
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


# ── KI-Erkundungsassistent ───────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stellen/{site_id}/ki-erkundung", response_class=HTMLResponse)
async def site_ki_erkundung(
    request: Request,
    lage_id: int,
    site_id: int,
    erkundungstext: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise HTTPException(status_code=404)

    from app.services.ai_service import analyze_site_reconnaissance
    result = await analyze_site_reconnaissance(
        erkundungstext,
        {"bezeichnung": site.bezeichnung, "ort": site.ort or "", "strasse": site.strasse or ""},
    )

    if not result:
        return HTMLResponse(
            '<p style="color:#f87171;font-size:.82rem;margin-top:8px;">KI-Analyse nicht verfügbar.</p>'
        )

    prio_colors = {1: "#f87171", 2: "#fb923c", 3: "#facc15", 4: "#9ca3af"}
    prio_labels = {1: "Sofort", 2: "Dringend", 3: "Normal", 4: "Aufschiebbar"}
    pv = result.get("prio_vorschlag", 3)
    csrf = request.state.csrf_token

    html = f"""
<div style="margin-top:12px;padding:10px 12px;background:rgba(139,92,246,.08);
border:1px solid rgba(139,92,246,.25);border-radius:6px;font-size:.82rem;">
  <div style="font-size:.72rem;font-weight:700;color:#a78bfa;margin-bottom:8px;letter-spacing:.04em;">
    ✨ KI-ERKUNDUNGSANALYSE
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;margin-bottom:10px;">
    <div><span style="color:var(--text-muted);font-size:.72rem;">Einsatzgrund</span><br>
      <strong>{result.get('einsatzgrund','–')}</strong></div>
    <div><span style="color:var(--text-muted);font-size:.72rem;">Gefahr</span><br>
      {result.get('gefahr','–')}</div>
    <div><span style="color:var(--text-muted);font-size:.72rem;">Benötigte Mittel</span><br>
      {result.get('benoetigte_mittel','–')}</div>
    <div><span style="color:var(--text-muted);font-size:.72rem;">Prio-Vorschlag</span><br>
      <strong style="color:{prio_colors.get(pv,'#fff')};">{prio_labels.get(pv,'–')}</strong>
      &nbsp;(Gefahr {result.get('danger_score','?')}/4 · Dringlichkeit {result.get('urgency_score','?')}/4)
    </div>
  </div>
  <div style="color:var(--text-muted);margin-bottom:10px;font-size:.8rem;line-height:1.5;">
    {result.get('zusammenfassung','').replace(chr(10),'<br>')}
  </div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;">
    <form hx-post="/lage/{lage_id}/stellen/{site_id}/prio"
          hx-swap="none" hx-vals='{{"_csrf":"{csrf}"}}'>
      <input type="hidden" name="priority" value="{pv}">
      <button type="submit" class="btn btn--secondary btn--sm">Prio {prio_labels.get(pv,'')} übernehmen</button>
    </form>
    <form hx-post="/lage/{lage_id}/stellen/{site_id}/log"
          hx-swap="none"
          hx-vals='{{"_csrf":"{csrf}","text":"KI-Erkundung: {result.get('zusammenfassung','').replace(chr(34),chr(39))[:200]}"}}'>
      <button type="submit" class="btn btn--ghost btn--sm">Als Notiz speichern</button>
    </form>
  </div>
</div>"""
    return HTMLResponse(html)


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
        user_id=user.id,
        author_name=get_author_name(request),
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
    from app.services.lage_media_service import site_thumb_path
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
    write_audit(db, "major_incident.closed", user_id=user.id,
                payload={"lage_id": lage_id, "name": lage.name})
    db.commit()
    await broadcast_lage(lage_id, {"type": "lage_closed", "reload_board": True})
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

    recent_logs = (
        db.query(SiteLogEntry, IncidentSite)
        .join(IncidentSite, SiteLogEntry.incident_site_id == IncidentSite.id)
        .filter(IncidentSite.major_incident_id == lage_id)
        .order_by(SiteLogEntry.ts.desc())
        .limit(40)
        .all()
    )

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

    return templates.TemplateResponse(request, "incident_major/dashboard.html", {
        "user": user,
        "lage": lage,
        "phase_stats": phase_stats,
        "phase_labels": PHASE_LABELS,
        "prio_stats": prio_stats,
        "prio_label": SITE_PRIORITY_LABEL,
        "prio_color": SITE_PRIORITY_COLOR,
        "recent_logs": recent_logs,
        "map_sites_json": map_sites_json,
        "open_count": open_count,
        "done_count": phase_stats[SitePhase.erledigt],
        "active_res": active_res,
        "total_sites": len(lage.sites),
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
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

    staff_by_fn: dict[StaffFunction, list] = {fn: [] for fn in StaffFunction}
    for s in lage.staff:
        if not s.released_at:
            staff_by_fn[s.function].append(s)

    journal_entries = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts.desc())
        .all()
    )

    return templates.TemplateResponse(request, "incident_major/stab.html", {
        "user": user,
        "lage": lage,
        "staff_by_fn": staff_by_fn,
        "staff_fn_label": STAFF_FUNCTION_LABEL,
        "staff_functions": list(StaffFunction),
        "journal_entries": journal_entries,
        "journal_categories": JOURNAL_CATEGORIES,
        "journal_category_color": JOURNAL_CATEGORY_COLOR,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
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
    db.add(LageJournalEntry(
        major_incident_id=lage_id,
        category=category,
        text=text.strip()[:2000],
        author_name=get_author_name(request),
        user_id=getattr(user, "id", None),
    ))
    db.commit()
    await broadcast_lage(lage_id, {"type": "journal_updated"})
    return Response(status_code=204)


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

    return templates.TemplateResponse(request, "incident_major/funkjournal.html", {
        "user": user,
        "lage": lage,
        "comms": comms,
        "sites": sites,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
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

    db.add(CommLogEntry(
        major_incident_id=lage_id,
        direction=direction,
        channel=channel.strip() or None,
        partner=partner.strip() or None,
        message=message.strip(),
        is_request=is_request,
        related_site_id=related_site_id or None,
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
    import io, uuid
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

    return templates.TemplateResponse(request, "incident_major/public_report.html", {
        "lage": lage, "token": token,
    })


@router.post("/melden/{token}")
async def buerger_submit(
    request: Request,
    token: str,
    description: str = Form(...),
    reporter_name: str = Form(""),
    reporter_contact: str = Form(""),
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

    db.add(CitizenReport(
        major_incident_id=lage.id,
        reporter_name=reporter_name.strip() or None,
        reporter_contact=reporter_contact.strip() or None,
        ort=ort.strip() or None,
        strasse=strasse.strip() or None,
        lat=lat,
        lng=lng,
        description=description.strip(),
        photo_filename=photo_fn,
        status="new",
        source_ip=source_ip,
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

    return templates.TemplateResponse(request, "incident_major/meldungen.html", {
        "user": user,
        "lage": lage,
        "reports": reports,
        "new_count": new_count,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "portal_url": str(request.base_url).rstrip("/") + f"/melden/{lage.public_token}"
                      if lage.public_token else None,
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

    site = create_site(
        db, lage,
        bezeichnung=f"Bürgermeldung – {report.ort or report.strasse or 'unbekannt'}",
        einsatzgrund=report.description[:160],
        ort=report.ort,
        strasse=report.strasse,
        lat=report.lat,
        lng=report.lng,
        created_by=user.id,
        source="buerger",
    )
    await _geocode_site(site)
    db.add(SiteLogEntry(
        incident_site_id=site.id,
        kind="status",
        text=f"Aus Bürgermeldung #{report.id} erstellt",
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    report.status = "accepted"
    report.site_id = site.id
    db.commit()
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
        text = await generate_situation_brief(context)
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
    text = await generate_pressemeldung(context)
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

    db.add(Sector(
        major_incident_id=lage_id,
        name=name.strip()[:80],
        color=color[:7] if color.startswith("#") else "#6b7280",
        leader_label=leader_label.strip()[:80] or None,
    ))
    db.commit()
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
    db.commit()
    await broadcast_lage(lage_id, {"type": "site_updated", "site_id": site_id, "reload_board": True})
    return Response(status_code=204)


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
        if c == "red":    return "#ef4444"
        if c == "orange": return "#f97316"
        if c == "yellow": return "#eab308"
        if s.phase == SitePhase.erledigt: return "#22c55e"
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
    } for s in active_sites if s.lat and s.lng])

    sectors = sorted(lage.sectors, key=lambda s: s.id)
    sectors_by_id = {s.id: s for s in sectors}
    sectors_json = json.dumps([{
        "id": s.id,
        "name": s.name,
        "color": s.color or "#6b7280",
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
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
    })


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

    return templates.TemplateResponse(request, "incident_major/ressourcen.html", {
        "user": user,
        "lage": lage,
        "all_res": all_res,
        "conflict_vids": conflict_vids,
        "released": released,
        "total_alarmed": total_alarmed,
        "total_committed": total_committed,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
    })


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
