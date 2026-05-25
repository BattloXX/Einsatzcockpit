from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.security import sign_session, unsign_qr_token, verify_password
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, IncidentToken
from app.models.user import User

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.SESSION_MAX_AGE_SECONDS,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Login mit Account-Lockout (Phase 7).

    - Bei Fehlversuch wird `failed_login_count` erhöht.
    - Ab `LOGIN_MAX_FAILED` wird der Account `LOGIN_LOCKOUT_MINUTES` lang gesperrt.
    - Während Lockout wird IMMER der gleiche generische Fehler gezeigt (kein Enumerations-Leak).
    """
    now = datetime.now(UTC)
    generic_error = "Benutzername oder Passwort falsch"

    user = db.query(User).filter(User.username == username).first()
    if not user or not user.active:
        return templates.TemplateResponse(
            request, "login.html", {"error": generic_error},
            status_code=401,
        )

    # Lockout-Status prüfen
    if user.locked_until:
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until > now:
            write_audit(db, "auth.login.locked", user_id=user.id,
                        ip=request.client.host if request.client else None)
            db.commit()
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Account ist aktuell gesperrt. Bitte später erneut versuchen."},
                status_code=401,
            )
        # Lockout abgelaufen – zurücksetzen
        user.locked_until = None
        user.failed_login_count = 0

    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.LOGIN_MAX_FAILED:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            write_audit(db, "auth.login.lockout_triggered", user_id=user.id,
                        ip=request.client.host if request.client else None,
                        payload={"failed_count": user.failed_login_count})
        else:
            write_audit(db, "auth.login.failed", user_id=user.id,
                        ip=request.client.host if request.client else None,
                        payload={"failed_count": user.failed_login_count})
        db.commit()
        return templates.TemplateResponse(
            request, "login.html", {"error": generic_error},
            status_code=401,
        )

    # Erfolg
    user.last_login_at = now
    user.failed_login_count = 0
    user.locked_until = None
    write_audit(db, "auth.login", user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    token = sign_session(user.id)
    redirect = RedirectResponse("/", status_code=302)
    _set_session_cookie(redirect, token)
    return redirect


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if user:
        write_audit(db, "auth.logout", user_id=user.id)
        db.commit()
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session", path="/")
    return response


@router.get("/qr-login")
async def qr_login(request: Request, token: str, incident_id: int, db: Session = Depends(get_db)):
    """One-click login via QR-Code – valid for incident lifetime."""
    data = unsign_qr_token(token)
    if not data or data.get("incident_id") != incident_id:
        return RedirectResponse("/login?error=qr_invalid", status_code=302)

    incident = db.get(Incident, incident_id)
    if not incident or incident.status != "active":
        return RedirectResponse("/login?error=incident_closed", status_code=302)

    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db_token = db.query(IncidentToken).filter(
        IncidentToken.incident_id == incident_id,
        IncidentToken.token_hash == token_hash,
        IncidentToken.revoked_at.is_(None),
    ).first()
    if not db_token:
        return RedirectResponse("/login?error=qr_invalid", status_code=302)

    user_id = data["user_id"]
    user = db.get(User, user_id)
    if not user or not user.active:
        return RedirectResponse("/login", status_code=302)

    # Org-Konsistenz prüfen (Phase 1): User muss zur Org des Einsatzes gehören
    from app.core.permissions import can_access_incident
    if not can_access_incident(user, incident):
        return RedirectResponse("/login?error=qr_invalid", status_code=302)

    user.last_login_at = datetime.now(UTC)
    write_audit(db, "auth.qr_login", user_id=user_id, incident_id=incident_id,
                ip=request.client.host if request.client else None)
    db.commit()

    session_token = sign_session(user.id)
    redirect = RedirectResponse(f"/einsatz/{incident_id}", status_code=302)
    _set_session_cookie(redirect, session_token)
    return redirect
