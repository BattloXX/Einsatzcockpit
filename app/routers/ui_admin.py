"""Admin-UI: Stammdaten, User, Rollen, API-Keys."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.permissions import require_role
from app.core.security import hash_password, generate_api_key, hash_api_key
from app.core.audit import write_audit
from app.models.user import User, Role, UserRole, ApiKey, AuditLog
from app.models.master import (
    Member, Qualification, MemberQualification, FireDept, VehicleMaster,
    AlarmType, TaskSuggestion, LageHint, DefaultMessage, AlarmDispatchVehicle, SystemSettings,
)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


def _admin_check(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise RedirectResponse("/login", status_code=302)
    return user


# ── Benutzer ──────────────────────────────────────────────────────────────────

@router.get("/benutzer", response_class=HTMLResponse)
async def users_list(request: Request, db: Session = Depends(get_db),
                     _=Depends(require_role("admin"))):
    users = db.query(User).order_by(User.username).all()
    roles = db.query(Role).all()
    return templates.TemplateResponse(request, "admin/users.html", {
        "user": request.state.user,
        "users": users, "roles": roles,
    })


@router.post("/benutzer/neu")
async def create_user(
    request: Request,
    username: str = Form(...), display_name: str = Form(""),
    password: str = Form(...), role_codes: list[str] = Form([]),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    new_user = User(
        username=username,
        display_name=display_name or username,
        password_hash=hash_password(password),
    )
    db.add(new_user)
    db.flush()
    for code in role_codes:
        role = db.query(Role).filter(Role.code == code).first()
        if role:
            db.add(UserRole(user_id=new_user.id, role_id=role.id))
    write_audit(db, "admin.user.created", user_id=request.state.user.id,
                entity_type="user", entity_id=new_user.id)
    db.commit()
    return RedirectResponse("/admin/benutzer", status_code=303)


@router.post("/benutzer/{user_id}/loeschen")
async def delete_user(
    user_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    u = db.get(User, user_id)
    if u and u.id != request.state.user.id:
        u.active = False
        write_audit(db, "admin.user.deactivated", user_id=request.state.user.id,
                    entity_type="user", entity_id=user_id)
        db.commit()
    return RedirectResponse("/admin/benutzer", status_code=303)


# ── API-Keys ──────────────────────────────────────────────────────────────────

@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys(request: Request, db: Session = Depends(get_db),
                   _=Depends(require_role("admin"))):
    keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/api_keys.html", {
        "user": request.state.user, "keys": keys, "new_key": None,
    })


@router.post("/api-keys/neu")
async def create_api_key(
    request: Request, label: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    raw = generate_api_key()
    key = ApiKey(key_hash=hash_api_key(raw), label=label, created_by_user_id=request.state.user.id)
    db.add(key)
    write_audit(db, "admin.api_key.created", user_id=request.state.user.id,
                payload={"label": label})
    db.commit()
    keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/api_keys.html", {
        "user": request.state.user, "keys": keys, "new_key": raw,
    })


@router.post("/api-keys/{key_id}/sperren")
async def revoke_api_key(
    key_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    key = db.get(ApiKey, key_id)
    if key:
        key.revoked_at = datetime.now(timezone.utc)
        write_audit(db, "admin.api_key.revoked", user_id=request.state.user.id,
                    entity_type="api_key", entity_id=key_id)
        db.commit()
    return RedirectResponse("/admin/api-keys", status_code=303)


# ── Mitglieder ────────────────────────────────────────────────────────────────

@router.get("/mitglieder", response_class=HTMLResponse)
async def members_list(request: Request, db: Session = Depends(get_db),
                       _=Depends(require_role("admin"))):
    members = db.query(Member).order_by(Member.lastname, Member.firstname).all()
    qualifications = db.query(Qualification).all()
    return templates.TemplateResponse(request, "admin/members.html", {
        "user": request.state.user,
        "members": members, "qualifications": qualifications,
    })


@router.post("/mitglieder/neu")
async def create_member(
    request: Request,
    lastname: str = Form(...), firstname: str = Form(...),
    phone: str = Form(""), email: str = Form(""),
    qualification_codes: list[str] = Form([]),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    member = Member(lastname=lastname, firstname=firstname,
                    phone=phone or None, email=email or None)
    db.add(member)
    db.flush()
    for code in qualification_codes:
        q = db.query(Qualification).filter(Qualification.code == code).first()
        if q:
            db.add(MemberQualification(member_id=member.id, qualification_id=q.id))
    write_audit(db, "admin.member.created", user_id=request.state.user.id,
                entity_type="member", entity_id=member.id)
    db.commit()
    return RedirectResponse("/admin/mitglieder", status_code=303)


# ── Audit-Log ─────────────────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request, db: Session = Depends(get_db),
                    _=Depends(require_role("admin"))):
    entries = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(request, "admin/audit.html", {
        "user": request.state.user, "entries": entries,
    })


# ── Admin Landing Page ────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_index(request: Request, db: Session = Depends(get_db),
                      _=Depends(require_role("admin", "org_admin", "system_admin"))):
    from app.core.permissions import has_role
    user = request.state.user
    is_sysadmin = has_role(user, "system_admin")
    return templates.TemplateResponse(request, "admin/index.html", {
        "user": user, "is_sysadmin": is_sysadmin,
    })


# ── Mitglieder Edit / Toggle / Qualifikationen ────────────────────────────────

@router.post("/mitglieder/{member_id}/edit")
async def edit_member(
    member_id: int, request: Request,
    lastname: str = Form(...), firstname: str = Form(...),
    phone: str = Form(""), email: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    m = db.get(Member, member_id)
    if m:
        m.lastname = lastname
        m.firstname = firstname
        m.phone = phone or None
        m.email = email or None
        write_audit(db, "admin.member.edited", user_id=request.state.user.id,
                    entity_type="member", entity_id=member_id)
        db.commit()
    return RedirectResponse("/admin/mitglieder?saved=1", status_code=303)


@router.post("/mitglieder/{member_id}/toggle")
async def toggle_member(
    member_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    m = db.get(Member, member_id)
    if m:
        m.active = not m.active
        write_audit(db, "admin.member.toggled", user_id=request.state.user.id,
                    entity_type="member", entity_id=member_id)
        db.commit()
    return RedirectResponse("/admin/mitglieder", status_code=303)


@router.post("/mitglieder/{member_id}/quali")
async def update_member_quali(
    member_id: int, request: Request,
    qualification_codes: list[str] = Form([]),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    m = db.get(Member, member_id)
    if not m:
        return RedirectResponse("/admin/mitglieder", status_code=303)
    # Remove all existing, then re-add
    db.query(MemberQualification).filter(MemberQualification.member_id == member_id).delete()
    form_data = await request.form()
    for code in qualification_codes:
        q = db.query(Qualification).filter(Qualification.code == code).first()
        if q:
            valid_until_str = form_data.get(f"valid_until_{code}")
            valid_until = None
            if valid_until_str:
                from datetime import date
                try:
                    valid_until = date.fromisoformat(valid_until_str)
                except ValueError:
                    pass
            db.add(MemberQualification(member_id=member_id, qualification_id=q.id, valid_until=valid_until))
    write_audit(db, "admin.member.quali_updated", user_id=request.state.user.id,
                entity_type="member", entity_id=member_id)
    db.commit()
    return RedirectResponse("/admin/mitglieder?saved=1", status_code=303)


# ── Benutzer Edit / Rollen / Passwort-Reset ───────────────────────────────────

@router.post("/benutzer/{user_id}/edit")
async def edit_user(
    user_id: int, request: Request,
    display_name: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    u = db.get(User, user_id)
    if u:
        u.display_name = display_name
        write_audit(db, "admin.user.edited", user_id=request.state.user.id,
                    entity_type="user", entity_id=user_id)
        db.commit()
    return RedirectResponse("/admin/benutzer?saved=1", status_code=303)


@router.post("/benutzer/{user_id}/rollen")
async def update_user_roles(
    user_id: int, request: Request,
    role_codes: list[str] = Form([]),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    u = db.get(User, user_id)
    if not u:
        return RedirectResponse("/admin/benutzer", status_code=303)
    db.query(UserRole).filter(UserRole.user_id == user_id).delete()
    for code in role_codes:
        role = db.query(Role).filter(Role.code == code).first()
        if role:
            db.add(UserRole(user_id=user_id, role_id=role.id))
    write_audit(db, "admin.user.roles_updated", user_id=request.state.user.id,
                entity_type="user", entity_id=user_id)
    db.commit()
    return RedirectResponse("/admin/benutzer?saved=1", status_code=303)


@router.post("/benutzer/{user_id}/passwort")
async def reset_user_password(
    user_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    import secrets as sec
    u = db.get(User, user_id)
    if not u:
        return RedirectResponse("/admin/benutzer", status_code=303)
    new_pw = sec.token_urlsafe(12)
    u.password_hash = hash_password(new_pw)
    write_audit(db, "admin.user.password_reset", user_id=request.state.user.id,
                entity_type="user", entity_id=user_id)
    db.commit()
    users = db.query(User).order_by(User.username).all()
    roles = db.query(Role).all()
    return templates.TemplateResponse(request, "admin/users.html", {
        "user": request.state.user,
        "users": users, "roles": roles,
        "new_password": new_pw, "new_password_user": u.username,
    })


# ── Fahrzeuge CRUD ────────────────────────────────────────────────────────────

@router.get("/fahrzeuge", response_class=HTMLResponse)
async def vehicles_list(request: Request, db: Session = Depends(get_db),
                        _=Depends(require_role("admin", "org_admin"))):
    from app.core.permissions import has_role
    user = request.state.user
    is_sysadmin = has_role(user, "system_admin")
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    vehicles = (
        db.query(VehicleMaster)
        .join(VehicleMaster.dept)
        .order_by(FireDept.name, VehicleMaster.display_order)
        .all()
    )
    saved = request.query_params.get("saved")
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "admin/vehicles.html", {
        "user": user, "vehicles": vehicles, "all_orgs": all_orgs,
        "is_sysadmin": is_sysadmin, "saved": saved, "error": error,
    })


@router.post("/fahrzeuge/neu")
async def create_vehicle(
    request: Request,
    code: str = Form(...), name: str = Form(...), type: str = Form(""),
    is_first_train: str = Form(""),
    dept_id: Optional[int] = Form(None),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    from app.core.permissions import has_role
    user = request.state.user
    target_dept_id = dept_id if (has_role(user, "system_admin") and dept_id) else None
    if not target_dept_id:
        home = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        target_dept_id = home.id if home else None
    if not target_dept_id:
        return RedirectResponse("/admin/fahrzeuge?error=no_org", status_code=303)
    max_order = db.query(VehicleMaster).filter(VehicleMaster.dept_id == target_dept_id).count()
    v = VehicleMaster(
        dept_id=target_dept_id, code=code, name=name, type=type,
        is_first_train=bool(is_first_train),
        display_order=max_order,
    )
    db.add(v)
    write_audit(db, "admin.vehicle.created", user_id=user.id,
                entity_type="vehicle_master", entity_id=v.id if v.id else 0)
    db.commit()
    return RedirectResponse("/admin/fahrzeuge?saved=1", status_code=303)


@router.post("/fahrzeuge/{vehicle_id}/edit")
async def edit_vehicle(
    vehicle_id: int, request: Request,
    code: str = Form(...), name: str = Form(...), type: str = Form(""),
    is_first_train: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    v = db.get(VehicleMaster, vehicle_id)
    if v:
        v.code = code
        v.name = name
        v.type = type
        v.is_first_train = bool(is_first_train)
        write_audit(db, "admin.vehicle.edited", user_id=request.state.user.id,
                    entity_type="vehicle_master", entity_id=vehicle_id)
        db.commit()
    return RedirectResponse("/admin/fahrzeuge?saved=1", status_code=303)


@router.post("/fahrzeuge/{vehicle_id}/toggle")
async def toggle_vehicle(
    vehicle_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    v = db.get(VehicleMaster, vehicle_id)
    if v:
        v.active = not v.active
        db.commit()
    return RedirectResponse("/admin/fahrzeuge", status_code=303)


@router.post("/fahrzeuge/{vehicle_id}/order/{direction}")
async def reorder_vehicle(
    vehicle_id: int, direction: str, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    v = db.get(VehicleMaster, vehicle_id)
    if not v:
        return RedirectResponse("/admin/fahrzeuge", status_code=303)
    siblings = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == v.dept_id)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    idx = next((i for i, s in enumerate(siblings) if s.id == vehicle_id), None)
    if idx is not None:
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(siblings):
            siblings[idx].display_order, siblings[swap_idx].display_order = (
                siblings[swap_idx].display_order, siblings[idx].display_order
            )
            db.commit()
    return RedirectResponse("/admin/fahrzeuge", status_code=303)


# ── Auftragsvorlagen CRUD ─────────────────────────────────────────────────────

@router.get("/auftragsvorlagen", response_class=HTMLResponse)
async def task_suggestions_list(request: Request, db: Session = Depends(get_db),
                                _=Depends(require_role("admin", "org_admin"))):
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    suggestions = db.query(TaskSuggestion).order_by(
        TaskSuggestion.alarm_type_code, TaskSuggestion.display_order
    ).all()
    from itertools import groupby
    by_alarm_type = []
    sugg_by_code: dict = {}
    for s in suggestions:
        sugg_by_code.setdefault(s.alarm_type_code, []).append(s)
    for at in alarm_types:
        by_alarm_type.append((at, sugg_by_code.get(at.code, [])))
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(request, "admin/task_suggestions.html", {
        "user": request.state.user, "alarm_types": alarm_types,
        "by_alarm_type": by_alarm_type, "saved": saved,
    })


@router.post("/auftragsvorlagen/neu")
async def create_task_suggestion(
    request: Request,
    alarm_type_code: str = Form(...), text: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    max_order = db.query(TaskSuggestion).filter(
        TaskSuggestion.alarm_type_code == alarm_type_code
    ).count()
    s = TaskSuggestion(alarm_type_code=alarm_type_code, text=text, display_order=max_order)
    db.add(s)
    db.commit()
    return RedirectResponse("/admin/auftragsvorlagen?saved=1", status_code=303)


@router.post("/auftragsvorlagen/{sid}/edit")
async def edit_task_suggestion(
    sid: int, request: Request, text: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    s = db.get(TaskSuggestion, sid)
    if s:
        s.text = text
        db.commit()
    return RedirectResponse("/admin/auftragsvorlagen?saved=1", status_code=303)


@router.post("/auftragsvorlagen/{sid}/loeschen")
async def delete_task_suggestion(
    sid: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    s = db.get(TaskSuggestion, sid)
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/admin/auftragsvorlagen", status_code=303)


@router.post("/auftragsvorlagen/{sid}/order/{direction}")
async def reorder_task_suggestion(
    sid: int, direction: str, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    s = db.get(TaskSuggestion, sid)
    if not s:
        return RedirectResponse("/admin/auftragsvorlagen", status_code=303)
    siblings = (
        db.query(TaskSuggestion)
        .filter(TaskSuggestion.alarm_type_code == s.alarm_type_code)
        .order_by(TaskSuggestion.display_order)
        .all()
    )
    idx = next((i for i, x in enumerate(siblings) if x.id == sid), None)
    if idx is not None:
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(siblings):
            siblings[idx].display_order, siblings[swap_idx].display_order = (
                siblings[swap_idx].display_order, siblings[idx].display_order
            )
            db.commit()
    return RedirectResponse("/admin/auftragsvorlagen", status_code=303)


# ── Alarmtypen CRUD ───────────────────────────────────────────────────────────

@router.get("/alarmtypen", response_class=HTMLResponse)
async def alarm_types_list(request: Request, db: Session = Depends(get_db),
                           _=Depends(require_role("admin"))):
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    saved = request.query_params.get("saved")
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "admin/alarm_types.html", {
        "user": request.state.user, "alarm_types": alarm_types, "saved": saved, "error": error,
    })


@router.post("/alarmtypen/neu")
async def create_alarm_type(
    request: Request,
    code: str = Form(...), category: str = Form("T"), label: str = Form(""),
    default_first_train_only: str = Form(""), notify_neighbors: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    code = code.upper()
    existing = db.get(AlarmType, code)
    if existing:
        return RedirectResponse("/admin/alarmtypen?error=exists", status_code=303)
    at = AlarmType(
        code=code, category=category, label=label,
        default_first_train_only=bool(default_first_train_only),
        notify_neighbors=bool(notify_neighbors),
    )
    db.add(at)
    db.commit()
    return RedirectResponse("/admin/alarmtypen?saved=1", status_code=303)


@router.post("/alarmtypen/{code}/edit")
async def edit_alarm_type(
    code: str, request: Request,
    category: str = Form("T"), label: str = Form(""),
    default_first_train_only: str = Form(""), notify_neighbors: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    at = db.get(AlarmType, code)
    if at:
        at.category = category
        at.label = label
        at.default_first_train_only = bool(default_first_train_only)
        at.notify_neighbors = bool(notify_neighbors)
        db.commit()
    return RedirectResponse("/admin/alarmtypen?saved=1", status_code=303)


@router.post("/alarmtypen/{code}/loeschen")
async def delete_alarm_type(
    code: str, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    from app.models.incident import Incident
    count = db.query(Incident).filter(Incident.alarm_type_code == code).count()
    if count > 0:
        return RedirectResponse(f"/admin/alarmtypen?error=in_use", status_code=303)
    at = db.get(AlarmType, code)
    if at:
        db.delete(at)
        db.commit()
    return RedirectResponse("/admin/alarmtypen", status_code=303)


# ── Ausrückordnung ────────────────────────────────────────────────────────────

@router.get("/ausrueckordnung", response_class=HTMLResponse)
async def dispatch_order_list(request: Request, db: Session = Depends(get_db),
                              _=Depends(require_role("admin", "org_admin"))):
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    home = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
    vehicles = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == home.id if home else True, VehicleMaster.active == True)  # noqa: E712
        .order_by(VehicleMaster.display_order)
        .all()
    )
    dispatch_entries = db.query(AlarmDispatchVehicle).order_by(
        AlarmDispatchVehicle.alarm_type_code, AlarmDispatchVehicle.display_order
    ).all()
    dispatch_map: dict = {}
    for e in dispatch_entries:
        dispatch_map.setdefault(e.alarm_type_code, []).append(e.vehicle_master_id)
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(request, "admin/dispatch_order.html", {
        "user": request.state.user, "alarm_types": alarm_types, "vehicles": vehicles,
        "dispatch_map": dispatch_map, "saved": saved,
    })


@router.post("/ausrueckordnung/{alarm_type_code}")
async def save_dispatch_order(
    alarm_type_code: str, request: Request,
    vehicle_ids: list[int] = Form([]),
    vehicle_order: str = Form(""),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    # Parse order string if provided, else use checkbox order
    order: list[int] = []
    if vehicle_order.strip():
        try:
            order = [int(x.strip()) for x in vehicle_order.split(",") if x.strip().isdigit()]
        except ValueError:
            pass
    if not order:
        order = vehicle_ids

    # Delete existing
    db.query(AlarmDispatchVehicle).filter(
        AlarmDispatchVehicle.alarm_type_code == alarm_type_code
    ).delete()
    for i, vid in enumerate(order):
        if vid in vehicle_ids or not vehicle_ids:
            db.add(AlarmDispatchVehicle(
                alarm_type_code=alarm_type_code,
                vehicle_master_id=vid,
                display_order=i,
            ))
    db.commit()
    return RedirectResponse("/admin/ausrueckordnung?saved=1", status_code=303)


@router.get("/ausrueckordnung/{alarm_type_code}/reset")
async def reset_dispatch_order(
    alarm_type_code: str, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    db.query(AlarmDispatchVehicle).filter(
        AlarmDispatchVehicle.alarm_type_code == alarm_type_code
    ).delete()
    db.commit()
    return RedirectResponse("/admin/ausrueckordnung?saved=1", status_code=303)


# ── Qualifikationen CRUD ──────────────────────────────────────────────────────

@router.get("/qualifikationen", response_class=HTMLResponse)
async def qualifications_list(request: Request, db: Session = Depends(get_db),
                              _=Depends(require_role("admin"))):
    qualifications = db.query(Qualification).order_by(Qualification.code).all()
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(request, "admin/qualifications.html", {
        "user": request.state.user, "qualifications": qualifications, "saved": saved,
    })


@router.post("/qualifikationen/neu")
async def create_qualification(
    request: Request, code: str = Form(...), label: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    q = Qualification(code=code.upper(), label=label)
    db.add(q)
    db.commit()
    return RedirectResponse("/admin/qualifikationen?saved=1", status_code=303)


@router.post("/qualifikationen/{qid}/edit")
async def edit_qualification(
    qid: int, request: Request, code: str = Form(...), label: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin")),
):
    q = db.get(Qualification, qid)
    if q:
        q.code = code.upper()
        q.label = label
        db.commit()
    return RedirectResponse("/admin/qualifikationen?saved=1", status_code=303)


@router.post("/qualifikationen/{qid}/loeschen")
async def delete_qualification(
    qid: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    q = db.get(Qualification, qid)
    if q:
        db.delete(q)
        db.commit()
    return RedirectResponse("/admin/qualifikationen", status_code=303)


# ── Lage-Hinweise CRUD ────────────────────────────────────────────────────────

@router.get("/lage-hinweise", response_class=HTMLResponse)
async def lage_hints_list(request: Request, db: Session = Depends(get_db),
                          _=Depends(require_role("admin", "org_admin"))):
    hints = db.query(LageHint).order_by(LageHint.display_order).all()
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(request, "admin/lage_hints.html", {
        "user": request.state.user, "hints": hints, "saved": saved,
    })


@router.post("/lage-hinweise/neu")
async def create_lage_hint(
    request: Request, text: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    max_order = db.query(LageHint).count()
    db.add(LageHint(text=text, display_order=max_order))
    db.commit()
    return RedirectResponse("/admin/lage-hinweise?saved=1", status_code=303)


@router.post("/lage-hinweise/{hid}/edit")
async def edit_lage_hint(
    hid: int, request: Request, text: str = Form(...),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    h = db.get(LageHint, hid)
    if h:
        h.text = text
        db.commit()
    return RedirectResponse("/admin/lage-hinweise?saved=1", status_code=303)


@router.post("/lage-hinweise/{hid}/loeschen")
async def delete_lage_hint(
    hid: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    h = db.get(LageHint, hid)
    if h:
        db.delete(h)
        db.commit()
    return RedirectResponse("/admin/lage-hinweise", status_code=303)


@router.post("/lage-hinweise/{hid}/order/{direction}")
async def reorder_lage_hint(
    hid: int, direction: str, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    h = db.get(LageHint, hid)
    if not h:
        return RedirectResponse("/admin/lage-hinweise", status_code=303)
    siblings = db.query(LageHint).order_by(LageHint.display_order).all()
    idx = next((i for i, x in enumerate(siblings) if x.id == hid), None)
    if idx is not None:
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(siblings):
            siblings[idx].display_order, siblings[swap_idx].display_order = (
                siblings[swap_idx].display_order, siblings[idx].display_order
            )
            db.commit()
    return RedirectResponse("/admin/lage-hinweise", status_code=303)


# ── Default-Meldungen CRUD ────────────────────────────────────────────────────

@router.get("/default-meldungen", response_class=HTMLResponse)
async def default_messages_list(request: Request, db: Session = Depends(get_db),
                                _=Depends(require_role("admin", "org_admin"))):
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    msgs = db.query(DefaultMessage).order_by(DefaultMessage.alarm_type_code, DefaultMessage.due_after_sec).all()
    msg_by_code: dict = {}
    for m in msgs:
        msg_by_code.setdefault(m.alarm_type_code, []).append(m)
    by_alarm_type = [(at, msg_by_code.get(at.code, [])) for at in alarm_types]
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(request, "admin/default_messages.html", {
        "user": request.state.user, "alarm_types": alarm_types,
        "by_alarm_type": by_alarm_type, "saved": saved,
    })


@router.post("/default-meldungen/neu")
async def create_default_message(
    request: Request,
    alarm_type_code: str = Form(...), text: str = Form(...), due_after_sec: int = Form(300),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    db.add(DefaultMessage(alarm_type_code=alarm_type_code, text=text, due_after_sec=due_after_sec))
    db.commit()
    return RedirectResponse("/admin/default-meldungen?saved=1", status_code=303)


@router.post("/default-meldungen/{mid}/edit")
async def edit_default_message(
    mid: int, request: Request, text: str = Form(...), due_after_sec: int = Form(300),
    db: Session = Depends(get_db), _=Depends(require_role("admin", "org_admin")),
):
    m = db.get(DefaultMessage, mid)
    if m:
        m.text = text
        m.due_after_sec = due_after_sec
        db.commit()
    return RedirectResponse("/admin/default-meldungen?saved=1", status_code=303)


@router.post("/default-meldungen/{mid}/loeschen")
async def delete_default_message(
    mid: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    m = db.get(DefaultMessage, mid)
    if m:
        db.delete(m)
        db.commit()
    return RedirectResponse("/admin/default-meldungen", status_code=303)


# ── System-Einstellungen ──────────────────────────────────────────────────────

@router.get("/system-einstellungen", response_class=HTMLResponse)
async def system_settings_page(request: Request, db: Session = Depends(get_db),
                               _=Depends(require_role("system_admin"))):
    settings_raw = db.query(SystemSettings).all()
    settings = {s.key: s.value for s in settings_raw}
    saved = request.query_params.get("saved")
    return templates.TemplateResponse(request, "admin/system_settings.html", {
        "user": request.state.user, "settings": settings, "saved": saved,
    })


@router.post("/system-einstellungen")
async def save_system_settings(
    request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
    new_key: str = Form(""), new_value: str = Form(""),
):
    form = await request.form()
    known_keys = [
        "vapid_public_key", "vapid_private_key", "vapid_email",
        "enable_push", "enable_speech_input", "default_session_max_age",
    ]
    for key in known_keys:
        val = form.get(f"k_{key}")
        if val is not None:
            existing = db.get(SystemSettings, key)
            if existing:
                existing.value = val
                existing.updated_at = datetime.now(timezone.utc)
                existing.updated_by_user_id = request.state.user.id
            else:
                db.add(SystemSettings(key=key, value=val,
                                      updated_by_user_id=request.state.user.id))
    # Handle any existing custom keys
    all_existing = db.query(SystemSettings).all()
    for s in all_existing:
        if s.key not in known_keys:
            val = form.get(f"k_{s.key}")
            if val is not None:
                s.value = val
    # New custom key
    if new_key.strip():
        existing = db.get(SystemSettings, new_key.strip())
        if existing:
            existing.value = new_value
        else:
            db.add(SystemSettings(key=new_key.strip(), value=new_value,
                                  updated_by_user_id=request.state.user.id))
    write_audit(db, "admin.system_settings.updated", user_id=request.state.user.id)
    db.commit()
    return RedirectResponse("/admin/system-einstellungen?saved=1", status_code=303)


# ── Backup / Export ───────────────────────────────────────────────────────────

@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, _=Depends(require_role("system_admin"))):
    return templates.TemplateResponse(request, "admin/backup.html", {
        "user": request.state.user,
    })


@router.get("/backup/json")
async def backup_json(request: Request, db: Session = Depends(get_db),
                      _=Depends(require_role("system_admin"))):
    import json as json_lib
    from fastapi.responses import JSONResponse
    data = {
        "vehicles": [
            {"id": v.id, "dept_slug": v.dept.slug, "code": v.code, "name": v.name,
             "type": v.type, "is_first_train": v.is_first_train, "display_order": v.display_order}
            for v in db.query(VehicleMaster).all()
        ],
        "members": [
            {"id": m.id, "org_slug": m.org.slug if m.org else None,
             "lastname": m.lastname, "firstname": m.firstname, "phone": m.phone, "email": m.email,
             "qualifications": [{"code": mq.qualification.code} for mq in m.qualifications if mq.qualification]}
            for m in db.query(Member).all()
        ],
        "qualifications": [{"code": q.code, "label": q.label} for q in db.query(Qualification).all()],
        "alarm_types": [
            {"code": at.code, "category": at.category, "label": at.label,
             "default_first_train_only": at.default_first_train_only, "notify_neighbors": at.notify_neighbors}
            for at in db.query(AlarmType).all()
        ],
        "task_suggestions": [
            {"alarm_type_code": s.alarm_type_code, "text": s.text, "display_order": s.display_order}
            for s in db.query(TaskSuggestion).order_by(TaskSuggestion.alarm_type_code, TaskSuggestion.display_order).all()
        ],
        "lage_hints": [{"text": h.text, "display_order": h.display_order} for h in db.query(LageHint).all()],
        "default_messages": [
            {"alarm_type_code": m.alarm_type_code, "text": m.text, "due_after_sec": m.due_after_sec}
            for m in db.query(DefaultMessage).all()
        ],
        "alarm_dispatch": [
            {"alarm_type_code": e.alarm_type_code, "vehicle_master_id": e.vehicle_master_id, "display_order": e.display_order}
            for e in db.query(AlarmDispatchVehicle).order_by(AlarmDispatchVehicle.alarm_type_code, AlarmDispatchVehicle.display_order).all()
        ],
    }
    from fastapi.responses import Response as FR
    content = json_lib.dumps(data, indent=2, ensure_ascii=False)
    return FR(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=stammdaten-backup.json"},
    )


@router.post("/backup/restore", response_class=HTMLResponse)
async def backup_restore(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
    backup_file: UploadFile = File(...),
):
    import json as json_lib
    lines = []
    try:
        raw = await backup_file.read()
        data = json_lib.loads(raw)

        dept_map = {d.slug: d for d in db.query(FireDept).all()}

        # Qualifications
        for q in data.get("qualifications", []):
            obj = db.query(Qualification).filter(Qualification.code == q["code"]).first()
            if not obj:
                db.add(Qualification(code=q["code"], label=q["label"]))
            else:
                obj.label = q["label"]
        db.flush()
        lines.append(f"Qualifikationen: {len(data.get('qualifications', []))} verarbeitet")

        # AlarmTypes
        for at in data.get("alarm_types", []):
            obj = db.get(AlarmType, at["code"])
            if not obj:
                db.add(AlarmType(**at))
            else:
                for k, v in at.items():
                    setattr(obj, k, v)
        db.flush()
        lines.append(f"Alarmtypen: {len(data.get('alarm_types', []))} verarbeitet")

        # Vehicles
        for v in data.get("vehicles", []):
            dept = dept_map.get(v.get("dept_slug"))
            if not dept:
                lines.append(f"  Warnung: Wehr '{v.get('dept_slug')}' nicht gefunden, Fahrzeug '{v.get('code')}' übersprungen")
                continue
            obj = db.query(VehicleMaster).filter(VehicleMaster.code == v["code"]).first()
            if not obj:
                db.add(VehicleMaster(
                    dept_id=dept.id, code=v["code"], name=v["name"],
                    type=v.get("type", ""), is_first_train=v.get("is_first_train", False),
                    display_order=v.get("display_order", 0),
                ))
            else:
                obj.name = v["name"]; obj.type = v.get("type", obj.type)
                obj.is_first_train = v.get("is_first_train", obj.is_first_train)
                obj.display_order = v.get("display_order", obj.display_order)
        db.flush()
        lines.append(f"Fahrzeuge: {len(data.get('vehicles', []))} verarbeitet")

        # TaskSuggestions – replace all
        if "task_suggestions" in data:
            db.query(TaskSuggestion).delete()
            for s in data["task_suggestions"]:
                db.add(TaskSuggestion(alarm_type_code=s["alarm_type_code"],
                                      text=s["text"], display_order=s.get("display_order", 0)))
            lines.append(f"Auftragsvorlagen: {len(data['task_suggestions'])} importiert")

        # LageHints – replace all
        if "lage_hints" in data:
            db.query(LageHint).delete()
            for h in data["lage_hints"]:
                db.add(LageHint(text=h["text"], display_order=h.get("display_order", 0)))
            lines.append(f"Lage-Hinweise: {len(data['lage_hints'])} importiert")

        # DefaultMessages – replace all
        if "default_messages" in data:
            db.query(DefaultMessage).delete()
            for m in data["default_messages"]:
                db.add(DefaultMessage(alarm_type_code=m["alarm_type_code"],
                                      text=m["text"], due_after_sec=m.get("due_after_sec", 300)))
            lines.append(f"Default-Meldungen: {len(data['default_messages'])} importiert")

        # AlarmDispatch – replace all
        if "alarm_dispatch" in data:
            db.query(AlarmDispatchVehicle).delete()
            vm_map = {v.id: v for v in db.query(VehicleMaster).all()}
            for e in data["alarm_dispatch"]:
                vid = e.get("vehicle_master_id")
                if vid and vid in vm_map:
                    db.add(AlarmDispatchVehicle(
                        alarm_type_code=e["alarm_type_code"],
                        vehicle_master_id=vid,
                        display_order=e.get("display_order", 0),
                    ))
            lines.append(f"Ausrückordnung: {len(data['alarm_dispatch'])} Einträge importiert")

        db.commit()
        result = "Import erfolgreich:\n" + "\n".join(lines)
    except Exception as exc:
        db.rollback()
        result = f"Fehler beim Import: {exc}"

    return templates.TemplateResponse(request, "admin/backup.html", {
        "user": request.state.user,
        "import_result": result,
    })
