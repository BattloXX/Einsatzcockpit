"""Benutzer-Profil: Anzeige, Änderung und Avatar-Upload."""
import io
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.async_bridge import run_async
from app.core.crypto import decrypt_secret
from app.core.security import hash_password, verify_password
from app.core.telefon import telefon_maske
from app.core.templating import templates
from app.db import get_db
from app.models.admin_security import AdminTotp, UserMfaSms, utcnow
from app.models.mcp import MCPOAuthToken
from app.models.user import User
from app.routers.ui_mfa import _page, _user
from app.services import mfa_service, mfa_sms_service
from app.services.benutzer_mail_service import send_passwort_geaendert

logger = logging.getLogger("einsatzleiter.profile")
router = APIRouter()

_AVATAR_DIR = Path(settings.MEDIA_STORAGE_DIR).parent / "avatars"
_AVATAR_MAX_BYTES = 3 * 1024 * 1024  # 3 MB


def _avatar_url(user: User) -> str | None:
    if not user.avatar_path:
        return None
    return f"/profil/avatar/{user.avatar_path}"


@router.get("/profil", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db_user = db.get(User, user.id)
    connections = (
        db.query(MCPOAuthToken.client_id)
        .filter(MCPOAuthToken.user_id == user.id, MCPOAuthToken.revoked_at.is_(None))
        .distinct()
        .all()
    )
    sms_factor = db.get(UserMfaSms, user.id)
    return templates.TemplateResponse(request, "profile/index.html", {
        "profile_user": db_user,
        "mfa_personal": not (request.state.is_device or user.is_device
                             or request.state.qr_incident_id is not None or request.state.qr_lage_id is not None
                             or user.username.startswith("alarmlink-gast@org")),
        "mfa_factor": db.get(AdminTotp, user.id),
        "mfa_sms": sms_factor,
        "mfa_sms_allowed": mfa_sms_service.sms_allowed(db, db_user) if db_user else False,
        "mfa_sms_phone": telefon_maske(decrypt_secret(sms_factor.phone_enc)) if sms_factor else "",
        "mfa_status": mfa_service.status(db, db_user) if db_user else {},
        "mfa_required": mfa_service.required_for(db, db_user) if db_user else False,
        "mfa_now": utcnow(),
        "avatar_url": _avatar_url(db_user),  # type: ignore[arg-type]
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
        "mcp_connections": [row[0] for row in connections],
    })


@router.post("/profil")
def profile_update(
    request: Request,
    display_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db_user = db.get(User, user.id)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    name = display_name.strip()[:150]
    if name:
        db_user.display_name = name
    db_user.phone = phone.strip()[:64] or None
    new_email = email.strip()[:255] or None
    if new_email != db_user.email:
        # Check uniqueness
        conflict = db.query(User).filter(User.email == new_email, User.id != db_user.id).first()
        if conflict:
            return RedirectResponse("/profil?error=email_taken", status_code=302)
        db_user.email = new_email
    db.commit()
    return RedirectResponse("/profil?saved=1", status_code=302)


@router.post("/profil/mcp/{client_id}/trennen")
def profile_disconnect_mcp(request: Request, client_id: str, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    for token in db.query(MCPOAuthToken).filter(
        MCPOAuthToken.user_id == user.id,
        MCPOAuthToken.client_id == client_id,
        MCPOAuthToken.revoked_at.is_(None),
    ):
        token.revoked_at = now
    db.commit()
    return RedirectResponse("/profil?saved=1", status_code=302)


@router.post("/profil/passwort")
async def profile_change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db_user = db.get(User, user.id)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    if not verify_password(current_password, db_user.password_hash):  # type: ignore[arg-type]
        return RedirectResponse("/profil?error=wrong_password", status_code=302)
    if len(new_password) < 8:
        return RedirectResponse("/profil?error=password_too_short", status_code=302)
    db_user.password_hash = hash_password(new_password)
    db.commit()
    try:
        await send_passwort_geaendert(
            db_user, db, ausloeser="profil", ip=request.client.host if request.client else None,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Versand der Passwort-Bestätigung fehlgeschlagen für user_id=%s: %s", db_user.id, exc)
    return RedirectResponse("/profil?saved=1", status_code=302)


@router.post("/profil/avatar")
def profile_upload_avatar(
    request: Request,
    avatar: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db_user = db.get(User, user.id)
    if not db_user:
        return RedirectResponse("/login", status_code=302)

    data = run_async(avatar.read, _AVATAR_MAX_BYTES + 1)
    if len(data) > _AVATAR_MAX_BYTES:
        return RedirectResponse("/profil?error=avatar_too_large", status_code=302)

    mime = avatar.content_type or ""
    if not mime.startswith("image/"):
        return RedirectResponse("/profil?error=avatar_not_image", status_code=302)

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")  # type: ignore[assignment]
        img.thumbnail((256, 256))
        _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{user.id}_{uuid.uuid4().hex[:8]}.jpg"
        path = _AVATAR_DIR / filename
        img.save(path, "JPEG", quality=85)

        # Remove old avatar file
        if db_user.avatar_path:
            old = _AVATAR_DIR / db_user.avatar_path
            if old.exists():
                old.unlink(missing_ok=True)

        db_user.avatar_path = filename
        db.commit()
    except Exception:
        logger.exception("Avatar-Upload fehlgeschlagen")
        return RedirectResponse("/profil?error=avatar_upload_failed", status_code=302)

    return RedirectResponse("/profil?saved=1", status_code=302)


@router.get("/profil/avatar/{filename}")
async def serve_avatar(filename: str, request: Request):
    """Liefert das Profilbild aus – kein Auth erforderlich (public Ressource).

    Fehlt die Datei (z.B. nach Redeploy ohne persistentes Avatar-Volume, siehe
    Vorfall 2026-07-01: DB verwies noch auf ein gelöschtes File -> 404 auf
    jeder Seite), liefern wir statt eines nackten 404 den neutralen
    Platzhalter aus -- kein kaputtes Bild-Icon, kein wiederholter 404-Request.
    """
    safe_name = Path(filename).name
    path = _AVATAR_DIR / safe_name
    if not path.exists() or not path.is_file():
        return RedirectResponse("/static/img/avatar-default.svg", status_code=302)
    import mimetypes
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


@router.post("/profil/mfa/recovery-codes")
def mfa_recovery_codes(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    # Recovery-Codes duerfen keine weiteren Recovery-Codes erzeugen.
    clean_code = code.strip()
    if len(clean_code) != 6 or not clean_code.isascii() or not clean_code.isdigit():
        raise HTTPException(403, "Gueltiger TOTP-Code erforderlich")
    success = mfa_service.verify(db, user.id, clean_code)
    db.commit()
    if not success:
        raise HTTPException(403, "Gueltiger TOTP-Code erforderlich")
    codes = mfa_service.create_recovery_codes(db, user.id)
    db.commit()
    return _page(request, "recovery", codes=codes, next="/profil")


@router.post("/profil/mfa/entfernen")
def mfa_remove(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    user = _user(request, db)
    if mfa_service.required_for(db, user):
        raise HTTPException(400, "Die Zwei-Faktor-Authentifizierung ist vorgeschrieben und kann nicht entfernt werden.")
    success = mfa_service.verify(db, user.id, code)
    db.commit()
    if not success:
        raise HTTPException(403, "Gueltiger TOTP- oder Recovery-Code erforderlich")
    mfa_service.reset(db, user, user.id)
    db.commit()
    mfa_service.notify_reset(db, user)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session", path="/")
    return response
