"""2FA fuer persoenliche Benutzer; alle Mutationen sind CSRF-geschuetzt."""
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import decrypt_secret
from app.core.rate_limit import limiter
from app.core.security import session_login_method, sign_session, unsign_session
from app.core.templating import templates
from app.db import get_db
from app.models.admin_security import AdminTotp, UserMfaSms
from app.models.user import User
from app.routers.auth import _safe_next, _set_session_cookie
from app.services import mfa_service as totp
from app.services import mfa_sms_service as sms


def require_personal_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    if (user.is_device or request.state.is_device or request.state.qr_incident_id is not None
            or request.state.qr_lage_id is not None or user.username.startswith("alarmlink-gast@org")):
        raise HTTPException(403, "Nur persoenliche Benutzerkonten")
    return user


router = APIRouter(prefix="/mfa", dependencies=[Depends(require_personal_user)])


def _user(request: Request, db: Session) -> User:
    require_personal_user(request)
    user = db.get(User, request.state.user.id)
    if user is None:
        raise HTTPException(401)
    return user


def _destination(user: User, next_url: str) -> str:
    return _safe_next(next_url) if next_url else ("/admin/system-einstellungen" if user.is_system_admin else "/")


def _page(request: Request, mode: str, *, status_code: int = 200, **context):
    response = templates.TemplateResponse(request, "mfa/form.html", {
        "user": request.state.user, "mode": mode, "next": _safe_next(request.query_params.get("next"))
        if request.query_params.get("next") else "", **context,
    }, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _verified_cookie(request: Request, user: User, response) -> None:
    old = unsign_session(request.cookies.get("session", ""))
    remember = bool(old and old[6])
    now = int(time.time())
    _set_session_cookie(response, sign_session(
        user.id, session_epoch=user.session_epoch, mfa_at=now, session_started_at=now, remember=remember,
        login_method=session_login_method(request.cookies.get("session", "")),
    ), max_age=settings.SESSION_REMEMBER_MAX_AGE_SECONDS if remember else None)


@router.get("/verify")
def verify_page(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    return _verify_page(request, db, user)


def _verify_page(request: Request, db: Session, user: User, **context):
    if not totp.status(db, user)["aktiv"]:
        return _page(request, "unconfigured", **context)
    factor = db.get(UserMfaSms, user.id)
    has_sms = factor is not None and factor.verified_at is not None
    method_ok = session_login_method(request.cookies.get("session", "")) in {"pw", "sso"}
    return _page(request, "verify", sms_enabled=has_sms and method_ok and sms.sms_allowed(db, user),
                 sms_login_blocked=has_sms and not method_ok,
                 sms_unavailable=has_sms and method_ok and not sms.sms_allowed(db, user), **context)


@router.post("/verify")
def verify_submit(request: Request, code: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    success = totp.verify(db, user.id, code)
    db.commit()
    if not success:
        return _verify_page(request, db, user,
                     error="Code ungueltig, bereits verwendet oder Zugang voruebergehend gesperrt.",
                     next=_safe_next(next))
    response = RedirectResponse(_destination(user, next), status_code=303)
    _verified_cookie(request, user, response)
    return response


@router.get("/einrichten")
def setup_page(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if totp.setup_expired(db, user):
        return _page(request, "expired", status_code=403)
    sms_factor = db.get(UserMfaSms, user.id)
    if sms_factor and sms_factor.verified_at and not totp.fresh(request.state.mfa_at):
        raise HTTPException(403, "Zum Hinzufuegen eines Faktors zuerst 2FA erneut bestaetigen")
    row = totp.start_setup(db, user)
    if row.confirmed_at:
        db.commit()
        return _page(request, "confirmed", sms_allowed=sms.sms_allowed(db, user))
    # Vor der Bestaetigung einmal anzeigen; nachher direkt zum urspruenglichen Ziel.
    codes = [] if sms_factor and sms_factor.verified_at else totp.create_recovery_codes(db, user.id)
    db.commit()
    secret = decrypt_secret(row.secret_enc)
    return _page(request, "setup", secret=secret, codes=codes,
                 qr=totp.qr_data_uri(totp.provisioning_uri(secret, user.username)),
                 sms_allowed=sms.sms_allowed(db, user))


@router.post("/bestaetigen")
def confirm_submit(request: Request, code: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    if totp.setup_expired(db, user):
        return _page(request, "expired", status_code=403)
    sms_factor = db.get(UserMfaSms, user.id)
    if sms_factor and sms_factor.verified_at and not totp.fresh(request.state.mfa_at):
        raise HTTPException(403, "Zum Hinzufuegen eines Faktors zuerst 2FA erneut bestaetigen")
    row = db.get(AdminTotp, user.id)
    if row is None or row.confirmed_at:
        raise HTTPException(400, "Keine offene Einrichtung")
    if not totp.verify(db, user.id, code, confirming=True):
        db.commit()
        return _page(request, "retry", error="Code ungueltig oder Zugang voruebergehend gesperrt.",
                     next=_safe_next(next))
    db.commit()
    response = RedirectResponse(_destination(user, next), status_code=303)
    _verified_cookie(request, user, response)
    return response


@router.post("/zuruecksetzen")
def reset_submit(
    request: Request, code: str = Form(""), user_id: int | None = Form(None),
    grund: str = Form("Systemadministration"), db: Session = Depends(get_db),
):
    actor = _user(request, db)
    if not actor.is_system_admin:
        raise HTTPException(403, "Nur Systemadministratoren haben Zugriff")
    target = db.query(User).execution_options(include_all_tenants=True).filter(
        User.id == (user_id or actor.id),
    ).first()
    if not target or target.is_device:
        raise HTTPException(404)
    phone = None
    if target.id == actor.id:
        success = totp.verify(db, actor.id, code)
        db.commit()
        if not success:
            raise HTTPException(403, "Gueltiger TOTP- oder Recovery-Code erforderlich")
        totp.reset(db, target, actor.id)
    else:
        phone = totp.admin_reset(db, actor, target, grund, request.state.mfa_at)
    db.commit()
    totp.notify_reset(db, target)
    sms.notify_reset_sms(target, phone)
    response = RedirectResponse("/login" if target.id == actor.id else "/admin/system-einstellungen", status_code=303)
    if target.id == actor.id:
        response.delete_cookie("session", path="/")
    return response


@router.post("/global")
def global_submit(request: Request, enabled: str = Form("false"), db: Session = Depends(get_db)):
    actor = _user(request, db)
    if not actor.is_system_admin:
        raise HTTPException(403, "Nur Systemadministratoren haben Zugriff")
    try:
        totp.set_enabled(db, enabled == "true", actor.id, request.state.mfa_at)
    except ValueError as exc:
        return _page(request, "error", error=str(exc))
    db.commit()
    return RedirectResponse("/admin/system-einstellungen", status_code=303)


_SMS_ERRORS = {
    "limit": "Zu viele SMS angefordert. Bitte spaeter erneut versuchen oder Recovery-Code verwenden.",
    "phone": "Bitte eine gueltige Telefonnummer mit Vorwahl eingeben.",
    "locked": "Zugang voruebergehend gesperrt. Bitte spaeter erneut versuchen.",
}


def _sms_error(exc: ValueError) -> str:
    return _SMS_ERRORS.get(str(exc), "SMS-Gateway nicht verfuegbar. Bitte Recovery-Code oder TOTP verwenden.")


def _require_sms_login(request: Request, db: Session, user: User) -> None:
    if session_login_method(request.cookies.get("session", "")) not in {"pw", "sso"}:
        raise HTTPException(403, "Nach Anmeldung per SMS-PIN bitte mit Passwort anmelden "
                            "oder einen Recovery-Code verwenden.")
    if not sms.sms_allowed(db, user):
        raise HTTPException(403, "SMS nicht verfuegbar. Bitte Recovery-Code oder TOTP verwenden.")


@router.post("/sms/senden")
@(limiter.limit("5/minute") if limiter else lambda f: f)
def sms_send(request: Request, next: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    _require_sms_login(request, db, user)
    try:
        sms.send_code(db, user, "mfa")
    except ValueError as exc:
        db.commit()
        return _verify_page(request, db, user, error=_sms_error(exc), next=_safe_next(next))
    db.commit()
    return _page(request, "sms_verify", next=_safe_next(next))


@router.post("/sms/verify")
@(limiter.limit("20/minute") if limiter else lambda f: f)
def sms_verify(request: Request, code: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    _require_sms_login(request, db, user)
    success = sms.verify_sms(db, user, code, "mfa")
    db.commit()
    if not success:
        return _page(request, "sms_verify", error="Code ungueltig, abgelaufen oder Zugang gesperrt.",
                     next=_safe_next(next))
    response = RedirectResponse(_destination(user, next), status_code=303)
    _verified_cookie(request, user, response)
    return response


def _require_sms_setup(request: Request, db: Session, user: User) -> None:
    if not sms.sms_allowed(db, user):
        raise HTTPException(403, "SMS als zweiter Faktor nicht verfuegbar")
    if totp.setup_expired(db, user):
        raise HTTPException(403, "2FA-Reset abgelaufen, Admin kontaktieren")
    factor = db.get(UserMfaSms, user.id)
    if totp.status(db, user)["aktiv"] and not totp.fresh(request.state.mfa_at):
        detail = ("Zum Nummernwechsel zuerst 2FA erneut bestaetigen" if factor and factor.verified_at
                  else "Zum Hinzufuegen eines Faktors zuerst 2FA erneut bestaetigen")
        raise HTTPException(403, detail)


@router.get("/sms/einrichten")
def sms_setup_page(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    _require_sms_setup(request, db, user)
    return _page(request, "sms_setup", phone=user.phone or "")


@router.post("/sms/einrichten")
@(limiter.limit("5/minute") if limiter else lambda f: f)
def sms_setup_submit(request: Request, phone: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    _require_sms_setup(request, db, user)
    try:
        # Bei Versandfehlern bleibt ein bestehender Faktor erhalten.
        with db.begin_nested():
            sms.start_sms_setup(db, user, phone)
    except ValueError as exc:
        db.commit()
        return _page(request, "sms_setup", error=_sms_error(exc), phone=phone, next=_safe_next(next))
    db.commit()
    return _page(request, "sms_confirm", next=_safe_next(next))


@router.post("/sms/bestaetigen")
@(limiter.limit("20/minute") if limiter else lambda f: f)
def sms_setup_confirm(request: Request, code: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    _require_sms_setup(request, db, user)
    codes = sms.confirm_sms_setup(db, user, code)
    db.commit()
    if codes is None:
        return _page(request, "sms_confirm", error="Code ungueltig, abgelaufen oder Zugang gesperrt.",
                     next=_safe_next(next))
    sms.notify_setup(db, user)
    response = (_page(request, "recovery", codes=codes, next=_destination(user, next)) if codes
                else RedirectResponse(_destination(user, next), status_code=303))
    _verified_cookie(request, user, response)
    return response


@router.post("/sms/entfernen")
def sms_remove(request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    if not totp.fresh(request.state.mfa_at):
        raise HTTPException(403, "Zuerst 2FA erneut bestaetigen")
    factor = db.get(UserMfaSms, user.id)
    other = db.get(AdminTotp, user.id)
    if totp.required_for(db, user) and not (other and other.confirmed_at):
        raise HTTPException(400, "Die Zwei-Faktor-Authentifizierung ist vorgeschrieben")
    if factor:
        db.delete(factor)
        from app.models.login_pin import LoginPin

        for pin in db.query(LoginPin).filter(
            LoginPin.user_id == user.id, LoginPin.purpose.in_(("mfa", "mfa_setup")), LoginPin.used_at.is_(None),
        ).all():
            pin.used_at = totp.utcnow()
        totp.audit(db, "sms_removed", user.id)
    db.commit()
    return RedirectResponse("/profil", status_code=303)
