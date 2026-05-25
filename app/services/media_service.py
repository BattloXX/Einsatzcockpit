"""Media-Upload-Pipeline fuer Auftrag-Anhaenge.

Bilder werden mit Pillow auf settings.MEDIA_IMAGE_MAX_WIDTH/HEIGHT verkleinert
und als JPEG (q=85) gespeichert. HEIC wird via pillow-heif unterstuetzt.
PDFs werden 1:1 gespeichert (Thumb optional aus erster Seite).
Videos werden via ffmpeg auf settings.MEDIA_VIDEO_MAX_HEIGHT transcodiert,
Thumb wird aus Frame bei 1s extrahiert.

Storage-Layout (alle Pfade relativ zu settings.MEDIA_STORAGE_DIR):
  {incident_id}/{task_id}/{uuid}.{ext}
  {incident_id}/{task_id}/{uuid}_thumb.jpg

Datei-Auslieferung erfolgt ausschliesslich ueber /medien/datei/{id} mit
Org-Check, damit Multi-Tenant-Isolation gewahrt bleibt.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.incident import Message, MessageMedia, PersonMedia, RescuedPerson, Task, TaskMedia
from app.models.user import User

logger = logging.getLogger("einsatzleiter.media")


# ── HEIC-Support optional registrieren ────────────────────────────
try:
    from pillow_heif import register_heif_opener  # type: ignore
    register_heif_opener()
    _HEIC_OK = True
except Exception:  # pillow_heif fehlt -> HEIC wird abgelehnt
    _HEIC_OK = False


IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
}
PDF_MIMES = {"application/pdf"}
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}

ALLOWED_MIMES = IMAGE_MIMES | PDF_MIMES | VIDEO_MIMES


@dataclass
class UploadResult:
    media: TaskMedia
    warnings: list[str]


def _storage_root() -> Path:
    root = Path(settings.MEDIA_STORAGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _task_dir(incident_id: int, task_id: int) -> Path:
    d = _storage_root() / str(incident_id) / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _entity_dir(incident_id: int, entity_type: str, entity_id: int) -> Path:
    d = _storage_root() / str(incident_id) / entity_type / str(entity_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _detect_mime(data: bytes) -> str | None:
    try:
        import filetype  # type: ignore
        kind = filetype.guess(data)
        return kind.mime if kind else None
    except ImportError:
        return None


def _kind_for_mime(mime: str) -> str | None:
    if mime in IMAGE_MIMES:
        return "image"
    if mime in PDF_MIMES:
        return "pdf"
    if mime in VIDEO_MIMES:
        return "video"
    return None


def _size_limit_for_kind(kind: str) -> int:
    return {
        "image": settings.MAX_UPLOAD_BYTES_IMAGE,
        "pdf":   settings.MAX_UPLOAD_BYTES_PDF,
        "video": settings.MAX_UPLOAD_BYTES_VIDEO,
    }[kind]


# ── Bild-Pipeline ─────────────────────────────────────────────────
def _process_image(data: bytes, dest_dir: Path) -> tuple[Path, Path, int, int, str]:
    """Verkleinert auf MAX_WIDTHxMAX_HEIGHT, EXIF-Rotation, schreibt JPEG + Thumb.
    Returns: (storage_path, thumb_path, width, height, mime_type)."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    img.thumbnail(
        (settings.MEDIA_IMAGE_MAX_WIDTH, settings.MEDIA_IMAGE_MAX_HEIGHT),
        Image.LANCZOS,
    )

    uid = uuid.uuid4().hex
    main_path = dest_dir / f"{uid}.jpg"
    thumb_path = dest_dir / f"{uid}_thumb.jpg"

    img.save(main_path, "JPEG", quality=85, optimize=True, progressive=True)

    thumb = img.copy()
    thumb.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE), Image.LANCZOS)
    thumb.save(thumb_path, "JPEG", quality=80, optimize=True)

    return main_path, thumb_path, img.width, img.height, "image/jpeg"


# ── PDF-Pipeline ──────────────────────────────────────────────────
def _process_pdf(data: bytes, dest_dir: Path, original_filename: str) -> tuple[Path, Path | None, int | None]:
    """Speichert PDF 1:1, ermittelt Seitenanzahl. Kein Page-Thumb (zu aufwendig)."""
    uid = uuid.uuid4().hex
    main_path = dest_dir / f"{uid}.pdf"
    main_path.write_bytes(data)

    pages: int | None = None
    try:
        from pypdf import PdfReader  # type: ignore
        pages = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as e:  # noqa: BLE001
        logger.debug("pdf page count failed for %s: %s", original_filename, e)

    return main_path, None, pages


# ── Video-Pipeline ────────────────────────────────────────────────
def _have_ffmpeg() -> bool:
    return shutil.which(settings.FFMPEG_BIN) is not None


def _process_video(
    data: bytes, dest_dir: Path,
) -> tuple[Path, Path | None, int | None, int | None, float | None]:
    """Transkodiert auf MEDIA_VIDEO_MAX_HEIGHT, extrahiert Thumb-Frame."""
    if not _have_ffmpeg():
        raise HTTPException(
            500,
            "ffmpeg ist auf dem Server nicht installiert. Video-Uploads sind deaktiviert.",
        )

    uid = uuid.uuid4().hex
    src = dest_dir / f".tmp_{uid}.bin"
    src.write_bytes(data)

    main_path = dest_dir / f"{uid}.mp4"
    thumb_path = dest_dir / f"{uid}_thumb.jpg"

    try:
        # Transcode to 720p MP4, H.264, AAC. -y = overwrite.
        cmd = [
            settings.FFMPEG_BIN, "-y", "-i", str(src),
            "-vf", f"scale=-2:min({settings.MEDIA_VIDEO_MAX_HEIGHT}\\,ih)",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(main_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)

        # Thumb @ 1s
        try:
            subprocess.run(
                [settings.FFMPEG_BIN, "-y", "-ss", "1", "-i", str(main_path),
                 "-vframes", "1", "-vf",
                 f"scale={settings.MEDIA_THUMB_SIZE}:-2",
                 str(thumb_path)],
                check=True, capture_output=True, timeout=30,
            )
        except subprocess.SubprocessError as e:
            logger.warning("video thumb failed: %s", e)
            thumb_path = None  # type: ignore

        # Probe dimensions/duration
        width = height = None
        duration_s: float | None = None
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height:format=duration",
                 "-of", "csv=p=0", str(main_path)],
                check=True, capture_output=True, text=True, timeout=15,
            )
            out = probe.stdout.strip().splitlines()
            if out:
                parts = out[0].split(",")
                if len(parts) >= 2:
                    width, height = int(parts[0]), int(parts[1])
            if len(out) > 1:
                duration_s = float(out[1])
        except (subprocess.SubprocessError, ValueError) as e:
            logger.debug("ffprobe failed: %s", e)
    finally:
        try:
            src.unlink()
        except OSError:
            pass

    return main_path, thumb_path if thumb_path else None, width, height, duration_s


# ── Public API ────────────────────────────────────────────────────
async def store_upload(
    file: UploadFile, task: Task, user: User, db: Session,
) -> UploadResult:
    """Validiert + verarbeitet einen Upload und legt einen TaskMedia-Eintrag an."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")

    mime = _detect_mime(raw) or (file.content_type or "").lower()
    if mime not in ALLOWED_MIMES:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")

    kind = _kind_for_mime(mime)
    if not kind:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")

    if len(raw) > _size_limit_for_kind(kind):
        limit_mb = _size_limit_for_kind(kind) // (1024 * 1024)
        raise HTTPException(413, f"Datei zu gross. Limit fuer {kind}: {limit_mb} MB.")

    if kind == "image" and mime in {"image/heic", "image/heif"} and not _HEIC_OK:
        raise HTTPException(
            415, "HEIC-Dateien werden auf diesem Server nicht unterstuetzt (pillow-heif fehlt).",
        )

    dest_dir = _task_dir(task.incident_id, task.id)
    storage_root = _storage_root().resolve()
    warnings: list[str] = []

    if kind == "image":
        main_p, thumb_p, w, h, out_mime = _process_image(raw, dest_dir)
        media = TaskMedia(
            task_id=task.id, incident_id=task.incident_id,
            uploaded_by_user_id=user.id,
            kind="image",
            original_filename=file.filename or "image",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            mime_type=out_mime, bytes=main_p.stat().st_size,
            width=w, height=h,
        )
    elif kind == "pdf":
        main_p, thumb_p, pages = _process_pdf(raw, dest_dir, file.filename or "document.pdf")
        media = TaskMedia(
            task_id=task.id, incident_id=task.incident_id,
            uploaded_by_user_id=user.id,
            kind="pdf",
            original_filename=file.filename or "document.pdf",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="application/pdf", bytes=main_p.stat().st_size,
            pages=pages,
        )
    else:  # video
        main_p, thumb_p, w, h, dur = _process_video(raw, dest_dir)
        media = TaskMedia(
            task_id=task.id, incident_id=task.incident_id,
            uploaded_by_user_id=user.id,
            kind="video",
            original_filename=file.filename or "video.mp4",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="video/mp4", bytes=main_p.stat().st_size,
            width=w, height=h, duration_s=dur,
        )
        if thumb_p is None:
            warnings.append("Video-Vorschaubild konnte nicht erzeugt werden.")

    db.add(media)
    db.flush()
    return UploadResult(media=media, warnings=warnings)


async def store_upload_for_message(
    file: UploadFile, message: Message, user: User, db: Session,
) -> MessageMedia:
    """Wie store_upload, aber fuer Message-Anhaenge."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")
    mime = _detect_mime(raw) or (file.content_type or "").lower()
    if mime not in ALLOWED_MIMES:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    kind = _kind_for_mime(mime)
    if not kind:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    if len(raw) > _size_limit_for_kind(kind):
        limit_mb = _size_limit_for_kind(kind) // (1024 * 1024)
        raise HTTPException(413, f"Datei zu gross. Limit fuer {kind}: {limit_mb} MB.")
    dest_dir = _entity_dir(message.incident_id, "msg", message.id)
    storage_root = _storage_root().resolve()
    if kind == "image":
        main_p, thumb_p, w, h, out_mime = _process_image(raw, dest_dir)
        media = MessageMedia(
            message_id=message.id, incident_id=message.incident_id,
            uploaded_by_user_id=user.id, kind="image",
            original_filename=file.filename or "image",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            mime_type=out_mime, bytes=main_p.stat().st_size, width=w, height=h,
        )
    elif kind == "pdf":
        main_p, thumb_p, pages = _process_pdf(raw, dest_dir, file.filename or "document.pdf")
        media = MessageMedia(
            message_id=message.id, incident_id=message.incident_id,
            uploaded_by_user_id=user.id, kind="pdf",
            original_filename=file.filename or "document.pdf",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="application/pdf", bytes=main_p.stat().st_size, pages=pages,
        )
    else:
        main_p, thumb_p, w, h, dur = _process_video(raw, dest_dir)
        media = MessageMedia(
            message_id=message.id, incident_id=message.incident_id,
            uploaded_by_user_id=user.id, kind="video",
            original_filename=file.filename or "video.mp4",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="video/mp4", bytes=main_p.stat().st_size, width=w, height=h, duration_s=dur,
        )
    db.add(media)
    db.flush()
    return media


async def store_upload_for_person(
    file: UploadFile, person: RescuedPerson, user: User, db: Session,
) -> PersonMedia:
    """Wie store_upload, aber fuer Person-Anhaenge."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")
    mime = _detect_mime(raw) or (file.content_type or "").lower()
    if mime not in ALLOWED_MIMES:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    kind = _kind_for_mime(mime)
    if not kind:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    if len(raw) > _size_limit_for_kind(kind):
        limit_mb = _size_limit_for_kind(kind) // (1024 * 1024)
        raise HTTPException(413, f"Datei zu gross. Limit fuer {kind}: {limit_mb} MB.")
    dest_dir = _entity_dir(person.incident_id, "person", person.id)
    storage_root = _storage_root().resolve()
    if kind == "image":
        main_p, thumb_p, w, h, out_mime = _process_image(raw, dest_dir)
        media = PersonMedia(
            person_id=person.id, incident_id=person.incident_id,
            uploaded_by_user_id=user.id, kind="image",
            original_filename=file.filename or "image",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            mime_type=out_mime, bytes=main_p.stat().st_size, width=w, height=h,
        )
    elif kind == "pdf":
        main_p, thumb_p, pages = _process_pdf(raw, dest_dir, file.filename or "document.pdf")
        media = PersonMedia(
            person_id=person.id, incident_id=person.incident_id,
            uploaded_by_user_id=user.id, kind="pdf",
            original_filename=file.filename or "document.pdf",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="application/pdf", bytes=main_p.stat().st_size, pages=pages,
        )
    else:
        main_p, thumb_p, w, h, dur = _process_video(raw, dest_dir)
        media = PersonMedia(
            person_id=person.id, incident_id=person.incident_id,
            uploaded_by_user_id=user.id, kind="video",
            original_filename=file.filename or "video.mp4",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="video/mp4", bytes=main_p.stat().st_size, width=w, height=h, duration_s=dur,
        )
    db.add(media)
    db.flush()
    return media


def delete_media(media, db: Session) -> None:
    """Loescht einen Media-Eintrag (TaskMedia/MessageMedia/PersonMedia) inkl. Dateien."""
    storage_root = _storage_root()
    for rel in (media.storage_path, media.thumb_path):
        if not rel:
            continue
        path = storage_root / rel
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            logger.warning("delete failed for %s: %s", path, e)
    db.delete(media)


def absolute_path(media: TaskMedia) -> Path:
    """Liefert den absoluten Pfad zur Hauptdatei (fuer FileResponse)."""
    return _storage_root() / media.storage_path


def absolute_thumb_path(media: TaskMedia) -> Path | None:
    if not media.thumb_path:
        return None
    return _storage_root() / media.thumb_path
