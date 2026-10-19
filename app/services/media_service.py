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

import asyncio
import io
import logging
import mimetypes
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

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
MAX_MESSAGE_FILE_BYTES = 25 * 1024 * 1024
_DANGEROUS_FILE_MIMES = {
    "application/javascript", "application/x-javascript", "application/xhtml+xml",
    "image/svg+xml", "text/html", "text/javascript", "text/xml",
}
_DANGEROUS_FILE_EXTENSIONS = {
    ".bat", ".cmd", ".com", ".exe", ".hta", ".html", ".htm", ".js", ".mjs",
    ".msi", ".ps1", ".sh", ".svg", ".vbs", ".xml",
}

# ORM-Klassenname -> annotation_service media_typ, fuer delete_media() (siehe unten)
_ANNOTATABLE_TYP_BY_CLASS = {"TaskMedia": "task", "MessageMedia": "message", "PersonMedia": "person"}


@dataclass
class UploadResult:
    media: TaskMedia
    warnings: list[str]


def _storage_root() -> Path:
    root = Path(settings.MEDIA_STORAGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _task_dir(incident_id: int, task_id: int, org_id: int | None = None) -> Path:
    if org_id is not None:
        d = _storage_root() / str(org_id) / str(incident_id) / str(task_id)
    else:
        d = _storage_root() / str(incident_id) / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _entity_dir(incident_id: int, entity_type: str, entity_id: int, org_id: int | None = None) -> Path:
    if org_id is not None:
        d = _storage_root() / str(org_id) / str(incident_id) / entity_type / str(entity_id)
    else:
        d = _storage_root() / str(incident_id) / entity_type / str(entity_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _detect_mime(data: bytes) -> str | None:
    """Erkennt MIME-Typ anhand Magic-Bytes (nie anhand des Client-Headers)."""
    try:
        import filetype  # type: ignore
        kind = filetype.guess(data)
        return kind.mime if kind else None
    except ImportError:
        logger.error(
            "filetype-Bibliothek nicht installiert – Magic-Byte-MIME-Erkennung deaktiviert. "
            "Upload-Sicherheit eingeschränkt. Bitte `pip install filetype` ausführen."
        )
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


def _safe_filename(filename: str | None, default: str = "datei") -> str:
    """Keep only a basename and printable filename characters for downloads."""
    name = (filename or default).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip(". ")
    return name[:255] or default


def _file_mime(raw: bytes, filename: str | None, content_type: str | None) -> str:
    """Return a safe stored MIME type for a download-only attachment."""
    detected = _detect_mime(raw)
    hinted = (content_type or "").lower().split(";", 1)[0].strip()
    guessed = mimetypes.guess_type(filename or "")[0]
    mime = detected or hinted or guessed or "application/octet-stream"
    suffix = Path(filename or "").suffix.lower()
    if mime in _DANGEROUS_FILE_MIMES or suffix in _DANGEROUS_FILE_EXTENSIONS:
        return "application/octet-stream"
    return mime


# ── Bild-Pipeline ─────────────────────────────────────────────────
def _write_thumb(img, dest_path: Path) -> None:
    """Skaliert eine bereits geoeffnete PIL-Image-Instanz auf MEDIA_THUMB_SIZE und
    speichert sie als JPEG q=80 unter dest_path. Gemeinsamer Helper fuer den
    Upload-Pfad (_process_image) und die Thumb-Regenerierung nach Bildbearbeitung
    (annotation_service._regenerate_thumb)."""
    from PIL import Image

    thumb = img.copy()
    if thumb.mode not in ("RGB", "L"):
        thumb = thumb.convert("RGB")
    thumb.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.save(dest_path, "JPEG", quality=80, optimize=True)


def _process_image(data: bytes, dest_dir: Path) -> tuple[Path, Path, int, int, str]:
    """Verkleinert auf MAX_WIDTHxMAX_HEIGHT, EXIF-Rotation, schreibt JPEG + Thumb.
    Returns: (storage_path, thumb_path, width, height, mime_type)."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))  # type: ignore[assignment]
    img = ImageOps.exif_transpose(img)  # type: ignore[assignment]
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")  # type: ignore[assignment]

    img.thumbnail(
        (settings.MEDIA_IMAGE_MAX_WIDTH, settings.MEDIA_IMAGE_MAX_HEIGHT),
        Image.Resampling.LANCZOS,
    )

    uid = uuid.uuid4().hex
    main_path = dest_dir / f"{uid}.jpg"
    thumb_path = dest_dir / f"{uid}_thumb.jpg"

    img.save(main_path, "JPEG", quality=85, optimize=True, progressive=True)
    _write_thumb(img, thumb_path)

    return main_path, thumb_path, img.width, img.height, "image/jpeg"


def regenerate_thumbnail_from_bytes(image_bytes: bytes, dest_path: Path) -> bool:
    """Erzeugt/ueberschreibt ein Thumbnail-JPEG unter dest_path aus beliebigen
    Bilddaten (z.B. dem geflachten PNG einer Bildbearbeitung). Wird von
    annotation_service.save_annotation() aufgerufen, damit Miniaturansichten nach
    dem Bearbeiten sofort den aktuellen Stand zeigen. Gibt True bei Erfolg zurueck,
    False bei einem (best-effort geloggten) Fehler -- ein fehlgeschlagenes
    Thumb-Update darf den eigentlichen Annotation-Save nie verhindern."""
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(image_bytes))  # type: ignore[assignment]
        img = ImageOps.exif_transpose(img)  # type: ignore[assignment]
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _write_thumb(img, dest_path)
        return True
    except Exception:
        logger.warning("Thumb-Regenerierung fehlgeschlagen fuer %s", dest_path, exc_info=True)
        return False


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


# ── Quota helpers ─────────────────────────────────────────────────
def _reserve(db: Session, org_id: int | None, n_bytes: int) -> None:
    if org_id is None:
        return
    from app.services.storage_service import reserve_storage
    reserve_storage(db, org_id, n_bytes)


def _release(db: Session, org_id: int | None, n_bytes: int) -> None:
    if org_id is None:
        return
    from app.services.storage_service import release_storage
    release_storage(db, org_id, n_bytes)


# ── Public API ────────────────────────────────────────────────────
async def store_upload(
    file: UploadFile, task: Task, user: User, db: Session,
    org_id: int | None = None,
) -> UploadResult:
    """Validiert + verarbeitet einen Upload und legt einen TaskMedia-Eintrag an."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")

    mime = _detect_mime(raw)
    if mime is None:
        # Magic-Byte-Erkennung schlug fehl – Client-Content-Type nur als Hint,
        # niemals als Sicherheits-Entscheidung verwenden.
        client_ct = (file.content_type or "").lower().split(";")[0].strip()
        if client_ct in ALLOWED_MIMES:
            mime = client_ct
        else:
            raise HTTPException(415, "Dateityp konnte nicht erkannt werden und ist nicht erlaubt.")
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

    dest_dir = _task_dir(task.incident_id, task.id, org_id)
    storage_root = _storage_root().resolve()
    warnings: list[str] = []

    if kind == "image":
        main_p, thumb_p, w, h, out_mime = await asyncio.to_thread(_process_image, raw, dest_dir)
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = TaskMedia(
            task_id=task.id, incident_id=task.incident_id,
            uploaded_by_user_id=user.id,
            kind="image",
            original_filename=file.filename or "image",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            mime_type=out_mime, bytes=stored_bytes,
            width=w, height=h,
        )
    elif kind == "pdf":
        main_p, thumb_p, pages = await asyncio.to_thread(  # type: ignore[assignment]
            _process_pdf, raw, dest_dir, file.filename or "document.pdf",  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = TaskMedia(
            task_id=task.id, incident_id=task.incident_id,
            uploaded_by_user_id=user.id,
            kind="pdf",
            original_filename=file.filename or "document.pdf",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="application/pdf", bytes=stored_bytes,
            pages=pages,
        )
    else:  # video
        main_p, thumb_p, w, h, dur = await asyncio.to_thread(  # type: ignore[arg-type,assignment]
            _process_video, raw, dest_dir,  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = TaskMedia(
            task_id=task.id, incident_id=task.incident_id,
            uploaded_by_user_id=user.id,
            kind="video",
            original_filename=file.filename or "video.mp4",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="video/mp4", bytes=stored_bytes,
            width=w, height=h, duration_s=dur,
        )
        if thumb_p is None:
            warnings.append("Video-Vorschaubild konnte nicht erzeugt werden.")

    db.add(media)
    db.flush()
    return UploadResult(media=media, warnings=warnings)


async def store_upload_for_message(
    file: UploadFile, message: Message, user: User | None, db: Session,
    org_id: int | None = None,
) -> MessageMedia:
    """Like :func:`store_bytes_for_message`, reading the request upload first."""
    raw = await file.read()
    return await store_bytes_for_message(
        raw, file.filename, file.content_type, message, db, org_id, user=user,
    )


async def store_bytes_for_message(
    raw: bytes,
    filename: str | None,
    content_type: str | None,
    msg: Message,
    db: Session,
    org_id: int | None,
    user: User | None = None,
    allow_datei: bool = False,
) -> MessageMedia:
    """Store a message attachment supplied by a background worker or a route.

    Recognised visual media use the existing processing pipeline. With
    ``allow_datei`` (mail intake) everything else is retained as a download-only
    ``datei`` attachment; otherwise unsupported types are rejected with 415.
    """
    if not raw:
        raise HTTPException(400, "Leere Datei.")
    mime = _detect_mime(raw)
    if mime is None:
        client_ct = (content_type or "").lower().split(";")[0].strip()
        if client_ct in ALLOWED_MIMES:
            mime = client_ct
    kind = _kind_for_mime(mime) if mime else None
    if kind is None and not allow_datei:
        if mime is None:
            raise HTTPException(415, "Dateityp konnte nicht erkannt werden und ist nicht erlaubt.")
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    if kind and len(raw) > _size_limit_for_kind(kind):
        limit_mb = _size_limit_for_kind(kind) // (1024 * 1024)
        raise HTTPException(413, f"Datei zu gross. Limit fuer {kind}: {limit_mb} MB.")
    if not kind and len(raw) > MAX_MESSAGE_FILE_BYTES:
        raise HTTPException(413, "Datei zu gross. Limit fuer datei: 25 MB.")
    if kind == "image" and mime in {"image/heic", "image/heif"} and not _HEIC_OK:
        raise HTTPException(415, "HEIC-Dateien werden auf diesem Server nicht unterstuetzt (pillow-heif fehlt).")
    safe_filename = _safe_filename(filename)
    dest_dir = _entity_dir(msg.incident_id, "msg", msg.id, org_id)
    storage_root = _storage_root().resolve()
    if kind == "image":
        main_p, thumb_p, w, h, out_mime = await asyncio.to_thread(_process_image, raw, dest_dir)
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = MessageMedia(
            message_id=msg.id, incident_id=msg.incident_id,
            uploaded_by_user_id=user.id if user else None, kind="image",
            original_filename=safe_filename,
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            mime_type=out_mime, bytes=stored_bytes, width=w, height=h,
        )
    elif kind == "pdf":
        main_p, thumb_p, pages = await asyncio.to_thread(  # type: ignore[assignment]
            _process_pdf, raw, dest_dir, safe_filename,  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = MessageMedia(
            message_id=msg.id, incident_id=msg.incident_id,
            uploaded_by_user_id=user.id if user else None, kind="pdf",
            original_filename=safe_filename,
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="application/pdf", bytes=stored_bytes, pages=pages,
        )
    elif kind == "video":
        main_p, thumb_p, w, h, dur = await asyncio.to_thread(  # type: ignore[arg-type,assignment]
            _process_video, raw, dest_dir,  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = MessageMedia(
            message_id=msg.id, incident_id=msg.incident_id,
            uploaded_by_user_id=user.id if user else None, kind="video",
            original_filename=safe_filename,
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="video/mp4", bytes=stored_bytes, width=w, height=h, duration_s=dur,
        )
    else:
        suffix = Path(safe_filename).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix if suffix and len(suffix) <= 16 else '.bin'}"
        main_p = dest_dir / stored_name
        await asyncio.to_thread(main_p.write_bytes, raw)
        _reserve(db, org_id, len(raw))
        media = MessageMedia(
            message_id=msg.id, incident_id=msg.incident_id,
            uploaded_by_user_id=user.id if user else None, kind="datei",
            original_filename=safe_filename,
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=None, mime_type=_file_mime(raw, safe_filename, content_type), bytes=len(raw),
        )
    db.add(media)
    db.flush()
    return media


async def store_upload_for_person(
    file: UploadFile, person: RescuedPerson, user: User, db: Session,
    org_id: int | None = None,
) -> PersonMedia:
    """Wie store_upload, aber fuer Person-Anhaenge."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")
    mime = _detect_mime(raw)
    if mime is None:
        client_ct = (file.content_type or "").lower().split(";")[0].strip()
        if client_ct in ALLOWED_MIMES:
            mime = client_ct
        else:
            raise HTTPException(415, "Dateityp konnte nicht erkannt werden und ist nicht erlaubt.")
    if mime not in ALLOWED_MIMES:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    kind = _kind_for_mime(mime)
    if not kind:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")
    if len(raw) > _size_limit_for_kind(kind):
        limit_mb = _size_limit_for_kind(kind) // (1024 * 1024)
        raise HTTPException(413, f"Datei zu gross. Limit fuer {kind}: {limit_mb} MB.")
    dest_dir = _entity_dir(person.incident_id, "person", person.id, org_id)
    storage_root = _storage_root().resolve()
    if kind == "image":
        main_p, thumb_p, w, h, out_mime = await asyncio.to_thread(_process_image, raw, dest_dir)
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = PersonMedia(
            person_id=person.id, incident_id=person.incident_id,
            uploaded_by_user_id=user.id, kind="image",
            original_filename=file.filename or "image",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            mime_type=out_mime, bytes=stored_bytes, width=w, height=h,
        )
    elif kind == "pdf":
        main_p, thumb_p, pages = await asyncio.to_thread(  # type: ignore[assignment]
            _process_pdf, raw, dest_dir, file.filename or "document.pdf",  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = PersonMedia(
            person_id=person.id, incident_id=person.incident_id,
            uploaded_by_user_id=user.id, kind="pdf",
            original_filename=file.filename or "document.pdf",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="application/pdf", bytes=stored_bytes, pages=pages,
        )
    else:
        main_p, thumb_p, w, h, dur = await asyncio.to_thread(  # type: ignore[arg-type,assignment]
            _process_video, raw, dest_dir,  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        media = PersonMedia(
            person_id=person.id, incident_id=person.incident_id,
            uploaded_by_user_id=user.id, kind="video",
            original_filename=file.filename or "video.mp4",
            storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
            thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None,
            mime_type="video/mp4", bytes=stored_bytes, width=w, height=h, duration_s=dur,
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
    # Release storage quota
    n_bytes = getattr(media, "bytes", 0) or 0
    if n_bytes > 0:
        incident_id = getattr(media, "incident_id", None)
        if incident_id is not None:
            from app.models.incident import Incident
            incident = db.get(Incident, incident_id)
            if incident:
                _release(db, incident.primary_org_id, n_bytes)
    # Annotation (falls das Bild bearbeitet wurde) mit aufraeumen: sonst bleibt
    # die _annotated.png als Datei-Leiche liegen und ihre Quota-Reservierung
    # wird nie freigegeben (Bug, siehe Session 2026-07-12).
    _media_typ = _ANNOTATABLE_TYP_BY_CLASS.get(type(media).__name__)
    if _media_typ:
        from app.services.annotation_service import delete_annotation_and_files
        delete_annotation_and_files(db, _media_typ, media)
    db.delete(media)


def absolute_path(media: TaskMedia) -> Path:
    """Liefert den absoluten Pfad zur Hauptdatei (fuer FileResponse)."""
    return _storage_root() / media.storage_path


def absolute_thumb_path(media: TaskMedia) -> Path | None:
    if not media.thumb_path:
        return None
    return _storage_root() / media.thumb_path


# ── UAS-Medien-Pipeline ───────────────────────────────────────────────────────

def _uas_flug_dir(org_id: int, flug_id: int) -> Path:
    d = _storage_root() / "uas" / str(org_id) / str(flug_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def store_upload_for_uas_medien(
    file: UploadFile,
    flug_id: int,
    org_id: int,
    user,
    db: Session,
    begruendung: str = "",
    loeschfrist=None,
):
    """Verarbeitet einen Datei-Upload fuer UAS-Medien.

    Gleiche Pipeline wie task_media: Bilder werden komprimiert + Thumb erzeugt,
    Videos per ffmpeg transkodiert. Gibt ein befuelltes UASMedien-Objekt zurueck
    (noch nicht commitet).
    """

    from app.models.uas import UASMedien, UASMedienDsgvoStatus

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")

    mime = _detect_mime(raw)
    if mime is None:
        client_ct = (file.content_type or "").lower().split(";")[0].strip()
        if client_ct in ALLOWED_MIMES:
            mime = client_ct
        else:
            raise HTTPException(415, "Dateityp konnte nicht erkannt werden und ist nicht erlaubt.")
    if mime not in ALLOWED_MIMES:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")

    kind = _kind_for_mime(mime)
    if not kind:
        raise HTTPException(415, f"Dateityp '{mime}' wird nicht unterstuetzt.")

    if len(raw) > _size_limit_for_kind(kind):
        limit_mb = _size_limit_for_kind(kind) // (1024 * 1024)
        raise HTTPException(413, f"Datei zu gross. Limit fuer {kind}: {limit_mb} MB.")

    if kind == "image" and mime in {"image/heic", "image/heif"} and not _HEIC_OK:
        raise HTTPException(415, "HEIC-Dateien werden auf diesem Server nicht unterstuetzt.")

    dest_dir = _uas_flug_dir(org_id, flug_id)
    storage_root = _storage_root().resolve()
    original_filename = file.filename or "upload"

    if kind == "image":
        main_p, thumb_p, w, h, out_mime = await asyncio.to_thread(_process_image, raw, dest_dir)
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        medientyp = "foto"
        storage_path = str(main_p.resolve().relative_to(storage_root)).replace("\\", "/")
        tpath = str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/")
        width, height, duration_s = w, h, None
    elif kind == "pdf":
        main_p, _, pages = await asyncio.to_thread(_process_pdf, raw, dest_dir, original_filename)
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        out_mime = "application/pdf"
        medientyp = "dokument"
        storage_path = str(main_p.resolve().relative_to(storage_root)).replace("\\", "/")
        tpath = None
        width, height, duration_s = None, None, None
    else:  # video
        main_p, thumb_p, w, h, dur = await asyncio.to_thread(  # type: ignore[arg-type,assignment]
            _process_video, raw, dest_dir,  # type: ignore[arg-type]
        )
        stored_bytes = main_p.stat().st_size
        _reserve(db, org_id, stored_bytes)
        out_mime = "video/mp4"
        medientyp = "video"
        storage_path = str(main_p.resolve().relative_to(storage_root)).replace("\\", "/")
        tpath = str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/") if thumb_p else None
        width, height, duration_s = w, h, dur

    dsgvo = (
        UASMedienDsgvoStatus.begruendet.value
        if begruendung.strip()
        else UASMedienDsgvoStatus.erfasst.value
    )

    m = UASMedien(
        org_id=org_id,
        uas_flug_id=flug_id,
        dateiname=original_filename,
        dateipfad=storage_path,
        kind=kind,
        thumb_path=tpath,
        mime_type=out_mime,
        bytes=stored_bytes,
        width=width,
        height=height,
        duration_s=duration_s,
        uploaded_by_user_id=user.id,
        medientyp=medientyp,
        dsgvo_status=dsgvo,
        begruendung=begruendung.strip() or None,
        loeschfrist=loeschfrist,
        erstellt_von=getattr(user, "display_name", None) or getattr(user, "email", None),
    )
    db.add(m)
    db.flush()
    return m


def absolute_uas_path(medien) -> Path:
    return _storage_root() / medien.dateipfad


def absolute_uas_thumb_path(medien) -> Path | None:
    if not medien.thumb_path:
        return None
    return _storage_root() / medien.thumb_path


# ── Fahrtenbuch/Schaden-Fotos ─────────────────────────────────────────────────

def _fahrt_dir(org_id: int, fahrt_id: int) -> Path:
    d = _storage_root() / "fahrt" / str(org_id) / str(fahrt_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def absolute_fahrt_media_path(media) -> Path:
    return _storage_root() / media.storage_path


def absolute_fahrt_media_thumb_path(media) -> Path | None:
    if not media.thumb_path:
        return None
    return _storage_root() / media.thumb_path


async def store_upload_for_schaden_foto(
    file: StarletteUploadFile, fahrt_id: int, org_id: int, db: Session, user=None,
):
    """Verarbeitet ein Schadensfoto-Upload fuer eine Fahrt (nur Bilder — anders als
    task_media/uas_medien gibt es hier keinen Video-/PDF-Anwendungsfall). Gleiche
    Bild-Pipeline wie task_media (_process_image: Verkleinerung + Thumb). Gibt ein
    befuelltes FahrtMedia-Objekt zurueck (noch nicht committet). `user` ist optional,
    da Fahrten auch ueber den No-Login-Token-Zugang (siehe ui_fahrtenbuch.py) erfasst
    werden."""
    from app.models.fahrtenbuch import FahrtMedia

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leere Datei.")

    mime = _detect_mime(raw)
    if mime is None:
        client_ct = (file.content_type or "").lower().split(";")[0].strip()
        if client_ct in IMAGE_MIMES:
            mime = client_ct
        else:
            raise HTTPException(415, "Dateityp konnte nicht erkannt werden und ist nicht erlaubt.")
    if mime not in IMAGE_MIMES:
        raise HTTPException(415, f"Nur Fotos werden unterstuetzt (Dateityp '{mime}' abgelehnt).")
    if mime in {"image/heic", "image/heif"} and not _HEIC_OK:
        raise HTTPException(415, "HEIC-Dateien werden auf diesem Server nicht unterstuetzt.")
    if len(raw) > _size_limit_for_kind("image"):
        limit_mb = _size_limit_for_kind("image") // (1024 * 1024)
        raise HTTPException(413, f"Foto zu gross. Limit: {limit_mb} MB.")

    dest_dir = _fahrt_dir(org_id, fahrt_id)
    storage_root = _storage_root().resolve()
    main_p, thumb_p, width, height, out_mime = await asyncio.to_thread(_process_image, raw, dest_dir)
    stored_bytes = main_p.stat().st_size
    _reserve(db, org_id, stored_bytes)

    media = FahrtMedia(
        fahrt_id=fahrt_id,
        org_id=org_id,
        uploaded_by_user_id=user.id if user else None,
        original_filename=file.filename or "foto.jpg",
        storage_path=str(main_p.resolve().relative_to(storage_root)).replace("\\", "/"),
        thumb_path=str(thumb_p.resolve().relative_to(storage_root)).replace("\\", "/"),
        mime_type=out_mime,
        bytes=stored_bytes,
        width=width,
        height=height,
    )
    db.add(media)
    db.flush()
    return media


def delete_fahrt_media(media, db: Session) -> None:
    """Loescht ein FahrtMedia-Eintrag (Schadensfoto) inkl. gespeicherter Dateien und gibt Quota frei."""
    storage_root = _storage_root()
    for rel in (media.storage_path, media.thumb_path):
        if not rel:
            continue
        path = storage_root / rel
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            logger.warning("fahrt media delete failed for %s: %s", path, e)
    n_bytes = media.bytes or 0
    if n_bytes > 0:
        _release(db, media.org_id, n_bytes)
    db.delete(media)


def delete_uas_medien(medien, db: Session) -> None:
    """Loescht UASMedien-Eintrag inkl. gespeicherte Dateien und gibt Quota frei."""
    storage_root = _storage_root()
    for rel in (medien.dateipfad, medien.thumb_path):
        if not rel:
            continue
        path = storage_root / rel
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            logger.warning("uas delete failed for %s: %s", path, e)
    n_bytes = medien.bytes or 0
    if n_bytes > 0:
        _release(db, medien.org_id, n_bytes)
    db.delete(medien)


def store_upload_for_katplan_anhang(
    raw: bytes, filename: str, content_type: str | None, anhang, plan, db: Session,
) -> None:
    """Synchrone Katplan-Pipeline; Quota umfasst Original und Thumbnail.

    Buerodateien bleiben download-only. Magic Bytes haben Vorrang vor Client-MIME.
    Bei Verarbeitungs- oder Quota-Fehlern bleiben keine Dateien zurueck.
    """
    import zipfile

    name = _safe_filename(filename)
    suffix = Path(name).suffix.lower()
    mime = _detect_mime(raw)
    office = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".odt": "application/vnd.oasis.opendocument.text",
        ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    }
    if not raw or suffix in _DANGEROUS_FILE_EXTENSIONS:
        raise ValueError("Dateityp nicht erlaubt")
    kind = "image" if mime in IMAGE_MIMES else "pdf"
    if mime in IMAGE_MIMES:
        if mime in {"image/heic", "image/heif"} and not _HEIC_OK:
            raise ValueError("HEIC-Dateien werden auf diesem Server nicht unterstuetzt.")
    elif mime == "application/pdf":
        pass
    elif suffix in office and mime in (None, "application/zip", office[suffix]):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = set(archive.namelist())
                expected = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml",
                            ".odt": "content.xml", ".ods": "content.xml"}[suffix]
                if expected not in names:
                    raise ValueError("Dateityp nicht erlaubt")
        except zipfile.BadZipFile as exc:
            raise ValueError("Dateityp nicht erlaubt") from exc
        mime = office[suffix]
    elif suffix == ".txt" and mime is None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Dateityp nicht erlaubt") from exc
        if "\x00" in text or text.lstrip().lower().startswith(("<", "mz", "#!")):
            raise ValueError("Dateityp nicht erlaubt")
        mime = "text/plain"
    else:
        raise ValueError("Dateityp nicht erlaubt")
    if len(raw) > _size_limit_for_kind(kind):
        raise HTTPException(413, "Datei zu gross.")
    root = _storage_root().resolve()
    dest = root / str(plan.org_id) / "katplan" / plan.uuid / anhang.uuid
    dest.resolve().relative_to(root)
    dest.mkdir(parents=True, exist_ok=False)
    reserved = 0
    try:
        thumb = None
        width = height = None
        if kind == "image":
            try:
                main, thumb, width, height, mime = _process_image(raw, dest)
            except (OSError, ValueError) as exc:
                raise ValueError("Ungueltige Bilddatei.") from exc
        else:
            main = dest / name
            main.write_bytes(raw)
            # Helfer schuetzt auch kuenftige zusaetzliche Download-Typen.
            mime = _file_mime(raw, name, mime or content_type)
        size = main.stat().st_size + (thumb.stat().st_size if thumb else 0)
        _reserve(db, plan.org_id, size)
        reserved = size
        anhang.art = "foto" if kind == "image" else "datei"
        anhang.dateiname = name
        anhang.mime = mime
        anhang.pfad = main.relative_to(root).as_posix()
        anhang.thumb_pfad = thumb.relative_to(root).as_posix() if thumb else None
        anhang.breite, anhang.hoehe = width, height
        anhang.groesse_bytes = size
        db.add(anhang)
        db.flush()
    except Exception:
        shutil.rmtree(dest)
        if reserved:
            _release(db, plan.org_id, reserved)
        raise


def delete_katplan_anhang_dateien(anhang, db: Session) -> None:
    """Dateien sicher entfernen und den gesamten belegten Storage freigeben."""
    root = _storage_root().resolve()
    paths = [(root / rel).resolve() for rel in (anhang.pfad, anhang.thumb_pfad) if rel]
    for path in paths:
        path.relative_to(root)
    for path in paths:
        path.unlink(missing_ok=True)
    _release(db, anhang.org_id, anhang.groesse_bytes or 0)
