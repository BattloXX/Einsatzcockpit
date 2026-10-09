from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.multi_account import ACCOUNTS_COOKIE, add_account, load_accounts, set_accounts_cookie
from app.core.rate_limit import limiter as _limiter
from app.core.security import (
    hash_api_key,
    sign_pin_access_token,
    sign_session,
    unsign_pin_access_token,
    unsign_qr_token,
    verify_pin,
)
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, IncidentToken
from app.models.user import DeviceToken, User
from app.services.auth_service import authenticate_user

router = APIRouter()


def _get_dummy_password_hash() -> str:
    """Kompatibilitaets-Export fuer bestehende Security-Regressionstests."""
    from app.services.auth_service import _dummy_hash

    return _dummy_hash()


def _set_session_cookie(response: Response, token: str, max_age: int | None = None) -> None:
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=max_age if max_age is not None else settings.SESSION_MAX_AGE_SECONDS,
    )


def _safe_next(next_url: str | None) -> str:
    """Nur interne Ziele als Rücksprung zulassen (Open-Redirect-Schutz)."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    # Browser behandeln "\\" wie "/" ("/\\evil.com" == "//evil.com"); Steuerzeichen werden teils entfernt.
    if "\\" in next_url or any(ord(char) < 32 or ord(char) == 127 for char in next_url):
        return "/"
    return next_url


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "", fcm_token: str = "", add: int = 0):
    if getattr(request.state, "user", None) and not add:
        return RedirectResponse(_safe_next(next), status_code=302)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": next, "fcm_token": fcm_token, "add": bool(add)}
    )


@router.post("/login")
@(_limiter.limit(settings.LOGIN_RATELIMIT) if _limiter else lambda f: f)
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    remember: str = Form(""),
    fcm_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Login mit Account-Lockout (Phase 7).

    - Bei Fehlversuch wird `failed_login_count` erhöht.
    - Ab `LOGIN_MAX_FAILED` wird der Account `LOGIN_LOCKOUT_MINUTES` lang gesperrt.
    - Während Lockout wird IMMER der gleiche generische Fehler gezeigt (kein Enumerations-Leak).
    """
    user, error = authenticate_user(db, username, password, request.client.host if request.client else None)
    if error == "enforce_sso":
        return RedirectResponse("/login?error=enforce_sso", status_code=302)
    if not user:
        return templates.TemplateResponse(
            request, "login.html", {"error": error, "next": next, "fcm_token": fcm_token},
            status_code=401,
        )
    if fcm_token:
        from app.services.push_service import upsert_fcm_token
        upsert_fcm_token(db, user_id=user.id, token=fcm_token)
    db.commit()

    # "Login merken": längeres, gleitendes Session-Fenster (7 Tage Inaktivität,
    # bis 30 Tage absolut). Checkbox sendet nur bei Aktivierung einen Wert.
    is_remember = bool(remember)
    token = sign_session(user.id, remember=is_remember)
    redirect = RedirectResponse(_safe_next(next), status_code=302)
    _set_session_cookie(
        redirect, token,
        max_age=settings.SESSION_REMEMBER_MAX_AGE_SECONDS if is_remember else None,
    )
    set_accounts_cookie(
        redirect,
        add_account(load_accounts(request.cookies.get(ACCOUNTS_COOKIE)), user.id, is_remember),
    )
    return redirect


@router.get("/geraet-login")
async def device_login(
    request: Request,
    token: str,
    fcm_token: str | None = None,
    db: Session = Depends(get_db),
):
    """Token-basierter Auto-Login für registrierte Geräte."""
    token_hash = hash_api_key(token)
    dt = db.query(DeviceToken).filter(
        DeviceToken.token_hash == token_hash,
        DeviceToken.revoked_at.is_(None),
    ).first()
    if not dt:
        return RedirectResponse("/login?error=device_invalid", status_code=302)
    user = db.get(User, dt.user_id)
    if not user or not user.active:
        return RedirectResponse("/login?error=device_invalid", status_code=302)

    now = datetime.now(UTC)
    dt.last_used_at = now
    user.last_login_at = now
    write_audit(db, "auth.device_login", user_id=user.id,
                ip=request.client.host if request.client else None,
                payload={"device_token_id": dt.id, "label": dt.label})
    if fcm_token:
        from app.services.push_service import upsert_fcm_token
        upsert_fcm_token(db, user_id=user.id, token=fcm_token, device_token_id=dt.id)
    db.commit()

    session_token = sign_session(user.id, device=True, device_token_id=dt.id)
    redirect = RedirectResponse("/", status_code=302)
    redirect.set_cookie(
        "session",
        session_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=10 * 365 * 24 * 3600,  # ~10 Jahre; kein Ablauf für Geräte-Sessions
    )
    return redirect


@router.get("/geraet-login-pin", response_class=HTMLResponse)
async def device_login_pin_form(request: Request):
    """PIN-Eingabeformular fürs Geräte-Pairing — Alternative zum QR-Code-Scan
    (z.B. wenn kein Kamerazugriff möglich ist). PIN wird im Admin-Bereich neben
    dem QR-Code angezeigt (Admin → Geräte-Login)."""
    return templates.TemplateResponse(request, "auth/geraet_login_pin.html", {"error": None})


@router.post("/geraet-login-pin", response_class=HTMLResponse)
@(_limiter.limit("5/15minutes") if _limiter else lambda f: f)
async def device_login_pin_submit(request: Request, pin: str = Form(...), db: Session = Depends(get_db)):
    from app.services.device_login_service import redeem_pairing_pin

    result = redeem_pairing_pin(db, pin)
    if result is None:
        write_audit(db, "auth.device_login_pin.failed",
                    ip=request.client.host if request.client else None)
        db.commit()
        return templates.TemplateResponse(
            request, "auth/geraet_login_pin.html",
            {"error": "PIN ungültig oder abgelaufen."}, status_code=401,
        )
    dt, raw_token, raw_gateway_token = result
    user = db.get(User, dt.user_id)
    if not user or not user.active:
        return templates.TemplateResponse(
            request, "auth/geraet_login_pin.html",
            {"error": "Gerät nicht verfügbar."}, status_code=401,
        )

    now = datetime.now(UTC)
    dt.last_used_at = now
    user.last_login_at = now
    write_audit(db, "auth.device_login_pin", user_id=user.id,
                ip=request.client.host if request.client else None,
                payload={"device_token_id": dt.id, "label": dt.label,
                         "gateway_paired": raw_gateway_token is not None})
    db.commit()

    session_token = sign_session(user.id, device=True, device_token_id=dt.id)
    response = templates.TemplateResponse(request, "auth/geraet_login_pin_done.html", {
        "raw_token": raw_token,
        "raw_gateway_token": raw_gateway_token,
    })
    response.set_cookie(
        "session", session_token,
        httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
        max_age=10 * 365 * 24 * 3600,  # ~10 Jahre; kein Ablauf für Geräte-Sessions
    )
    return response


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    from app.routers.ui_account_switch import logout_oder_wechseln

    return logout_oder_wechseln(request, db)


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

    session_token = sign_session(user.id, qr=True, incident_id=incident_id)
    redirect = RedirectResponse(f"/qr-name?incident_id={incident_id}", status_code=302)
    _set_session_cookie(redirect, session_token)
    return redirect


_PIN_COOKIE = "board_pin"
_PIN_COOKIE_MAX_AGE = 86400


def _set_pin_cookie_auth(response: Response, incident_id: int) -> None:
    token = sign_pin_access_token(incident_id)
    response.set_cookie(
        _PIN_COOKIE, token,
        httponly=True, secure=settings.COOKIE_SECURE,
        samesite="lax", max_age=_PIN_COOKIE_MAX_AGE,
    )


def _has_valid_pin_cookie(request: Request, incident_id: int) -> bool:
    token = request.cookies.get(_PIN_COOKIE)
    if not token:
        return False
    return unsign_pin_access_token(token) == incident_id


@router.get("/qr-pin", response_class=HTMLResponse)
async def qr_pin_page(request: Request, incident_id: int, db: Session = Depends(get_db)):
    """PIN-Abfrage im QR-Flow – vor der Namenseingabe."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident or not incident.access_pin_hash:
        return RedirectResponse(f"/qr-name?incident_id={incident_id}", status_code=302)
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "auth/qr_pin.html", {
        "incident": incident,
        "incident_id": incident_id,
        "error": error,
    })


@router.post("/qr-pin")
@(_limiter.limit("5/15minutes") if _limiter else lambda f: f)
async def qr_pin_submit(
    request: Request,
    incident_id: int = Form(...),
    pin: str = Form(""),
    db: Session = Depends(get_db),
):
    """Prüft den PIN im QR-Flow und setzt das Zugangscookie."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident or not incident.access_pin_hash:
        return RedirectResponse(f"/qr-name?incident_id={incident_id}", status_code=302)
    if not verify_pin(pin.strip(), incident.access_pin_hash):
        return RedirectResponse(f"/qr-pin?incident_id={incident_id}&error=wrong_pin", status_code=302)
    redirect = RedirectResponse(f"/qr-name?incident_id={incident_id}", status_code=302)
    _set_pin_cookie_auth(redirect, incident_id)
    return redirect


@router.get("/qr-name", response_class=HTMLResponse)
async def qr_name_page(request: Request, incident_id: int | None = None, db: Session = Depends(get_db)):
    """Intermediate name-entry step after QR login."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    qr_incident_id = incident_id or getattr(request.state, "qr_incident_id", None)
    if not qr_incident_id:
        return RedirectResponse("/", status_code=302)
    incident = db.get(Incident, qr_incident_id)
    # PIN-Prüfung: wenn Einsatz einen PIN hat und kein gültiges Cookie vorhanden
    if incident and incident.access_pin_hash and not _has_valid_pin_cookie(request, qr_incident_id):
        return RedirectResponse(f"/qr-pin?incident_id={qr_incident_id}", status_code=302)
    return templates.TemplateResponse(request, "auth/qr_name.html", {
        "incident": incident,
        "incident_id": qr_incident_id,
    })


@router.post("/qr-name")
async def qr_name_submit(
    request: Request,
    response: Response,
    incident_id: int = Form(...),
    display_name: str = Form(...),
):
    """Save the entered name into the QR session token and proceed to the board."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    qr_incident_id = getattr(request.state, "qr_incident_id", None)
    if not qr_incident_id or qr_incident_id != incident_id:
        return RedirectResponse("/login?error=qr_invalid", status_code=302)

    name = display_name.strip()[:120] or None
    session_token = sign_session(user.id, qr=True, incident_id=incident_id, display_name=name)
    redirect = RedirectResponse(f"/einsatz/{incident_id}", status_code=302)
    _set_session_cookie(redirect, session_token)
    return redirect
