"""UI Router – Einsatz-Board (HTMX-Endpoints)."""
import base64
import hashlib
import io
from datetime import UTC, datetime

import qrcode
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role
from app.core.security import sign_qr_token
from app.core.templating import templates
from app.db import get_db
from app.models.incident import (
    PERSON_STATUS_VALUES,
    UNIT_STATUS_VALUES,
    Incident,
    IncidentColumn,
    IncidentToken,
    IncidentVehicle,
    Message,
    MessageMedia,
    PersonMedia,
    RescuedPerson,
    Task,
)
from app.models.master import (
    BOS_VALUES,
    AlarmType,
    FireDept,
    LageHint,
    Member,
    MemberQualification,
    MessageSuggestion,
    Qualification,
    TaskSuggestion,
    VehicleMaster,
)
from app.models.user import Role, User, UserRole
from app.services.broadcast import manager
from app.services.incident_service import (
    add_section_column,
    add_task,
    assign_task_to_vehicle,
    cancel_task,
    close_incident,
    create_incident,
    list_commander_candidates,
    list_el_candidates,
    move_card,
    move_vehicle_to_column,
    quick_create_commander,
    set_commander,
    set_message_status,
    set_task_status,
    set_unit_status,
    update_task,
)

router = APIRouter()


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
        primary_org_id=user.org_id,
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
    msg_suggestions = db.query(MessageSuggestion).filter(
        MessageSuggestion.alarm_type_code == incident.alarm_type_code
    ).order_by(MessageSuggestion.display_order).all()
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    # Leader candidates: active users of same org with relevant roles
    leader_roles = {"incident_leader", "admin", "org_admin", "system_admin"}
    leader_candidates = (
        db.query(User)
        .join(UserRole, User.id == UserRole.user_id)
        .join(Role, UserRole.role_id == Role.id)
        .filter(
            User.active == True,
            or_(
                Role.code == "system_admin",
                User.org_id == incident.primary_org_id,
            ) if incident.primary_org_id else True,
            Role.code.in_(leader_roles),
        )
        .distinct()
        .order_by(User.display_name)
        .all()
    )
    org_ids = [incident.primary_org_id] if incident.primary_org_id else []
    for io in (incident.collaborating_orgs or []):
        if io.org_id not in org_ids:
            org_ids.append(io.org_id)
    el_member_candidates = list_el_candidates(db, org_ids)
    gk_member_candidates = list_commander_candidates(db, org_ids)
    return templates.TemplateResponse(request, "incident/board.html", {
        "user": user, "incident": incident,
        "alarm_types": alarm_types, "lage_hints": lage_hints,
        "task_suggestions": task_suggestions, "msg_suggestions": msg_suggestions,
        "can_edit": can_edit, "leader_candidates": leader_candidates,
        "el_member_candidates": el_member_candidates,
        "gk_member_candidates": gk_member_candidates,
        "unit_status_values": UNIT_STATUS_VALUES,
    })


# ── Einsatzleiter wechseln ────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/einsatzleiter-mitglied")
async def set_incident_leader_member(
    incident_id: int, request: Request,
    member_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    incident.incident_leader_member_id = member_id or None
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "incident_leader_changed",
        "reload_board": True,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/gk")
async def set_vehicle_gk_quick(
    incident_id: int, vehicle_id: int, request: Request,
    member_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response(status_code=404)
    if member_id:
        ok = (
            db.query(MemberQualification)
            .join(Qualification, Qualification.id == MemberQualification.qualification_id)
            .filter(
                MemberQualification.member_id == member_id,
                Qualification.is_gruppenkommandant.is_(True),
            )
            .first()
        )
        if not ok:
            return Response(status_code=422)
    set_commander(db, vehicle, member_id or None, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/einsatzleiter")
async def set_incident_leader(
    incident_id: int, request: Request,
    user_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    leader = db.get(User, user_id)
    if leader and leader.active:
        incident.incident_leader_user_id = leader.id
        db.commit()
        await manager.broadcast(incident_id, {
            "type": "incident_leader_changed",
            "leader_id": leader.id,
            "leader_name": leader.display_name,
        })
    return Response(status_code=204)


# ── Aufgaben ───────────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/aufgabe", response_class=HTMLResponse)
async def create_task(
    incident_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    column_id: int | None = Form(None),
    vehicle_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    incident = _incident_or_404(incident_id, db)
    task = add_task(
        db, incident, title, detail or None,
        user_id=request.state.user.id, column_id=column_id,
    )
    # Einheit direkt mit-zuweisen — column_id bleibt erhalten, damit der Auftrag
    # gleichzeitig auf dem Board UND in der Fahrzeug-Karte sichtbar ist
    # (analog assign_task_to_vehicle).
    if vehicle_id:
        task.vehicle_id = vehicle_id
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
    # Toggle: sowohl is_done als auch status synchron halten,
    # damit die Status-Pille (Ampel) den Wechsel reflektiert.
    new_status = "open" if task.is_done else "done"
    set_task_status(db, task, new_status, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": True})
    # Re-render der Vehicle-Card, falls die Task zugewiesen ist (Strikethrough sichtbar).
    if task.vehicle_id:
        vehicle = db.get(IncidentVehicle, task.vehicle_id)
        if vehicle:
            inc = task.incident
            _org_ids = [inc.primary_org_id] if inc.primary_org_id else []
            for io in (inc.collaborating_orgs or []):
                if io.org_id not in _org_ids:
                    _org_ids.append(io.org_id)
            return templates.TemplateResponse(request, "incident/_vehicle_card.html", {
                "vehicle": vehicle, "incident": inc,
                "can_edit": True,
                "unit_status_values": UNIT_STATUS_VALUES,
                "gk_member_candidates": list_commander_candidates(db, _org_ids),
            })
    # Free Task (nicht zugewiesen): Task-Card refreshen.
    return templates.TemplateResponse(request, "incident/_task_card.html", {
        "task": task, "incident": task.incident, "can_edit": True,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/ampel")
async def set_task_ampel(
    incident_id: int, task_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    try:
        set_task_status(db, task, status, user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=422)
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


# ── Fahrzeug hinzufügen (Inline-Wizard) ───────────────────────────────────────

@router.get("/einsatz/{incident_id}/fahrzeug-vorschlaege")
async def vehicle_suggestions(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    q: str = "",
):
    """JSON-Endpoint: Vorschläge an Fahrzeugen, die zu diesem Einsatz hinzugefügt werden können.

    Liefert VehicleMaster-Einträge der primären Org + kollaborierenden Orgs,
    abzüglich der bereits aktuell zugewiesenen Fahrzeuge.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    incident = _incident_or_404(incident_id, db)
    if not has_role(user, "incident_leader", "admin", "recorder"):
        return Response(status_code=403)

    org_ids = set()
    if incident.primary_org_id:
        org_ids.add(incident.primary_org_id)
    for io in (incident.collaborating_orgs or []):
        org_ids.add(io.org_id)

    already_master_ids = {
        v.vehicle_master_id for v in incident.vehicles if v.removed_at is None
    }
    base_q = db.query(VehicleMaster).filter(
        VehicleMaster.active == True,  # noqa: E712
    )
    if q:
        like = f"%{q.strip()}%"
        from sqlalchemy import or_ as _or
        base_q = base_q.filter(
            _or(VehicleMaster.code.ilike(like), VehicleMaster.name.ilike(like))
        )
    vehicles = base_q.order_by(VehicleMaster.display_order, VehicleMaster.code).all()
    items = [
        {
            "id": v.id,
            "code": v.code,
            "name": v.name,
            "type": v.type or "",
            "dept_id": v.dept_id,
            "dept_name": v.dept.name if v.dept else "",
            "in_use": v.id in already_master_ids,
        }
        for v in vehicles
    ]
    from fastapi.responses import JSONResponse
    return JSONResponse({"items": items})


@router.post("/einsatz/{incident_id}/fahrzeug-hinzufuegen")
async def attach_vehicle_to_incident(
    incident_id: int, request: Request,
    vehicle_master_id: int | None = Form(None),
    new_code: str = Form(""),
    new_name: str = Form(""),
    new_type: str = Form(""),
    commander_member_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    """Fügt ein Fahrzeug zum laufenden Einsatz hinzu — entweder per Master-ID oder
    durch Anlegen eines neuen, nicht in den Stammdaten existierenden Fahrzeugs.
    """
    incident = _incident_or_404(incident_id, db)
    db.refresh(incident, ["columns", "vehicles"])

    # Stammfahrzeug bestimmen / neu anlegen
    if vehicle_master_id:
        vm = db.get(VehicleMaster, vehicle_master_id)
        if not vm:
            return Response("Fahrzeug nicht gefunden", status_code=404)
    elif new_code.strip() and new_name.strip():
        dept_id = incident.primary_org_id or request.state.user.org_id
        if not dept_id:
            return Response("Keine Organisation zugeordnet", status_code=400)
        vm = VehicleMaster(
            dept_id=dept_id,
            code=new_code.strip()[:30],
            name=new_name.strip()[:150],
            type=(new_type.strip() or None),
            is_first_train=False,
            active=True,
            display_order=999,
        )
        db.add(vm)
        db.flush()
    else:
        return Response("vehicle_master_id ODER (new_code + new_name) erforderlich", status_code=400)

    # Schon im Einsatz?
    existing = next(
        (v for v in incident.vehicles if v.vehicle_master_id == vm.id and v.removed_at is None),
        None,
    )
    if existing:
        return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)

    target_col = next((c for c in incident.columns if c.code == "dispatched"), None)
    if target_col is None and incident.columns:
        target_col = incident.columns[0]
    if target_col is None:
        return Response("Keine Spalte vorhanden", status_code=400)

    iv = IncidentVehicle(
        incident_id=incident.id,
        column_id=target_col.id,
        vehicle_master_id=vm.id,
        display_order=999,
        commander_member_id=commander_member_id or None,
    )
    db.add(iv)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_added", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)


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
    vehicle_id: int | None = Form(None),
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
        vehicle_id=vehicle_id or None,
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
    msg.done_at = datetime.now(UTC) if msg.is_done else None
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/meldung/{msg_id}/ampel")
async def set_message_ampel(
    incident_id: int, msg_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, msg_id)
    if not msg:
        return Response(status_code=404)
    try:
        set_message_status(db, msg, status, user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=422)
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    return Response(status_code=204)


# ── Person erfassen ──────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/person")
async def create_person(
    incident_id: int, request: Request,
    gender: str = Form(...), person_group: str = Form(...),
    age_range: str = Form(""), name: str = Form(""), location: str = Form(""),
    vehicle_id: int | None = Form(None),
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


@router.post("/einsatz/{incident_id}/autoclose/keepopen")
async def autoclose_keepopen(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    """"Offen halten"-Bestätigung aus dem 48h-Warning-Banner.

    Setzt den Warning-Stempel zurück und aktualisiert started_at, damit
    der 48h-Zähler von neuem läuft.
    """
    incident = _incident_or_404(incident_id, db)
    incident.autoclose_warn_sent_at = None
    incident.started_at = datetime.now(UTC).replace(tzinfo=None)
    incident.autoclose_keepopen_count = (incident.autoclose_keepopen_count or 0) + 1
    db.commit()
    await manager.broadcast(incident_id, {"type": "autoclose_dismissed"})
    return Response(status_code=204)


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


@router.get("/einsatz/{incident_id}/qr/print", response_class=HTMLResponse)
async def qr_print(incident_id: int, request: Request, db: Session = Depends(get_db)):
    """Druckoptimierte Seite mit großem QR-Code + Einsatz-Eckdaten.
    Wiederverwendung der Token-Logik von /qr."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)

    token = sign_qr_token(incident_id, user.id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
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
    # Größerer QR-Code für den Druck (box_size erhöht)
    img = qrcode.make(url, box_size=14, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
    logo_url = (org.logo_path if org and org.logo_path else None) or "/static/img/logo.png"
    return templates.TemplateResponse(request, "incident/qr_print.html", {
        "incident": incident,
        "qr_img": img_b64, "qr_url": url,
        "logo_url": logo_url,
        "base_url": str(request.base_url).rstrip("/"),
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

    org_ids = [incident.primary_org_id] + [io.org_id for io in (incident.collaborating_orgs or [])]
    org_ids = [oid for oid in org_ids if oid]
    commander_candidates = list_commander_candidates(db, org_ids)

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
        "members": commander_candidates, "recent_changes": recent_changes,
        "can_edit": can_edit, "unit_status_values": UNIT_STATUS_VALUES,
        "bos_values": BOS_VALUES,
    })


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/kommandant")
async def set_vehicle_commander(
    incident_id: int, vehicle_id: int, request: Request,
    member_id: int | None = Form(None),
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


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/status")
async def set_vehicle_unit_status(
    incident_id: int, vehicle_id: int, request: Request,
    unit_status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response(status_code=404)
    try:
        set_unit_status(db, vehicle, unit_status, user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=422)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return Response(status_code=204)


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


# ── Meldungs-Detail / Edit-Modal ──────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/meldung/{message_id}/detail", response_class=HTMLResponse)
async def message_detail(
    incident_id: int, message_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    msg = db.get(Message, message_id)
    if not msg or msg.incident_id != incident_id:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": user, "incident": incident, "msg": msg, "can_edit": can_edit,
    })


@router.post("/einsatz/{incident_id}/meldung/{message_id}", response_class=HTMLResponse)
async def update_message_endpoint(
    incident_id: int, message_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, message_id)
    if not msg or msg.incident_id != incident_id:
        return Response(status_code=404)
    msg.title = title.strip() or msg.title
    msg.detail = detail.strip() or None
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": request.state.user, "incident": incident, "msg": msg, "can_edit": can_edit,
    })


# ── Personen-Detail / Edit-Modal ──────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/person/{person_id}/detail", response_class=HTMLResponse)
async def person_detail(
    incident_id: int, person_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": user, "incident": incident, "person": person, "can_edit": can_edit,
        "person_status_values": PERSON_STATUS_VALUES,
    })


@router.post("/einsatz/{incident_id}/person/{person_id}", response_class=HTMLResponse)
async def update_person_endpoint(
    incident_id: int, person_id: int, request: Request,
    gender: str = Form(""), person_group: str = Form(""),
    age_range: str = Form(""), name: str = Form(""), location: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    if gender.strip():
        person.gender = gender.strip()
    if person_group.strip():
        person.person_group = person_group.strip()
    person.age_range = age_range.strip() or None
    person.name = name.strip() or None
    person.location = location.strip() or None
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_updated", "reload_board": True})
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": request.state.user, "incident": incident, "person": person, "can_edit": can_edit,
        "person_status_values": PERSON_STATUS_VALUES,
    })


@router.post("/einsatz/{incident_id}/person/{person_id}/status")
async def set_person_status_endpoint(
    incident_id: int, person_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    if status not in PERSON_STATUS_VALUES:
        return Response("Ungültiger Status", status_code=400)
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    person.status = status
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/person/{person_id}/loeschen")
async def delete_person_endpoint(
    incident_id: int, person_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    db.delete(person)
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_deleted", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}", response_class=HTMLResponse)
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
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_task_modal.html", {
        "user": request.state.user, "incident": incident, "task": task, "can_edit": can_edit,
    })


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


# ── Media-Upload / -Löschen ───────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/medien", response_class=HTMLResponse)
async def upload_task_media(
    incident_id: int, task_id: int, request: Request,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task or task.incident_id != incident_id:
        return Response(status_code=404)
    from fastapi import HTTPException as _HE

    from app.services.media_service import store_upload
    errors: list[str] = []
    for f in files:
        if not f.filename:
            continue
        try:
            await store_upload(f, task, request.state.user, db)
        except _HE as exc:
            errors.append(str(exc.detail))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f.filename}: {exc}")
    db.commit()
    db.refresh(task, ["media"])
    incident = _incident_or_404(incident_id, db)
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": False})
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_task_media.html", {
        "user": request.state.user, "task": task, "incident": incident,
        "can_edit": can_edit, "errors": errors,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/medien/{media_id}/loeschen", response_class=HTMLResponse)
async def delete_task_media(
    incident_id: int, task_id: int, media_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.models.incident import TaskMedia
    from app.services.media_service import delete_media
    media = db.get(TaskMedia, media_id)
    if not media or media.task_id != task_id:
        return Response(status_code=404)
    user = request.state.user
    if media.uploaded_by_user_id != user.id and not has_role(user, "admin", "org_admin"):
        return Response(status_code=403)
    delete_media(media, db)
    db.commit()
    task = db.get(Task, task_id)
    db.refresh(task, ["media"])
    incident = _incident_or_404(incident_id, db)
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": False})
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_task_media.html", {
        "user": user, "task": task, "incident": incident,
        "can_edit": can_edit, "errors": [],
    })


# ── Meldungs-Medien ──────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/meldung/{message_id}/medien", response_class=HTMLResponse)
async def upload_message_media(
    incident_id: int, message_id: int, request: Request,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, message_id)
    if not msg or msg.incident_id != incident_id:
        return Response(status_code=404)
    from fastapi import HTTPException as _HE

    from app.services.media_service import store_upload_for_message
    for f in files:
        if not f.filename:
            continue
        try:
            await store_upload_for_message(f, msg, request.state.user, db)
        except _HE:
            pass
    db.commit()
    db.refresh(msg, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": request.state.user, "incident": incident, "msg": msg, "can_edit": can_edit,
    })


@router.post("/einsatz/{incident_id}/meldung/{message_id}/medien/{media_id}/loeschen", response_class=HTMLResponse)
async def delete_message_media(
    incident_id: int, message_id: int, media_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.services.media_service import delete_media
    media = db.get(MessageMedia, media_id)
    if not media or media.message_id != message_id:
        return Response(status_code=404)
    delete_media(media, db)
    db.commit()
    msg = db.get(Message, message_id)
    db.refresh(msg, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": request.state.user, "incident": incident, "msg": msg, "can_edit": can_edit,
    })


# ── Personen-Medien ───────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/person/{person_id}/medien", response_class=HTMLResponse)
async def upload_person_media(
    incident_id: int, person_id: int, request: Request,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    from fastapi import HTTPException as _HE

    from app.services.media_service import store_upload_for_person
    for f in files:
        if not f.filename:
            continue
        try:
            await store_upload_for_person(f, person, request.state.user, db)
        except _HE:
            pass
    db.commit()
    db.refresh(person, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": request.state.user, "incident": incident, "person": person, "can_edit": can_edit,
    })


@router.post("/einsatz/{incident_id}/person/{person_id}/medien/{media_id}/loeschen", response_class=HTMLResponse)
async def delete_person_media(
    incident_id: int, person_id: int, media_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.services.media_service import delete_media
    media = db.get(PersonMedia, media_id)
    if not media or media.person_id != person_id:
        return Response(status_code=404)
    delete_media(media, db)
    db.commit()
    person = db.get(RescuedPerson, person_id)
    db.refresh(person, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": request.state.user, "incident": incident, "person": person, "can_edit": can_edit,
    })


# ── Drag & Drop: generischer Karte-verschieben-Endpoint ──────────────────────

@router.post("/einsatz/{incident_id}/karte/verschieben")
async def move_card_endpoint(
    incident_id: int, request: Request,
    kind: str = Form(...),
    uid: int = Form(...),
    column_id: int | None = Form(None),
    position: int = Form(0),
    vehicle_id: int | None = Form(None),
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
