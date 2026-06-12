"""Benutzer-Profil: Anzeige, Änderung und Avatar-Upload."""
import io
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password, verify_password
from app.core.templating import templates
from app.db import get_db
from app.models.user import User

logger = logging.getLogger("einsatzleiter.profile")
router = APIRouter()

_AVATAR_DIR = Path(settings.MEDIA_STORAGE_DIR).parent / "avatars"
_AVATAR_MAX_BYTES = 3 * 1024 * 1024  # 3 MB


def _avatar_url(user: User) -> str | None:
    if not user.avatar_path:
        return None
    return f"/profil/avatar/{user.avatar_path}"


@router.get("/profil", response_class=HTMLResponse)
async def profile_page(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db_user = db.get(User, user.id)
    return templates.TemplateResponse(request, "profile/index.html", {
        "profile_user": db_user,
        "avatar_url": _avatar_url(db_user),
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/profil")
async def profile_update(
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
    if not verify_password(current_password, db_user.password_hash):
        return RedirectResponse("/profil?error=wrong_password", status_code=302)
    if len(new_password) < 8:
        return RedirectResponse("/profil?error=password_too_short", status_code=302)
    db_user.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse("/profil?saved=1", status_code=302)


@router.post("/profil/avatar")
async def profile_upload_avatar(
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

    data = await avatar.read(_AVATAR_MAX_BYTES + 1)
    if len(data) > _AVATAR_MAX_BYTES:
        return RedirectResponse("/profil?error=avatar_too_large", status_code=302)

    mime = avatar.content_type or ""
    if not mime.startswith("image/"):
        return RedirectResponse("/profil?error=avatar_not_image", status_code=302)

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
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
    """Liefert das Profilbild aus – kein Auth erforderlich (public Ressource)."""
    safe_name = Path(filename).name
    path = _AVATAR_DIR / safe_name
    if not path.exists() or not path.is_file():
        return Response(status_code=404)
    import mimetypes
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return Response(content=path.read_bytes(), media_type=mime)
