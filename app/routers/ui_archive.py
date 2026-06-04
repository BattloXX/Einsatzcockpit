"""Archiv & PDF-Export.

Org-Scoping:
- Listen und Detailansichten werden nach Org gefiltert; system_admin sieht alles.
- Endpoints können nur eigene oder mitwirkende Org-Einsätze abrufen.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import can_access_incident, has_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, IncidentOrg
from app.services.ai_service import AIServiceError, generate_report_draft
from app.services.ai_service import is_enabled as ai_is_enabled
from app.services.pdf_service import render_incident_pdf

router = APIRouter()
logger = logging.getLogger("einsatzleiter.archive")


def _load_incident_with_orgs(incident_id: int, db: Session) -> Incident | None:
    """Lädt Incident und stellt sicher, dass collaborating_orgs eager geladen ist,
    damit can_access_incident() nicht in ein Lazy-Load-Problem läuft."""
    return (
        db.query(Incident)
        .options(selectinload(Incident.collaborating_orgs))
        .filter(Incident.id == incident_id)
        .first()
    )


def _deny_access(user, incident) -> HTTPException:
    """Erzeugt eine 403 mit diagnostischem Hinweis (welche Orgs verglichen wurden)."""
    collab_ids = [io.org_id for io in (incident.collaborating_orgs or [])]
    logger.info(
        "access denied: user=%s org=%s incident=%s primary_org=%s collaborators=%s",
        user.id, user.org_id, incident.id, incident.primary_org_id, collab_ids,
    )
    msg = (
        f"Kein Zugriff auf diesen Einsatz. Dein Account gehört zu Org "
        f"{user.org_id}, der Einsatz zur Org {incident.primary_org_id}. "
        f"Bitte als Mit-Organisation eintragen lassen (Admin)."
    )
    return HTTPException(403, detail=msg)


def _scoped_incidents_query(db: Session, user):
    """Liefert eine Incident-Query, die nur Einsätze enthält, die der User sehen darf."""
    q = db.query(Incident)
    user_role_codes = {r.code for r in user.roles}
    if "system_admin" in user_role_codes:
        return q
    if user.org_id is None:
        # Kein Org, kein system_admin → keine Einsätze
        return q.filter(Incident.id == None)  # noqa: E711  → leeres Resultset
    collab_ids_subq = db.query(IncidentOrg.incident_id).filter(
        IncidentOrg.org_id == user.org_id
    )
    return q.filter(
        or_(
            Incident.primary_org_id == user.org_id,
            Incident.id.in_(collab_ids_subq),
        )
    )


_AI_ROLES = ("incident_leader", "recorder", "org_admin", "system_admin")


@router.get("/archiv", response_class=HTMLResponse)
async def archive_list(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incidents = _scoped_incidents_query(db, user).order_by(Incident.started_at.desc()).all()
    return templates.TemplateResponse(request, "archive/list.html", {
        "user": user, "incidents": incidents,
    })


@router.get("/archiv/{incident_id}", response_class=HTMLResponse)
async def archive_detail(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons",
                           "breathing_troops", "log_entries"])
    return templates.TemplateResponse(request, "archive/detail.html", {
        "user": user, "incident": incident,
        "ai_enabled": ai_is_enabled(),
    })


@router.get("/archiv/{incident_id}/pdf")
async def download_pdf(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons",
                           "breathing_troops", "log_entries"])
    pdf_bytes = render_incident_pdf(incident, base_url=str(request.base_url))
    filename = f"einsatz_{incident.id}_{incident.alarm_type_code}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/archiv/{incident_id}/ki-bericht", response_class=HTMLResponse)
async def generate_ai_report(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not has_role(user, *_AI_ROLES):
        raise HTTPException(403, detail="Keine Berechtigung")
    if not ai_is_enabled():
        return HTMLResponse('<p class="text-muted">KI-Funktionen sind nicht aktiviert.</p>')

    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)

    from app.core.audit import write_audit
    from app.services.incident_service import collect_report_context

    try:
        context = collect_report_context(incident_id, db)
        draft = await generate_report_draft(context)
    except AIServiceError as exc:
        return HTMLResponse(f'<p style="color:var(--red)">KI-Fehler: {exc}</p>')

    write_audit(db, "ai_report_generated", user_id=user.id, incident_id=incident_id)
    db.commit()

    return templates.TemplateResponse(request, "archive/_ki_bericht.html", {
        "user": user, "incident": incident, "draft": draft,
    })


@router.post("/archiv/{incident_id}/ki-bericht/speichern", response_class=HTMLResponse)
async def save_ai_report(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not has_role(user, *_AI_ROLES):
        raise HTTPException(403, detail="Keine Berechtigung")

    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)

    form = await request.form()
    draft_text = str(form.get("ki_bericht_entwurf", "")).strip()
    incident.ai_report_draft = draft_text or None
    db.commit()

    return HTMLResponse('<p style="color:var(--green); margin-top:.5rem;">✓ Entwurf gespeichert.</p>')
