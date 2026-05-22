"""Archiv & PDF-Export."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.incident import Incident
from app.services.pdf_service import render_incident_pdf

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/archiv", response_class=HTMLResponse)
async def archive_list(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incidents = db.query(Incident).order_by(Incident.started_at.desc()).all()
    return templates.TemplateResponse(request, "archive/list.html", {
        "user": user, "incidents": incidents,
    })


@router.get("/archiv/{incident_id}", response_class=HTMLResponse)
async def archive_detail(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(404)
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons",
                           "breathing_troops", "log_entries"])
    return templates.TemplateResponse(request, "archive/detail.html", {
        "user": user, "incident": incident,
    })


@router.get("/archiv/{incident_id}/pdf")
async def download_pdf(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(404)
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons",
                           "breathing_troops", "log_entries"])
    pdf_bytes = render_incident_pdf(incident, base_url=str(request.base_url))
    filename = f"einsatz_{incident.id}_{incident.alarm_type_code}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
