"""Atemschutzüberwachung UI."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.breathing import BOTTLE_PRESETS, BreathingTroop
from app.models.incident import Incident
from app.models.master import Member
from app.services.breathing_service import (
    ack_warning,
    create_troop,
    get_time_warning,
    get_warning_level,
    log_pressure,
    start_troop,
    update_meldung,
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
    db.refresh(incident, ["breathing_troops", "vehicles"])
    members = db.query(Member).filter(Member.active == True).order_by(Member.lastname).all()  # noqa: E712
    vehicles = [v for v in incident.vehicles if not v.removed_at]

    troops_with_warnings = [
        (t, get_warning_level(t), get_time_warning(t)) for t in incident.breathing_troops
    ]
    return templates.TemplateResponse(request, "breathing/board.html", {
        "user": user, "incident": incident,
        "troops_with_warnings": troops_with_warnings, "members": members,
        "vehicles": vehicles, "bottle_presets": BOTTLE_PRESETS,
    })


@router.post("/einsatz/{incident_id}/atemschutz/trupp")
async def create_breathing_troop(
    incident_id: int, request: Request,
    name: str = Form(...),
    task_text: str = Form(""),
    vehicle_id: int | None = Form(None),
    unit_name: str = Form(""),
    location_text: str = Form(""),
    bottle_preset: str = Form(""),
    planned_duration_min: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    # Geplante Einsatzzeit aus Preset ableiten (außer bei "manuell" oder leerem Preset)
    duration = planned_duration_min
    if bottle_preset and bottle_preset != "manuell":
        from app.models.breathing import BOTTLE_PRESET_DURATIONS
        preset_dur = BOTTLE_PRESET_DURATIONS.get(bottle_preset)
        if preset_dur is not None:
            duration = preset_dur

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
        vehicle_id=vehicle_id, unit_name=unit_name.strip() or None,
        location_text=location_text.strip() or None,
        planned_duration_min=duration,
        bottle_preset=bottle_preset.strip() or None,
        user_id=request.state.user.id,
    )
    db.commit()
    await manager.broadcast(incident_id, {"type": "troop_created", "reload_breathing": True})
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/starten")
async def start_troop_view(
    incident_id: int, troop_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
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
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    update_troop_status(db, troop, status, user_id=request.state.user.id)
    db.commit()
    warning = get_warning_level(troop)
    time_warn = get_time_warning(troop)
    await manager.broadcast(incident_id, {
        "type": "troop_status_changed", "troop_id": troop_id,
        "status": status, "warning": warning, "time_warning": time_warn,
    })
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/druck")
async def log_pressure_view(
    incident_id: int, troop_id: int, request: Request,
    member_id: int | None = Form(None),
    pressure_bar: float = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    log_pressure(db, troop, member_id, pressure_bar,
                 note=note.strip() or None,
                 recorded_by_user_id=request.state.user.id)
    db.commit()
    updated_troop = db.get(BreathingTroop, troop_id)
    warning = get_warning_level(updated_troop)
    time_warn = get_time_warning(updated_troop)
    lowest = updated_troop.lowest_current_pressure or pressure_bar
    await manager.broadcast(incident_id, {
        "type": "pressure_logged", "troop_id": troop_id,
        "member_id": member_id,
        "pressure": pressure_bar, "lowest_pressure": lowest,
        "warning": warning, "time_warning": time_warn,
        "last_meldung_at": updated_troop.last_meldung_at.isoformat() if updated_troop.last_meldung_at else None,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/meldung")
async def troop_meldung(
    incident_id: int, troop_id: int, request: Request,
    text: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    update_meldung(db, troop, text.strip() or None, user_id=request.state.user.id)
    db.commit()
    time_warn = get_time_warning(troop)
    await manager.broadcast(incident_id, {
        "type": "troop_meldung", "troop_id": troop_id,
        "text": text.strip() or None,
        "time_warning": time_warn,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/ack")
async def troop_ack(
    incident_id: int, troop_id: int, request: Request,
    kind: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    if kind not in ("one_third", "max_time", "withdraw"):
        return Response(status_code=400)
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    ack_warning(db, troop, kind, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "troop_warning_acked", "troop_id": troop_id, "kind": kind,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/standort")
async def troop_standort(
    incident_id: int, troop_id: int, request: Request,
    location_text: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    troop.location_text = location_text.strip() or None
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "troop_standort", "troop_id": troop_id,
        "location_text": troop.location_text,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/auftrag")
async def troop_auftrag(
    incident_id: int, troop_id: int, request: Request,
    task_text: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop:
        return Response(status_code=404)
    troop.task_text = task_text.strip() or None
    db.commit()
    return Response(status_code=204)


@router.get("/einsatz/{incident_id}/atemschutz/{troop_id}/pdf")
async def troop_pdf(
    incident_id: int, troop_id: int, request: Request,
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        from fastapi import HTTPException
        raise HTTPException(404)
    # Eager-load relationships needed for PDF
    db.refresh(troop, ["members", "pressure_logs"])
    incident = db.get(Incident, incident_id)
    db.refresh(incident)

    from app.services.pdf_service import render_troop_pdf
    base_url = str(request.base_url).rstrip("/")
    pdf_bytes = render_troop_pdf(troop, incident, base_url=base_url)

    safe_name = troop.name.replace(" ", "_").replace("/", "-")
    filename = f"AS-Protokoll_{safe_name}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
