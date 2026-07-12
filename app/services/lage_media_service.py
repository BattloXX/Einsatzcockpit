"""Bild-Upload für Einsatzstellen (SiteMedia), Einsatzjournal (LageJournalMedia)
und CrossSiteMarker (CrossMarkerMedia).

Bildverarbeitung (EXIF-Transpose, Resize, JPEG-Encode) nutzt dieselbe Pipeline
wie media_service._process_image (KONS-1) — keine eigene Kopie mehr."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.major_incident import CrossMarkerMedia, LageJournalMedia, SiteMedia
from app.services.media_service import IMAGE_MIMES, _detect_mime, _process_image

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

    dest = _site_dir(site_id, org_id)
    main_p, _thumb_p, _w, _h, _mime = _process_image(data, dest)
    main_fn = main_p.name

    stored_bytes = main_p.stat().st_size
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


def copy_citizen_photo_to_site(
    citizen_photo_path: Path,
    site_id: int,
    org_id: int | None = None,
    user_id: int | None = None,
    author_name: str | None = None,
    db=None,
) -> SiteMedia | None:
    """Überträgt ein Bürgermeldungs-Foto auf eine Einsatzstelle (SiteMedia)."""
    if not citizen_photo_path.exists():
        return None
    try:
        data = citizen_photo_path.read_bytes()
        dest = _site_dir(site_id, org_id)
        main_p, _thumb_p, _w, _h, _mime = _process_image(data, dest)
        main_fn = main_p.name
        stored_bytes = main_p.stat().st_size
        if db is not None and org_id is not None:
            from app.services.storage_service import reserve_storage
            reserve_storage(db, org_id, stored_bytes)
        return SiteMedia(
            incident_site_id=site_id,
            stored_filename=main_fn,
            original_filename="bürgermeldung.jpg",
            media_type="image",
            uploaded_by=user_id,
            author_name=author_name,
            bytes=stored_bytes,
            org_id=org_id,
        )
    except Exception:
        logger.warning("Bürgermeldungs-Foto konnte nicht übertragen werden", exc_info=True)
        return None


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

    dest = _journal_dir(entry_id, org_id)
    main_p, _thumb_p, _w, _h, _mime = _process_image(data, dest)
    main_fn = main_p.name

    stored_bytes = main_p.stat().st_size
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


# ── CrossSiteMarker-Medien ─────────────────────────────────────────────────────

_CROSS_MEDIA_DIR = "app_storage/cross_marker_media"


def _cross_media_dir(marker_id: int, org_id: int | None = None) -> Path:
    if org_id is not None:
        d = Path(_CROSS_MEDIA_DIR) / str(org_id) / str(marker_id)
    else:
        d = Path(_CROSS_MEDIA_DIR) / str(marker_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cross_media_path(media: CrossMarkerMedia) -> Path:
    org_id = getattr(media, "org_id", None)
    if org_id is not None:
        return Path(_CROSS_MEDIA_DIR) / str(org_id) / str(media.marker_id) / media.stored_filename
    return Path(_CROSS_MEDIA_DIR) / str(media.marker_id) / media.stored_filename


def cross_media_thumb_path(media: CrossMarkerMedia) -> Path:
    fname = media.stored_filename.replace(".jpg", "_thumb.jpg")
    org_id = getattr(media, "org_id", None)
    if org_id is not None:
        return Path(_CROSS_MEDIA_DIR) / str(org_id) / str(media.marker_id) / fname
    return Path(_CROSS_MEDIA_DIR) / str(media.marker_id) / fname


async def upload_cross_media(
    file: UploadFile,
    marker_id: int,
    org_id: int | None = None,
    user_id: int | None = None,
    author_name: str | None = None,
    db=None,
) -> CrossMarkerMedia:
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

    dest = _cross_media_dir(marker_id, org_id)
    main_p, _thumb_p, _w, _h, _mime = _process_image(data, dest)
    main_fn = main_p.name

    stored_bytes = main_p.stat().st_size
    if db is not None and org_id is not None:
        from app.services.storage_service import reserve_storage
        reserve_storage(db, org_id, stored_bytes)

    return CrossMarkerMedia(
        marker_id=marker_id,
        stored_filename=main_fn,
        original_filename=(file.filename or "foto.jpg")[:255],
        media_type="image",
        uploaded_by=user_id,
        author_name=author_name,
        bytes=stored_bytes,
        org_id=org_id,
    )


def delete_cross_media_files(media: CrossMarkerMedia) -> None:
    """Löscht Bild- und Thumbnail-Dateien vom Dateisystem (ignore errors)."""
    for p in (cross_media_path(media), cross_media_thumb_path(media)):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# ── Loeschen inkl. Quota-Freigabe + Annotation-Cleanup ────────────────────────
# GSL-Medien (Site/CrossMarker/LageJournal) hatten bisher KEINE Loeschfunktion,
# die die beim Upload reservierte Quota wieder freigibt (media_service.delete_media
# macht das fuer Task/Message/Person schon lange richtig) -- Bug, siehe Session
# 2026-07-12: jedes geloeschte GSL-Foto liess used_bytes dauerhaft zu hoch stehen.

def delete_site_media(media: SiteMedia, db: Session) -> None:
    """Loescht eine SiteMedia-Zeile inkl. Dateien, Annotation und Quota-Freigabe."""
    for p in (site_media_path(media), site_thumb_path(media)):
        try:
            if p and p.exists():
                p.unlink()
        except OSError as e:
            logger.warning("site media delete failed for %s: %s", p, e)
    n_bytes = media.bytes or 0
    if n_bytes > 0 and media.org_id:
        from app.services.storage_service import release_storage
        release_storage(db, media.org_id, n_bytes)
    from app.services.annotation_service import delete_annotation_and_files
    delete_annotation_and_files(db, "site", media)
    db.delete(media)


def delete_cross_marker_media(media: CrossMarkerMedia, db: Session) -> None:
    """Loescht eine CrossMarkerMedia-Zeile inkl. Dateien, Annotation und Quota-Freigabe."""
    delete_cross_media_files(media)
    n_bytes = media.bytes or 0
    if n_bytes > 0 and media.org_id:
        from app.services.storage_service import release_storage
        release_storage(db, media.org_id, n_bytes)
    from app.services.annotation_service import delete_annotation_and_files
    delete_annotation_and_files(db, "cross_marker", media)
    db.delete(media)


def delete_journal_media(media: LageJournalMedia, db: Session) -> None:
    """Loescht eine LageJournalMedia-Zeile inkl. Dateien, Annotation und Quota-Freigabe."""
    for p in (journal_media_path(media), journal_thumb_path(media)):
        try:
            if p and p.exists():
                p.unlink()
        except OSError as e:
            logger.warning("journal media delete failed for %s: %s", p, e)
    n_bytes = media.bytes or 0
    if n_bytes > 0 and media.org_id:
        from app.services.storage_service import release_storage
        release_storage(db, media.org_id, n_bytes)
    from app.services.annotation_service import delete_annotation_and_files
    delete_annotation_and_files(db, "lage_journal", media)
    db.delete(media)
