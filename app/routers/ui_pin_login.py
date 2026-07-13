"""SMS-PIN-Login: passwortlose Anmeldung per Einmal-PIN an die hinterlegte
Telefonnummer (User.phone). Alternative zu Benutzername/Passwort und
QR-Code-Scan, v.a. für die Android-App.

Flow:
1. GET/POST /pin-login       — Telefonnummer eingeben, PIN per SMS versenden
2. GET/POST /pin-login/code  — PIN eingeben, Session anlegen

Sicherheit:
- Neutrale Antwort bei unbekannter/nicht registrierter Nummer (kein Enumerations-Leak,
  Muster ui_password_reset.py) — es wird immer zur Code-Eingabe weitergeleitet.
- PIN als sha256-Hex gespeichert, 10 Minuten gültig, max. 5 Fehlversuche, Einmal-Gebrauch
  (siehe app/models/login_pin.py).
- Rate-Limit: 5 Anfragen/Minute pro IP auf beide POST-Endpunkte.
- SMS-Versand nur wenn ein SMS-Gateway für die Org des Users verbunden ist — sonst
  wird intern übersprungen, ohne das dem Client zu verraten (Enumerations-Schutz).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.rate_limit import limiter as _limiter
from app.core.security import generate_numeric_pin, sign_session
from app.core.templating import templates
from app.db import get_db
from app.models.login_pin import LOGIN_PIN_TTL_MINUTES, LoginPin
from app.models.user import User

logger = logging.getLogger("einsatzleiter.pin_login")
router = APIRouter()

_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)]")


def _hash_pin(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_phone(phone: str | None) -> str:
    return _PHONE_STRIP_RE.sub("", phone or "").strip()


def _find_user_by_phone(db: Session, phone_norm: str) -> User | None:
    """Lineare Suche über alle aktiven User mit Telefonnummer — kein Index auf
    normalisierte Nummern nötig, Org-Nutzerzahlen sind klein (Feuerwehren)."""
    if not phone_norm:
        return None
    users = db.query(User).filter(User.active == True, User.phone.isnot(None)).all()  # noqa: E712
    return next((u for u in users if _normalize_phone(u.phone) == phone_norm), None)


def _set_session_cookie(response: Response, token: str, max_age: int | None = None) -> None:
    response.set_cookie(
        "session", token, httponly=True, secure=settings.COOKIE_SECURE,
        samesite="lax", max_age=max_age if max_age is not None else settings.SESSION_MAX_AGE_SECONDS,
    )


@router.get("/pin-login", response_class=HTMLResponse)
async def pin_login_form(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "auth/pin_login.html", {"error": None})


@router.post("/pin-login", response_class=HTMLResponse)
@(_limiter.limit("5/minute") if _limiter else lambda f: f)
async def pin_login_submit(request: Request, phone: str = Form(...), db: Session = Depends(get_db)):
    phone_norm = _normalize_phone(phone)
    # Immer zur Code-Eingabe weiterleiten — unabhängig davon, ob die Nummer
    # registriert ist (kein Enumerations-Leak, Muster ui_password_reset.py).
    redirect = RedirectResponse(f"/pin-login/code?phone={phone_norm}", status_code=303)
    if not phone_norm:
        return redirect

    match = _find_user_by_phone(db, phone_norm)
    if not match or not match.org_id or not match.phone:
        return redirect

    from app.routers.ws import is_sms_gateway_connected
    if not is_sms_gateway_connected(match.org_id):
        logger.debug("PIN-Login: kein SMS-Gateway verbunden (org_id=%s)", match.org_id)
        return redirect

    # Alte offene PINs dieses Users entwerten (nur die zuletzt erzeugte gilt).
    db.query(LoginPin).filter(
        LoginPin.user_id == match.id, LoginPin.used_at.is_(None),
    ).update({"used_at": datetime.now(UTC)})

    pin = generate_numeric_pin()
    db.add(LoginPin(
        user_id=match.id,
        pin_hash=_hash_pin(pin),
        expires_at=datetime.now(UTC) + timedelta(minutes=LOGIN_PIN_TTL_MINUTES),
        requesting_ip=request.client.host if request.client else None,
    ))
    write_audit(db, "auth.pin_login.requested", user_id=match.id,
                ip=request.client.host if request.client else None)
    db.commit()

    from app.services.sms_service import send_sms
    text = f"Ihr {settings.APP_NAME}-Anmelde-PIN: {pin} (gültig {LOGIN_PIN_TTL_MINUTES} Minuten)"
    try:
        await send_sms(match.org_id, match.phone, text)
    except Exception:
        logger.exception("PIN-SMS-Versand fehlgeschlagen (user_id=%s)", match.id)

    return redirect


@router.get("/pin-login/code", response_class=HTMLResponse)
async def pin_login_code_form(request: Request, phone: str = ""):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "auth/pin_login_code.html", {"error": None, "phone": phone},
    )


@router.post("/pin-login/code", response_class=HTMLResponse)
@(_limiter.limit("5/minute") if _limiter else lambda f: f)
async def pin_login_code_submit(
    request: Request, phone: str = Form(...), pin: str = Form(...),
    remember: str = Form(""), db: Session = Depends(get_db),
):
    generic_error = "PIN ungültig oder abgelaufen."

    def _err() -> HTMLResponse:
        return templates.TemplateResponse(
            request, "auth/pin_login_code.html",
            {"error": generic_error, "phone": phone}, status_code=401,
        )

    phone_norm = _normalize_phone(phone)
    pin_clean = pin.strip()
    if not phone_norm or not pin_clean:
        return _err()

    match = _find_user_by_phone(db, phone_norm)
    if not match:
        return _err()

    login_pin = (
        db.query(LoginPin)
        .filter(LoginPin.user_id == match.id, LoginPin.used_at.is_(None))
        .order_by(LoginPin.created_at.desc())
        .first()
    )
    if not login_pin or not login_pin.is_valid:
        return _err()

    if login_pin.pin_hash != _hash_pin(pin_clean):
        login_pin.attempt_count += 1
        write_audit(db, "auth.pin_login.failed", user_id=match.id,
                    ip=request.client.host if request.client else None)
        db.commit()
        return _err()

    login_pin.used_at = datetime.now(UTC)
    match.last_login_at = datetime.now(UTC)
    write_audit(db, "auth.pin_login", user_id=match.id,
                ip=request.client.host if request.client else None)
    db.commit()

    is_remember = bool(remember)
    token = sign_session(match.id, remember=is_remember)
    redirect = RedirectResponse("/", status_code=302)
    _set_session_cookie(
        redirect, token,
        max_age=settings.SESSION_REMEMBER_MAX_AGE_SECONDS if is_remember else None,
    )
    return redirect
