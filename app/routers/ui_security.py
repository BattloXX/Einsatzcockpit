"""Org-Richtlinie fuer persoenliche Anmeldungen und Zwei-Faktor-Authentifizierung."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.org_auswahl import erste_org_weiterleitung
from app.core.permissions import ORG_ADMIN_ROLES, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.admin_security import AdminTotp
from app.models.master import FireDept, OrgSettings
from app.models.user import User
from app.services import mfa_service

router = APIRouter(prefix="/admin", dependencies=[Depends(require_role("admin"))])
_FIELDS = ("mfa_policy", "mfa_sms_erlaubt", "mfa_sso_ausgenommen")


def _org_id(user: User, target: int | None) -> int:
    if target is not None and not user.is_system_admin and target != user.org_id:
        raise HTTPException(403, "Keine Berechtigung fuer diese Organisation")
    org_id = target if user.is_system_admin and target is not None else user.org_id
    if org_id is None:
        raise HTTPException(400, "Bitte eine Organisation auswaehlen")
    return org_id


def _page(request: Request, db: Session, user: User, org_id: int, *, saved: bool = False):
    org = db.get(FireDept, org_id)
    if org is None:
        raise HTTPException(404, "Organisation nicht gefunden")
    config = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    confirmed = {row[0] for row in db.query(AdminTotp.user_id).join(User, User.id == AdminTotp.user_id).filter(
        User.org_id == org_id, AdminTotp.confirmed_at.is_not(None),
    ).all()}
    users = db.query(User).filter(User.org_id == org_id, User.active.is_(True), User.is_device.is_(False)).all()
    missing = [u for u in users if not u.is_system_admin and u.id not in confirmed
               and not u.username.startswith("alarmlink-gast@org")]
    counts = {
        policy + ("_sso" if exempt else ""): sum(
            1 for u in missing if not (exempt and u.entra_oid)
            and (policy == "alle" or policy == "admins" and bool(u.role_codes & ORG_ADMIN_ROLES))
        )
        for policy in mfa_service.MFA_POLICIES for exempt in (False, True)
    }
    own_factor = db.get(AdminTotp, user.id)
    warning = saved and not user.is_system_admin and mfa_service.required_for(db, user) and (
        own_factor is None or own_factor.confirmed_at is None
    )
    return templates.TemplateResponse(request, "admin/security.html", {
        "user": user, "org": org, "config": config, "counts": counts, "saved": saved, "warning": warning,
        "all_orgs": db.query(FireDept).order_by(FireDept.name).all() if user.is_system_admin else [],
    })


@router.get("/sicherheit")
def security_page(request: Request, org_id: int | None = None, db: Session = Depends(get_db)):
    user = request.state.user
    if (redirect := erste_org_weiterleitung(request, db, user, org_id)) is not None:
        return redirect
    return _page(request, db, user, _org_id(user, org_id))


@router.post("/sicherheit")
def security_save(
    request: Request, org_id: int | None = Form(None), mfa_policy: str = Form(...),
    mfa_sms_erlaubt: bool = Form(False), mfa_sso_ausgenommen: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = request.state.user
    target = _org_id(user, org_id)
    if db.get(FireDept, target) is None:
        raise HTTPException(404, "Organisation nicht gefunden")
    if mfa_policy not in mfa_service.MFA_POLICIES:
        raise HTTPException(400, "Ungueltige 2FA-Richtlinie")
    config = db.query(OrgSettings).filter(OrgSettings.org_id == target).first()
    if config is None:
        config = OrgSettings(org_id=target)
        db.add(config)
        db.flush()
    before = {field: getattr(config, field) for field in _FIELDS}
    config.mfa_policy = mfa_policy
    config.mfa_sms_erlaubt = mfa_sms_erlaubt
    config.mfa_sso_ausgenommen = mfa_sso_ausgenommen
    write_audit(db, "admin.mfa.policy_changed", org_id=target, user_id=user.id,
                payload={"alt": before, "neu": {field: getattr(config, field) for field in _FIELDS}})
    db.commit()
    mfa_service.clear_policy_cache()
    return _page(request, db, user, target, saved=True)
