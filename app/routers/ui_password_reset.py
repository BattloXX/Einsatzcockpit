"""Self-Service-Passwort-Reset per E-Mail (Phase 5.3).

Flow:
1. GET  /passwort-vergessen        — Formular (E-Mail-Eingabe)
2. POST /passwort-vergessen        — neutrale Bestätigung, ggf. Token + Mail
3. GET  /passwort-zuruecksetzen?token=…  — Formular (neues Passwort)
4. POST /passwort-zuruecksetzen    — Passwort updaten, Token markieren

Sicherheit:
- Antwort bei unbekannter Mail ist identisch zu bekannter Mail (kein Enumerations-Leak).
- Token wird als sha256-Hex in DB gespeichert; rohes Token nur im Link.
- Tokens sind einmalig (used_at) und befristet (PASSWORD_RESET_TTL_MIN).
- Sobald ein neuer Token erzeugt wird, werden alle alten offenen Tokens des Users invalidiert.
- Rate-Limit: 5 Requests/Minute pro IP auf POST-Endpunkte.
"""
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.rate_limit import limiter as _limiter
from app.core.security import hash_password
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.services.benutzer_mail_service import send_passwort_geaendert, send_sso_hinweis
from app.services.mail_service import send_password_reset

logger = logging.getLogger("einsatzleiter.password_reset")
router = APIRouter()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/passwort-vergessen", response_class=HTMLResponse)
async def forgot_form(request: Request):
    return templates.TemplateResponse(request, "auth/forgot.html", {"sent": False, "error": None})


@router.post("/passwort-vergessen", response_class=HTMLResponse)
@(_limiter.limit("5/minute") if _limiter else lambda f: f)
async def forgot_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email_clean = (email or "").strip().lower()
    # Immer mit neutraler Antwort reagieren — kein Hinweis, ob die Mail existiert
    neutral_response = templates.TemplateResponse(
        request, "auth/forgot.html",
        {"sent": True, "error": None},
    )

    if not email_clean or "@" not in email_clean:
        return neutral_response

    user = db.query(User).filter(User.email == email_clean, User.active == True).first()  # noqa: E712
    if not user:
        # Trotzdem warten, um Timing-Unterschiede zu reduzieren – minimaler Schutz
        return neutral_response

    if user.auth_provider != "local":
        org = db.get(FireDept, user.org_id)
        write_audit(
            db, "auth.password_reset.sso_hinweis",
            user_id=user.id, ip=request.client.host if request.client else None,
        )
        db.commit()
        try:
            await send_sso_hinweis(
                to=user.email,  # type: ignore[arg-type]
                user_display_name=user.full_name or user.display_name or user.username,
                org_slug=org.slug if org else "",
                db=db, org_id=user.org_id,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Versand des SSO-Hinweises fehlgeschlagen für user_id=%s: %s", user.id, exc)
        return neutral_response

    # Alte offene Tokens entwerten
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": _now()})

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires = _now() + timedelta(minutes=settings.PASSWORD_RESET_TTL_MIN)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires,
        requesting_ip=request.client.host if request.client else None,
    ))
    write_audit(
        db, "auth.password_reset.requested",
        user_id=user.id, ip=request.client.host if request.client else None,
    )
    db.commit()

    base = settings.effective_public_base_url.rstrip("/")
    reset_url = f"{base}/passwort-zuruecksetzen?token={raw_token}"
    try:
        await send_password_reset(
            to=user.email,  # type: ignore[arg-type]
            reset_url=reset_url,
            user_display_name=user.full_name or user.display_name or user.username,
            db=db, org_id=user.org_id,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Versand des Passwort-Reset-Mails fehlgeschlagen für user_id=%s: %s",
                     user.id, exc)

    return neutral_response


@router.get("/passwort-zuruecksetzen", response_class=HTMLResponse)
async def reset_form(request: Request, token: str = "", db: Session = Depends(get_db)):
    error: str | None = None
    ist_einladung = False
    if not token:
        error = "Token fehlt."
    else:
        prt = db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == _hash_token(token)
        ).first()
        ist_einladung = bool(prt and prt.zweck == "einladung")
        if not prt or not prt.is_valid:
            error = (
                "Diese Einladung ist abgelaufen. Bitte wende dich an deinen Administrator."
                if ist_einladung else "Dieser Reset-Link ist nicht mehr gültig. Fordere bitte einen neuen an."
            )
    return templates.TemplateResponse(
        request, "auth/reset.html",
        {"token": token, "error": error, "done": False, "ist_einladung": ist_einladung},
    )


@router.post("/passwort-zuruecksetzen", response_class=HTMLResponse)
@(_limiter.limit("5/minute") if _limiter else lambda f: f)
async def reset_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_repeat: str = Form(...),
    db: Session = Depends(get_db),
):
    def _err(msg: str) -> HTMLResponse:
        prt_fehler = db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == _hash_token(token)
        ).first()
        return templates.TemplateResponse(
            request, "auth/reset.html",
            {"token": token, "error": msg, "done": False,
             "ist_einladung": bool(prt_fehler and prt_fehler.zweck == "einladung")},
        )

    if not token:
        return _err("Token fehlt.")
    if len(password) < 10:
        return _err("Bitte mindestens 10 Zeichen verwenden.")
    if password != password_repeat:
        return _err("Die beiden Passwörter stimmen nicht überein.")

    prt = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == _hash_token(token)
    ).first()
    if not prt or not prt.is_valid:
        return _err(
            "Diese Einladung ist abgelaufen. Bitte wende dich an deinen Administrator."
            if prt and prt.zweck == "einladung"
            else "Dieser Reset-Link ist nicht mehr gültig. Fordere bitte einen neuen an."
        )

    user = db.get(User, prt.user_id)
    if not user or not user.active:
        return _err("Benutzer nicht verfügbar.")

    user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    prt.used_at = _now()
    write_audit(
        db, "auth.password_reset.completed",
        user_id=user.id, ip=request.client.host if request.client else None,
    )
    db.commit()

    try:
        await send_passwort_geaendert(
            user, db, ausloeser=prt.zweck, ip=request.client.host if request.client else None,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Versand der Passwort-Bestätigung fehlgeschlagen für user_id=%s: %s", user.id, exc)

    return templates.TemplateResponse(
        request, "auth/reset.html",
        {"token": "", "error": None, "done": True, "ist_einladung": prt.zweck == "einladung"},
    )
