"""Optionale 2FA fuer Systemadministratoren; alle Mutationen sind CSRF-geschuetzt."""
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import decrypt_secret
from app.core.permissions import require_role
from app.core.security import sign_session, unsign_session
from app.core.templating import templates
from app.db import get_db
from app.models.admin_security import AdminTotp
from app.models.user import User
from app.routers.auth import _set_session_cookie
from app.services import totp_service as totp

router = APIRouter(prefix="/mfa", dependencies=[Depends(require_role("system_admin"))])


def _user(request: Request, db: Session) -> User:
    if request.state.is_device or request.state.qr_incident_id or request.state.qr_lage_id:
        raise HTTPException(403, "Nur persoenliche Admin-Konten")
    user = db.get(User, request.state.user.id)
    if user is None:
        raise HTTPException(401)
    return user


def _page(request: Request, mode: str, **context):
    response = templates.TemplateResponse(request, "mfa/form.html", {
        "user": request.state.user, "mode": mode, **context,
    })
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _verified_cookie(request: Request, user: User, response) -> None:
    old = unsign_session(request.cookies.get("session", ""))
    remember = bool(old and old[6])
    now = int(time.time())
    _set_session_cookie(response, sign_session(
        user.id, session_epoch=user.session_epoch, mfa_at=now, session_started_at=now, remember=remember,
    ), max_age=settings.SESSION_REMEMBER_MAX_AGE_SECONDS if remember else None)


@router.get("/verify")
def verify_page(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    row = db.get(AdminTotp, user.id)
    if row is None or row.confirmed_at is None:
        # Keine Weiterleitung zum Secret: Offline-Caches koennten Redirect-Antworten
        # unter dem urspruenglichen Fachseiten-Pfad ablegen.
        return _page(request, "unconfigured")
    return _page(request, "verify")


@router.post("/verify")
def verify_submit(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    user = _user(request, db)
    success = totp.verify(db, user.id, code)
    db.commit()
    if not success:
        return _page(request, "verify", error="Code ungueltig, bereits verwendet oder Zugang voruebergehend gesperrt.")
    response = RedirectResponse("/admin/system-einstellungen", status_code=303)
    _verified_cookie(request, user, response)
    return response


@router.get("/einrichten")
def setup_page(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    row = totp.start_setup(db, user)
    db.commit()
    if row.confirmed_at:
        return _page(request, "confirmed")
    secret = decrypt_secret(row.secret_enc)
    return _page(request, "setup", secret=secret, qr=totp.qr_data_uri(totp.provisioning_uri(secret, user.username)))


@router.post("/bestaetigen")
def confirm_submit(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    user = _user(request, db)
    row = db.get(AdminTotp, user.id)
    if row is None or row.confirmed_at:
        raise HTTPException(400, "Keine offene Einrichtung")
    if not totp.verify(db, user.id, code, confirming=True):
        db.commit()
        return _page(request, "retry", error="Code ungueltig oder Zugang voruebergehend gesperrt.")
    codes = totp.create_recovery_codes(db, user.id)
    db.commit()
    response = _page(request, "recovery", codes=codes)
    _verified_cookie(request, user, response)
    return response


@router.post("/zuruecksetzen")
def reset_submit(
    request: Request, code: str = Form(""), user_id: int | None = Form(None), db: Session = Depends(get_db),
):
    actor = _user(request, db)
    target = db.query(User).execution_options(include_all_tenants=True).filter(
        User.id == (user_id or actor.id),
    ).first()
    if not target or not target.is_system_admin or target.is_device:
        raise HTTPException(404)
    if target.id == actor.id:
        success = totp.verify(db, actor.id, code)
        db.commit()
        if not success:
            raise HTTPException(403, "Gueltiger TOTP- oder Recovery-Code erforderlich")
    elif totp.enabled(db) and not totp.fresh(request.state.mfa_at):
        raise HTTPException(403, "Zuerst 2FA erneut bestaetigen (maximal 5 Minuten)")
    totp.reset(db, target, actor.id)
    db.commit()
    totp.notify_reset(db, target)
    response = RedirectResponse("/login" if target.id == actor.id else "/admin/system-einstellungen", status_code=303)
    if target.id == actor.id:
        response.delete_cookie("session", path="/")
    return response


@router.post("/global")
def global_submit(request: Request, enabled: str = Form("false"), db: Session = Depends(get_db)):
    actor = _user(request, db)
    try:
        totp.set_enabled(db, enabled == "true", actor.id, request.state.mfa_at)
    except ValueError as exc:
        return _page(request, "error", error=str(exc))
    db.commit()
    return RedirectResponse("/admin/system-einstellungen", status_code=303)
