"""Bild-Upload für Einsatzstellen (SiteMedia) und Einsatzjournal (LageJournalMedia)."""
from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.models.major_incident import LageJournalMedia, SiteMedia
from app.services.media_service import IMAGE_MIMES, _detect_mime

logger = logging.getLogger("einsatzleiter.lage_media")

_LAGE_MEDIA_DIR = "app_storage/lage_media"
_JOURNAL_MEDIA_DIR = "app_storage/lage_journal_media"


def _site_dir(site_id: int, org_id: int | None = None) -> Path:
    if org_id is not None:
        d = Path(_LAGE_MEDIA_DIR) / str(org_id) / str(site_id)
    else:
        d = Path(_LAGE_MEDIA_DIR) / str(site_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def site_media_path(media: SiteMedia) -> Path:
    org_id = getattr(media, "org_id", None)
    if org_id is not None:
        return Path(_LAGE_MEDIA_DIR) / str(org_id) / str(media.incident_site_id) / media.stored_filename
    return Path(_LAGE_MEDIA_DIR) / str(media.incident_site_id) / media.stored_filename


def site_thumb_path(media: SiteMedia) -> Path:
    fname = media.stored_filename.replace(".jpg", "_thumb.jpg")
    org_id = getattr(media, "org_id", None)
    if org_id is not None:
        return Path(_LAGE_MEDIA_DIR) / str(org_id) / str(media.incident_site_id) / fname
    return Path(_LAGE_MEDIA_DIR) / str(media.incident_site_id) / fname


async def upload_site_media(
    file: UploadFile,
    site_id: int,
    org_id: int | None = None,
    user_id: int | None = None,
    author_name: str | None = None,
    db=None,
) -> SiteMedia:
    """Verarbeitet das hochgeladene Bild und gibt ein (unflushed) SiteMedia-Objekt zurück."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")

    mime = _detect_mime(data) or (file.content_type or "")
    if mime not in IMAGE_MIMES:
        raise HTTPException(status_code=400, detail=f"Nur Bilder erlaubt (erhalten: {mime})")

    max_bytes = settings.MAX_UPLOAD_BYTES_IMAGE
    if len(data) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Datei zu groß (max {mb} MB)")

    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        raise HTTPException(status_code=500, detail="Pillow nicht verfügbar")

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail(
        (settings.MEDIA_IMAGE_MAX_WIDTH, settings.MEDIA_IMAGE_MAX_HEIGHT),
        Image.Resampling.LANCZOS,
    )

    dest = _site_dir(site_id, org_id)
    uid = uuid.uuid4().hex
    main_fn = f"{uid}.jpg"
    thumb_fn = f"{uid}_thumb.jpg"

    img.save(dest / main_fn, "JPEG", quality=85, optimize=True, progressive=True)
    thumb = img.copy()
    thumb.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.save(dest / thumb_fn, "JPEG", quality=80, optimize=True)

    stored_bytes = (dest / main_fn).stat().st_size
    if db is not None and org_id is not None:
        from app.services.storage_service import reserve_storage
        reserve_storage(db, org_id, stored_bytes)

    return SiteMedia(
        incident_site_id=site_id,
        stored_filename=main_fn,
        original_filename=(file.filename or "foto.jpg")[:255],
        media_type="image",
        uploaded_by=user_id,
        author_name=author_name,
        bytes=stored_bytes,
        org_id=org_id,
    )


def _journal_dir(entry_id: int, org_id: int | None = None) -> Path:
    if org_id is not None:
        d = Path(_JOURNAL_MEDIA_DIR) / str(org_id) / str(entry_id)
    else:
        d = Path(_JOURNAL_MEDIA_DIR) / str(entry_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def journal_media_path(media: LageJournalMedia) -> Path:
    org_id = getattr(media, "org_id", None)
    if org_id is not None:
        return Path(_JOURNAL_MEDIA_DIR) / str(org_id) / str(media.journal_entry_id) / media.stored_filename
    return Path(_JOURNAL_MEDIA_DIR) / str(media.journal_entry_id) / media.stored_filename


def journal_thumb_path(media: LageJournalMedia) -> Path:
    fname = media.stored_filename.replace(".jpg", "_thumb.jpg")
    org_id = getattr(media, "org_id", None)
    if org_id is not None:
        return Path(_JOURNAL_MEDIA_DIR) / str(org_id) / str(media.journal_entry_id) / fname
    return Path(_JOURNAL_MEDIA_DIR) / str(media.journal_entry_id) / fname


async def upload_journal_media(
    file: UploadFile,
    entry_id: int,
    org_id: int | None = None,
    user_id: int | None = None,
    author_name: str | None = None,
    db=None,
) -> LageJournalMedia:
    """Verarbeitet das hochgeladene Bild und gibt ein (unflushed) LageJournalMedia-Objekt zurück."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")

    mime = _detect_mime(data) or (file.content_type or "")
    if mime not in IMAGE_MIMES:
        raise HTTPException(status_code=400, detail=f"Nur Bilder erlaubt (erhalten: {mime})")

    max_bytes = settings.MAX_UPLOAD_BYTES_IMAGE
    if len(data) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Datei zu groß (max {mb} MB)")

    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        raise HTTPException(status_code=500, detail="Pillow nicht verfügbar")

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail(
        (settings.MEDIA_IMAGE_MAX_WIDTH, settings.MEDIA_IMAGE_MAX_HEIGHT),
        Image.Resampling.LANCZOS,
    )

    dest = _journal_dir(entry_id, org_id)
    uid = uuid.uuid4().hex
    main_fn = f"{uid}.jpg"
    thumb_fn = f"{uid}_thumb.jpg"

    img.save(dest / main_fn, "JPEG", quality=85, optimize=True, progressive=True)
    thumb = img.copy()
    thumb.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.save(dest / thumb_fn, "JPEG", quality=80, optimize=True)

    stored_bytes = (dest / main_fn).stat().st_size
    if db is not None and org_id is not None:
        from app.services.storage_service import reserve_storage
        reserve_storage(db, org_id, stored_bytes)

    return LageJournalMedia(
        journal_entry_id=entry_id,
        stored_filename=main_fn,
        original_filename=(file.filename or "foto.jpg")[:255],
        media_type="image",
        uploaded_by=user_id,
        author_name=author_name,
        bytes=stored_bytes,
        org_id=org_id,
    )
