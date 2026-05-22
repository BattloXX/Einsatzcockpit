"""Admin-UI: Stammdaten, User, Rollen, API-Keys."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.permissions import require_role
from app.core.security import hash_password, generate_api_key, hash_api_key
from app.core.audit import write_audit
from app.models.user import User, Role, UserRole, ApiKey, AuditLog
from app.models.master import Member, Qualification, MemberQualification, FireDept, VehicleMaster

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
