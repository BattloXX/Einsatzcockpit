"""Atemschutzüberwachung UI."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.breathing import BOTTLE_PRESETS, BreathingTroop
from app.models.incident import Incident
from app.models.master import Member
from app.services.breathing_service import (
    ReadinessWarning,
    ack_warning,
    check_troop_warnings,
    create_troop,
    get_time_warning,
    get_warning_level,
    log_pressure,
    report_objective_reached,
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
    all_members = db.query(Member).filter(Member.active == True).order_by(Member.lastname).all()  # noqa: E712
    members = [m for m in all_members if m.is_agt]
    vehicles = [v for v in incident.vehicles if not v.removed_at]

    troops_with_warnings = [
        (t, *get_warning_level(t), get_time_warning(t)) for t in incident.breathing_troops
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
    is_sicherheitstrupp: bool = Form(False),
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
            "member_id": int(mid) if mid and str(mid).isdigit() else None,  # type: ignore[arg-type]
            "free_text_name": form_data.get(key_name) or None,
            "role": form_data.get(key_role, "truppmann"),
            "start_press": float(form_data[key_press]) if form_data.get(key_press) else None,  # type: ignore[arg-type]
        })
        i += 1

    # Validierung: mindestens 2 Mitglieder ausgefüllt (Member-ID ODER Freitext-Name)
    filled = [
        m for m in members_data
        if m["member_id"] or (m["free_text_name"] and m["free_text_name"].strip())  # type: ignore[union-attr]
    ]
    if len(filled) < 2:
        return RedirectResponse(
            f"/einsatz/{incident_id}/atemschutz?error=min_two_members",
            status_code=303,
        )

    create_troop(
        db, incident_id=incident_id, name=name,
        members_data=members_data, task_text=task_text or None,
        vehicle_id=vehicle_id, unit_name=unit_name.strip() or None,
        location_text=location_text.strip() or None,
        planned_duration_min=duration,
        bottle_preset=bottle_preset.strip() or None,
        is_sicherheitstrupp=is_sicherheitstrupp,
        user_id=request.state.user.id,
    )
    db.commit()
    await manager.broadcast(incident_id, {"type": "troop_created", "reload_breathing": True})
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/starten")
async def start_troop_view(
    incident_id: int, troop_id: int, request: Request,
    override_reason: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    can_override = has_role(user, "breathing_supervisor", "incident_leader", "admin")
    permitted_reason = override_reason if can_override else None
    try:
        start_troop(
            db, troop, user_id=user.id,
            override_reason=permitted_reason,
            override_user_id=user.id if permitted_reason else None,
        )
    except ReadinessWarning as warning:
        return templates.TemplateResponse(request, "breathing/_readiness_warning.html", {
            "user": user,
            "incident": troop.incident,
            "troop": troop,
            "issues": warning.issues,
            "can_override_readiness": can_override,
        })
    except ValueError as exc:
        return Response(str(exc), status_code=400)
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "troop_started", "troop_id": troop_id,
        "readiness_overridden": bool(permitted_reason),
    })
    return Response(status_code=204)


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
        "status": status, "warning": warning.level, "time_warning": time_warn,
    })
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/druck")
async def log_pressure_view(
    incident_id: int, troop_id: int, request: Request,
    troop_member_id: int = Form(...),
    pressure_bar: float = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        log_pressure(db, troop, troop_member_id, pressure_bar,
                 note=note.strip() or None,
                 recorded_by_user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=400)
    db.commit()
    updated_troop = db.get(BreathingTroop, troop_id)
    assert updated_troop is not None
    warning = get_warning_level(updated_troop)
    time_warn = get_time_warning(updated_troop)
    lowest = updated_troop.lowest_current_pressure or pressure_bar
    await manager.broadcast(incident_id, {
        "type": "pressure_logged", "troop_id": troop_id,
        "troop_member_id": troop_member_id,
        "pressure": pressure_bar, "lowest_pressure": lowest,
        "warning": warning.level,
        "warning_member_id": warning.member.id if warning.member else None,
        "warning_member_name": warning.member.display_name if warning.member else None,
        "time_warning": time_warn,
        "last_meldung_at": updated_troop.last_meldung_at.isoformat() if updated_troop.last_meldung_at else None,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/einsatzziel")
async def objective_reached_view(
    incident_id: int, troop_id: int, request: Request,
    troop_member_id: int = Form(...),
    pressure_bar: float = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
):
    """Erfasst den Druck am Einsatzziel und finalisiert den Rückzugsdruck."""
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        member = report_objective_reached(
            db, troop, troop_member_id, pressure_bar,
            recorded_by_user_id=request.state.user.id,
        )
    except ValueError:
        return Response(status_code=400)
    db.commit()
    warning = get_warning_level(troop)
    await manager.broadcast(incident_id, {
        "type": "troop_objective_reached", "troop_id": troop_id,
        "troop_member_id": member.id, "pressure": pressure_bar,
        "withdraw_press": member.withdraw_press,
        "warning": warning.level,
        "warning_member_id": warning.member.id if warning.member else None,
    })
    return Response(status_code=204)


@router.get("/einsatz/{incident_id}/atemschutz/aktive-warnungen")
async def active_breathing_warnings(
    incident_id: int, request: Request, db: Session = Depends(get_db),
):
    """Liefert aktive Warnungen für Reload und WebSocket-Wiederverbindung."""
    if not getattr(request.state, "user", None):
        return Response(status_code=401)
    incident = db.get(Incident, incident_id)
    if not incident:
        return Response(status_code=404)
    db.refresh(incident, ["breathing_troops"])
    active = []
    for troop in incident.breathing_troops:
        if troop.status not in ("im_einsatz", "rueckzug"):
            continue
        for kind in check_troop_warnings(troop):
            active.append({"troop_id": troop.id, "kind": kind})
    return JSONResponse({"warnings": active})


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
    if kind not in ("one_third", "two_third", "max_time", "withdraw"):
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
    pdf_bytes = render_troop_pdf(troop, incident, base_url=base_url)  # type: ignore[arg-type]

    safe_name = troop.name.replace(" ", "_").replace("/", "-")
    filename = f"AS-Protokoll_{safe_name}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
