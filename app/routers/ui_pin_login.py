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
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.multi_account import ACCOUNTS_COOKIE, add_account, load_accounts, set_accounts_cookie
from app.core.rate_limit import limiter as _limiter
from app.core.security import generate_numeric_pin, sign_session
from app.core.telefon import telefon_identitaet_at, telefon_kompakt
from app.core.templating import templates
from app.db import get_db
from app.models.login_pin import LOGIN_PIN_TTL_MINUTES, LoginPin
from app.models.user import User

logger = logging.getLogger("einsatzleiter.pin_login")
router = APIRouter()


def _hash_pin(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _find_user_by_phone(db: Session, phone_norm: str) -> User | None:
    """Lineare Suche über alle aktiven User mit Telefonnummer — kein Index auf
    normalisierte Nummern nötig, Org-Nutzerzahlen sind klein (Feuerwehren).

    Verglichen wird über telefon_identitaet_at, nicht über die kompakte Schreibweise:
    "0664…" im Profil und "+43 664…" im Formular (dessen Platzhalter) waren bisher
    verschiedene Nummern, die PIN-SMS wurde dann still nicht verschickt
    (Vorfall 2026-10-07)."""
    schluessel = telefon_identitaet_at(phone_norm)
    if not schluessel:
        return None
    users = db.query(User).filter(User.active == True, User.phone.isnot(None)).all()  # noqa: E712
    treffer = [u for u in users if telefon_identitaet_at(u.phone) == schluessel]
    if len(treffer) > 1:
        logger.warning(
            "PIN-Login: Nummer %s ist mehreren aktiven Benutzern zugeordnet (user_ids=%s), nehme den ersten",
            _maskiert(schluessel), [u.id for u in treffer],
        )
    return treffer[0] if treffer else None


def _maskiert(nummer: str) -> str:
    return nummer[:-4] + "****" if len(nummer) >= 5 else "****"


def _set_session_cookie(response: Response, token: str, max_age: int | None = None) -> None:
    response.set_cookie(
        "session", token, httponly=True, secure=settings.COOKIE_SECURE,
        samesite="lax", max_age=max_age if max_age is not None else settings.SESSION_MAX_AGE_SECONDS,
    )


@router.get("/pin-login", response_class=HTMLResponse)
async def pin_login_form(request: Request, add: int = 0):
    if getattr(request.state, "user", None) and not add:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "auth/pin_login.html", {"error": None, "add": bool(add)},
    )


@router.post("/pin-login", response_class=HTMLResponse)
@(_limiter.limit("5/minute") if _limiter else lambda f: f)
async def pin_login_submit(
    request: Request,
    phone: str = Form(...),
    add: int = Form(0),
    db: Session = Depends(get_db),
):
    phone_norm = telefon_kompakt(phone)
    # Immer zur Code-Eingabe weiterleiten — unabhängig davon, ob die Nummer
    # registriert ist (kein Enumerations-Leak, Muster ui_password_reset.py).
    add_query = "&add=1" if add else ""
    # quote(): ein rohes "+" im Query-String wird beim Lesen zum Leerzeichen, die
    # Nummer passte im zweiten Schritt dann nicht mehr zur angeforderten PIN.
    redirect = RedirectResponse(
        f"/pin-login/code?phone={quote(phone_norm)}{add_query}", status_code=303,
    )
    if not phone_norm:
        return redirect

    match = _find_user_by_phone(db, phone_norm)
    if not match or not match.org_id or not match.phone:
        # Neutrale Antwort an den Client (Enumerations-Schutz), aber im Log sichtbar.
        logger.info(
            "PIN-Login: keine PIN versendet, kein aktiver Benutzer mit Organisation zur Nummer %s",
            _maskiert(telefon_identitaet_at(phone_norm)),
        )
        return redirect

    from app.services.sms_service import sms_available
    if not sms_available(match.org_id, db):
        logger.warning(
            "PIN-Login: keine PIN versendet, SMS-Versand nicht verfuegbar (user_id=%s, org_id=%s)",
            match.id, match.org_id,
        )
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
        result = await send_sms(match.org_id, match.phone, text)
    except Exception:
        logger.exception("PIN-SMS-Versand fehlgeschlagen (user_id=%s)", match.id)
    else:
        if result.success:
            logger.info("PIN-SMS versendet (user_id=%s, provider=%s)", match.id, result.provider)
        else:
            logger.warning(
                "PIN-SMS-Versand fehlgeschlagen (user_id=%s, letzter provider=%s)", match.id, result.provider,
            )

    return redirect


@router.get("/pin-login/code", response_class=HTMLResponse)
async def pin_login_code_form(request: Request, phone: str = "", add: int = 0):
    if getattr(request.state, "user", None) and not add:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request,
        "auth/pin_login_code.html",
        {"error": None, "phone": phone, "add": bool(add)},
    )


@router.post("/pin-login/code", response_class=HTMLResponse)
@(_limiter.limit("5/minute") if _limiter else lambda f: f)
async def pin_login_code_submit(
    request: Request, phone: str = Form(...), pin: str = Form(...),
    remember: str = Form(""), add: int = Form(0), db: Session = Depends(get_db),
):
    generic_error = "PIN ungültig oder abgelaufen."

    def _err() -> HTMLResponse:
        return templates.TemplateResponse(
            request, "auth/pin_login_code.html",
            {"error": generic_error, "phone": phone, "add": bool(add)}, status_code=401,
        )

    phone_norm = telefon_kompakt(phone)
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
    set_accounts_cookie(
        redirect,
        add_account(load_accounts(request.cookies.get(ACCOUNTS_COOKIE)), match.id, is_remember),
    )
    return redirect
