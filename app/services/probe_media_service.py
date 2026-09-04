"""Geschützte Upload-Pipeline für Skizzen und Dokumente der Probenplanung."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.probenplanung import ProbeMedia
from app.services.media_service import (
    IMAGE_MIMES,
    PDF_MIMES,
    _detect_mime,
    _kind_for_mime,
    _process_image,
    _process_pdf,
    _size_limit_for_kind,
)
from app.services.storage_service import release_storage, reserve_storage

logger = logging.getLogger("einsatzleiter.probe_media")

_ALLOWED_MIMES = IMAGE_MIMES | PDF_MIMES
_ARTEN = {"dokument", "skizze", "bild"}


def _storage_root() -> Path:
    root = Path(settings.PROBE_MEDIA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _probe_dir(org_id: int, termin_id: int) -> Path:
    directory = _storage_root() / str(org_id) / str(termin_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def probe_media_path(media: ProbeMedia) -> Path:
    return _storage_root() / media.path


def probe_thumb_path(media: ProbeMedia) -> Path | None:
    return _storage_root() / media.thumb_path if media.thumb_path else None


async def upload_probe_media(
    file: UploadFile,
    *,
    termin_id: int,
    org_id: int,
    user_id: int | None,
    art: str,
    name: str,
    typ: str | None,
    beschreibung: str | None,
    db: Session,
) -> ProbeMedia:
    """Verarbeitet JPG/PNG/PDF; MIME stammt ausschließlich aus Magic Bytes."""
    if art not in _ARTEN:
        raise HTTPException(422, "Ungültige Medienart")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei")
    mime = _detect_mime(raw)
    if mime not in _ALLOWED_MIMES:
        raise HTTPException(415, "Dateityp wird nicht unterstützt")
    kind = _kind_for_mime(mime)
    if kind not in {"image", "pdf"}:
        raise HTTPException(415, "Dateityp wird nicht unterstützt")
    if art in {"skizze", "bild"} and kind != "image":
        raise HTTPException(422, "Für eine Skizze ist ein Bild erforderlich")
    limit = _size_limit_for_kind(kind)
    if len(raw) > limit:
        raise HTTPException(413, f"Datei zu groß. Limit: {limit // (1024 * 1024)} MB")

    destination = _probe_dir(org_id, termin_id)
    root = _storage_root().resolve()
    thumb_path: Path | None
    if kind == "image":
        main_path, image_thumb_path, _width, _height, stored_mime = _process_image(raw, destination)
        thumb_path = image_thumb_path
    else:
        main_path, thumb_path, _pages = _process_pdf(raw, destination, file.filename or "dokument.pdf")
        stored_mime = "application/pdf"
    stored_bytes = main_path.stat().st_size
    try:
        reserve_storage(db, org_id, stored_bytes)
    except Exception:
        main_path.unlink(missing_ok=True)
        if thumb_path:
            thumb_path.unlink(missing_ok=True)
        raise

    def relative(path: Path) -> str:
        return str(path.resolve().relative_to(root)).replace("\\", "/")

    media = ProbeMedia(
        termin_id=termin_id,
        org_id=org_id,
        art=art,
        name=(name.strip() or file.filename or "Datei")[:255],
        typ=(typ.strip()[:50] if typ and typ.strip() else None),
        beschreibung=(beschreibung.strip() if beschreibung and beschreibung.strip() else None),
        kind=kind,
        mime_type=stored_mime,
        path=relative(main_path),
        thumb_path=relative(thumb_path) if thumb_path else None,
        size_bytes=stored_bytes,
        hochgeladen_von=user_id,
    )
    db.add(media)
    db.flush()
    return media


def delete_probe_media(db: Session, media: ProbeMedia) -> None:
    """Löscht Original, Thumbnail, Annotation und gibt die Quota frei."""
    from app.services.annotation_service import delete_annotation_and_files

    delete_annotation_and_files(db, "probe", media)
    for path in (probe_media_path(media), probe_thumb_path(media)):
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Probe-Medium konnte nicht gelöscht werden: %s", path, exc_info=True)
    if media.org_id is not None:
        release_storage(db, media.org_id, media.size_bytes)
    db.delete(media)
