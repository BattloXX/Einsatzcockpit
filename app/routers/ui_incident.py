"""UI Router – Einsatz-Board (HTMX-Endpoints)."""
import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.permissions import require_role, has_role
from app.models.incident import Incident, IncidentColumn, IncidentVehicle, Task, Message, RescuedPerson, IncidentToken
from app.models.master import AlarmType, TaskSuggestion, LageHint, VehicleMaster
from app.services.incident_service import (
    create_incident, add_task, assign_task_to_vehicle, move_vehicle_to_column,
    close_incident, add_section_column, set_commander, quick_create_commander,
    update_task, cancel_task, move_card,
)
from app.models.master import Member
from app.services.broadcast import manager
from app.core.security import sign_qr_token, hash_api_key
import qrcode
import io
import base64

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _incident_or_404(incident_id: int, db: Session):
    inc = db.get(Incident, incident_id)
    if not inc:
        from fastapi import HTTPException
        raise HTTPException(404, "Einsatz nicht gefunden")
    return inc


# ── Dashboard / Index ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    active = db.query(Incident).filter(Incident.status == "active").order_by(Incident.started_at.desc()).all()
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    return templates.TemplateResponse(request, "index.html", {
        "user": user,
        "active_incidents": active, "alarm_types": alarm_types,
    })


# ── Einsatz starten (manuell) ─────────────────────────────────────────────────

@router.post("/einsatz/neu")
async def new_incident(
    request: Request,
    alarm_type_code: str = Form(...),
    address_street: str = Form(""),
    address_no: str = Form(""),
    address_city: str = Form("Wolfurt"),
    report_text: str = Form(""),
    is_exercise: bool = Form(False),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    user = request.state.user
    incident = create_incident(
        db, alarm_type_code=alarm_type_code,
        address_street=address_street or None,
        address_no=address_no or None,
        address_city=address_city or None,
        report_text=report_text or None,
        is_exercise=is_exercise,
        incident_leader_user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    await manager.broadcast_all({
        "type": "incident_created", "incident_id": incident.id,
        "alarm": alarm_type_code, "is_exercise": is_exercise,
        "url": f"/einsatz/{incident.id}",
        "title": f"{'[ÜBUNG] ' if is_exercise else ''}Neuer Einsatz: {alarm_type_code}",
    })
    return RedirectResponse(f"/einsatz/{incident.id}", status_code=303)


# ── Einsatz-Board ─────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}", response_class=HTMLResponse)
async def incident_board(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons"])
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    lage_hints = db.query(LageHint).order_by(LageHint.display_order).all()
    task_suggestions = db.query(TaskSuggestion).filter(
        TaskSuggestion.alarm_type_code == incident.alarm_type_code
    ).order_by(TaskSuggestion.display_order).all()
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/board.html", {
        "user": user, "incident": incident,
        "alarm_types": alarm_types, "lage_hints": lage_hints,
        "task_suggestions": task_suggestions, "can_edit": can_edit,
    })


# ── Aufgaben ───────────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/aufgabe", response_class=HTMLResponse)
async def create_task(
    incident_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    incident = _incident_or_404(incident_id, db)
    task = add_task(db, incident, title, detail or None, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "task_created", "task_id": task.id, "reload_board": True,
    })
    return templates.TemplateResponse(request, "incident/_task_card.html", {
        "task": task, "incident": incident,
        "can_edit": True,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/erledigt")
async def toggle_task_done(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    task.is_done = not task.is_done
    task.done_at = datetime.now(timezone.utc) if task.is_done else None
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/zuweisen")
async def assign_task(
    incident_id: int, task_id: int, request: Request,
    vehicle_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    task = db.get(Task, task_id)
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not task or not vehicle:
        return Response(status_code=404)
    assign_task_to_vehicle(db, task, vehicle, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_assigned", "reload_board": True})
    return Response(status_code=204)


# ── Fahrzeug verschieben ──────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/verschieben")
async def move_vehicle(
    incident_id: int, vehicle_id: int, request: Request,
    column_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    column = db.get(IncidentColumn, column_id)
    if not vehicle or not column:
        return Response(status_code=404)
    move_vehicle_to_column(db, vehicle, column, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_moved", "reload_board": True})
    return Response(status_code=204)


# ── Abschnitt-Spalte anlegen ──────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/abschnitt")
async def create_section(
    incident_id: int, request: Request,
    title: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    col = add_section_column(db, incident, title, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "column_created", "reload_board": True})
    return Response(status_code=204)


# ── Meldungen ─────────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/meldung")
async def create_message(
    incident_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    due_after_min: int = Form(0),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    incident = _incident_or_404(incident_id, db)
    due_sec = due_after_min * 60 if due_after_min > 0 else None
    due_at = None
    if due_sec:
        from datetime import timedelta
        due_at = incident.started_at + timedelta(seconds=due_sec)
    msg = Message(
        incident_id=incident_id, title=title, detail=detail or None,
        due_after_sec=due_sec, due_at=due_at,
    )
    db.add(msg)
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_created", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/meldung/{msg_id}/erledigt")
async def toggle_message(
    incident_id: int, msg_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, msg_id)
    if not msg:
        return Response(status_code=404)
    msg.is_done = not msg.is_done
    msg.done_at = datetime.now(timezone.utc) if msg.is_done else None
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    return Response(status_code=204)


# ── Person erfassen ──────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/person")
async def create_person(
    incident_id: int, request: Request,
    gender: str = Form(...), person_group: str = Form(...),
    age_range: str = Form(""), name: str = Form(""), location: str = Form(""),
    vehicle_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    person = RescuedPerson(
        incident_id=incident_id,
        gender=gender, person_group=person_group,
        age_range=age_range or None, name=name or None,
        location=location or None, vehicle_id=vehicle_id,
    )
    db.add(person)
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_created", "reload_board": True})
    return Response(status_code=204)


# ── Einsatz abschließen ───────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/abschliessen")
async def close_incident_view(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    close_incident(db, incident, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "incident_closed"})
    return RedirectResponse(f"/archiv/{incident_id}", status_code=303)


# ── Log-Eintrag ───────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/log")
async def add_log(
    incident_id: int, request: Request,
    text: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.models.incident import IncidentLog
    entry = IncidentLog(incident_id=incident_id, text=text, user_id=request.state.user.id)
    db.add(entry)
    db.commit()
    await manager.broadcast(incident_id, {"type": "log_updated"})
    return Response(status_code=204)


# ── QR-Code ───────────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/qr", response_class=HTMLResponse)
async def get_qr_code(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)

    token = sign_qr_token(incident_id, user.id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Store token in DB (upsert per user+incident)
    existing = db.query(IncidentToken).filter(
        IncidentToken.incident_id == incident_id,
        IncidentToken.issued_by_user_id == user.id,
        IncidentToken.revoked_at.is_(None),
    ).first()
    if not existing:
        from app.models.incident import IncidentToken as IT
        db.add(IT(incident_id=incident_id, token_hash=token_hash, issued_by_user_id=user.id))
        db.commit()

    url = f"{request.base_url}qr-login?incident_id={incident_id}&token={token}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse(request, "incident/qr_modal.html", {
        "incident": incident,
        "qr_img": img_b64, "qr_url": url,
    })


# ── Verlauf / Historie ────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/historie", response_class=HTMLResponse)
async def incident_history(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)
    from app.models.incident import IncidentChange
    changes = db.query(IncidentChange).filter(
        IncidentChange.incident_id == incident_id
    ).order_by(IncidentChange.ts.desc()).limit(500).all()
    return templates.TemplateResponse(request, "incident/history.html", {
        "user": user, "incident": incident, "changes": changes,
    })


# ── Fahrzeug-Detail-Modal ─────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/detail", response_class=HTMLResponse)
async def vehicle_detail(
    incident_id: int, vehicle_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)

    dept_id = vehicle.vehicle_master.dept_id if vehicle.vehicle_master else None
    members = (
        db.query(Member)
        .filter(Member.org_id == dept_id, Member.active == True)  # noqa: E712
        .order_by(Member.lastname, Member.firstname)
        .all()
    ) if dept_id else []

    from app.models.incident import IncidentChange
    recent_changes = (
        db.query(IncidentChange)
        .filter(
            IncidentChange.incident_id == incident_id,
            IncidentChange.entity_type == "incident_vehicle",
            IncidentChange.entity_id == vehicle_id,
        )
        .order_by(IncidentChange.ts.desc())
        .limit(10)
        .all()
    )
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_vehicle_modal.html", {
        "user": user, "incident": incident, "vehicle": vehicle,
        "members": members, "recent_changes": recent_changes, "can_edit": can_edit,
    })


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/kommandant")
async def set_vehicle_commander(
    incident_id: int, vehicle_id: int, request: Request,
    member_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response(status_code=404)
    set_commander(db, vehicle, member_id or None, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}/fahrzeug/{vehicle_id}/detail", status_code=303)


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/kommandant-neu")
async def set_vehicle_commander_new(
    incident_id: int, vehicle_id: int, request: Request,
    full_name: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle or not full_name.strip():
        return Response(status_code=404)
    quick_create_commander(db, vehicle, full_name.strip(), user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}/fahrzeug/{vehicle_id}/detail", status_code=303)


# ── Auftrags-Detail / Edit-Modal ──────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/aufgabe/{task_id}/detail", response_class=HTMLResponse)
async def task_detail(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    task = db.get(Task, task_id)
    if not task:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_task_modal.html", {
        "user": user, "incident": incident, "task": task, "can_edit": can_edit,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}")
async def update_task_endpoint(
    incident_id: int, task_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    update_task(db, task, title, detail or None, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}/aufgabe/{task_id}/detail", status_code=303)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/ausblenden")
async def cancel_task_endpoint(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    cancel_task(db, task, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "reload_board": True})
    return Response(status_code=204)


# ── Drag & Drop: generischer Karte-verschieben-Endpoint ──────────────────────

@router.post("/einsatz/{incident_id}/karte/verschieben")
async def move_card_endpoint(
    incident_id: int, request: Request,
    kind: str = Form(...),
    uid: int = Form(...),
    column_id: Optional[int] = Form(None),
    position: int = Form(0),
    vehicle_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    move_card(
        db, incident_id, kind, uid,
        column_id=column_id, position=position,
        vehicle_id=vehicle_id,
        user_id=request.state.user.id,
    )
    db.commit()
    await manager.broadcast(incident_id, {"type": "card_moved", "reload_board": True})
    return Response(status_code=204)
