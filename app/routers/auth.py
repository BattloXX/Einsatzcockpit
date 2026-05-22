from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.security import verify_password, sign_session, unsign_qr_token
from app.core.audit import write_audit
from app.models.user import User
from app.models.incident import Incident, IncidentToken
from app.core.security import hash_api_key

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
    user = db.query(User).filter(User.username == username, User.active == True).first()  # noqa: E712
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Benutzername oder Passwort falsch"},
            status_code=401,
        )
    user.last_login_at = datetime.now(timezone.utc)
    write_audit(db, "auth.login", user_id=user.id, ip=request.client.host if request.client else None)
    db.commit()

    token = sign_session(user.id)
    redirect = RedirectResponse("/", status_code=302)
    redirect.set_cookie("session", token, httponly=True, samesite="lax", max_age=86400)
    return redirect


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if user:
        write_audit(db, "auth.logout", user_id=user.id)
        db.commit()
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
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

    # Check token in DB (not revoked)
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

    write_audit(db, "auth.qr_login", user_id=user_id, incident_id=incident_id,
                ip=request.client.host if request.client else None)
    db.commit()

    session_token = sign_session(user.id)
    redirect = RedirectResponse(f"/einsatz/{incident_id}", status_code=302)
    redirect.set_cookie("session", session_token, httponly=True, samesite="lax", max_age=86400)
    return redirect
