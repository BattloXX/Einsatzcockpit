"""Atemschutzüberwachung UI."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.breathing import BreathingTroop
from app.models.incident import Incident
from app.models.master import Member
from app.services.breathing_service import (
    create_troop,
    get_warning_level,
    log_pressure,
    start_troop,
    update_troop_status,
)
from app.services.broadcast import manager

router = APIRouter()


@router.get("/einsatz/{incident_id}/atemschutz", response_class=HTMLResponse)
async def breathing_board(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(404)
    db.refresh(incident, ["breathing_troops"])
    members = db.query(Member).filter(Member.active == True).order_by(Member.lastname).all()  # noqa: E712

    troops_with_warnings = [
        (t, get_warning_level(t)) for t in incident.breathing_troops
    ]
    return templates.TemplateResponse(request, "breathing/board.html", {
        "user": user, "incident": incident,
        "troops_with_warnings": troops_with_warnings, "members": members,
    })


@router.post("/einsatz/{incident_id}/atemschutz/trupp")
async def create_breathing_troop(
    incident_id: int, request: Request,
    name: str = Form(...),
    task_text: str = Form(""),
    vehicle_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin")),
):
    # Parse member data from form (member_0_id, member_0_role, member_0_press, etc.)
    form_data = await request.form()
    members_data = []
    i = 0
    while True:
        key_id = f"member_{i}_id"
        key_name = f"member_{i}_name"
        key_role = f"member_{i}_role"
        key_press = f"member_{i}_press"
        if key_id not in form_data and key_name not in form_data:
            break
        mid = form_data.get(key_id)
        members_data.append({
            "member_id": int(mid) if mid and str(mid).isdigit() else None,
            "free_text_name": form_data.get(key_name) or None,
            "role": form_data.get(key_role, "truppmann"),
            "start_press": float(form_data[key_press]) if form_data.get(key_press) else None,
        })
        i += 1

    # Validierung: mindestens 2 Mitglieder ausgefüllt (Member-ID ODER Freitext-Name)
    filled = [
        m for m in members_data
        if m["member_id"] or (m["free_text_name"] and m["free_text_name"].strip())
    ]
    if len(filled) < 2:
        return RedirectResponse(
            f"/einsatz/{incident_id}/atemschutz?error=min_two_members",
            status_code=303,
        )

    troop = create_troop(
        db, incident_id=incident_id, name=name,
        members_data=members_data, task_text=task_text or None,
        vehicle_id=vehicle_id, user_id=request.state.user.id,
    )
    db.commit()
    await manager.broadcast(incident_id, {"type": "troop_created", "reload_breathing": True})
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/starten")
async def start_troop_view(
    incident_id: int, troop_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    start_troop(db, troop, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "troop_started", "troop_id": troop_id})
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/status")
async def update_status(
    incident_id: int, troop_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    update_troop_status(db, troop, status, user_id=request.state.user.id)
    db.commit()
    warning = get_warning_level(troop)
    await manager.broadcast(incident_id, {
        "type": "troop_status_changed", "troop_id": troop_id,
        "status": status, "warning": warning,
    })
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/druck")
async def log_pressure_view(
    incident_id: int, troop_id: int, request: Request,
    member_id: int | None = Form(None),
    pressure_bar: float = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    log_pressure(db, troop, member_id, pressure_bar, recorded_by_user_id=request.state.user.id)
    db.commit()
    warning = get_warning_level(db.get(BreathingTroop, troop_id))
    await manager.broadcast(incident_id, {
        "type": "pressure_logged", "troop_id": troop_id,
        "pressure": pressure_bar, "warning": warning,
    })
    return Response(status_code=204)
