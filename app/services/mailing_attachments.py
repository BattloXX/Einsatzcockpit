"""Safe campaign attachment storage and quota accounting."""

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.config import settings
from app.models.mailing import MailingCampaignAttachment
from app.services.media_service import _detect_mime
from app.services.storage_service import release_storage, reserve_storage

_EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "text/plain": "txt", "text/csv": "csv"}


def store_attachment(db, campaign, filename, data, user_id=None):
    if campaign.status != "draft":
        raise HTTPException(409, "Anhänge sind nur im Entwurf erlaubt.")
    if not data or len(data) > settings.MAILING_ATTACHMENT_MAX_BYTES:
        raise HTTPException(413, "Datei ist zu groß oder leer.")
    if (
        len(campaign.attachments) >= settings.MAILING_ATTACHMENT_MAX_COUNT
        or sum(x.bytes for x in campaign.attachments) + len(data) > settings.MAILING_ATTACHMENT_MAX_TOTAL_BYTES
    ):
        raise HTTPException(413, "Anhangslimit der Kampagne überschritten.")
    mime = _detect_mime(data)
    if mime not in _EXT:
        raise HTTPException(415, "Dateityp nicht unterstützt.")
    reserve_storage(db, campaign.org_id, len(data))
    root = Path(settings.MEDIA_STORAGE_DIR) / "mailing" / str(campaign.org_id) / str(campaign.id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{uuid4().hex}.{_EXT[mime]}"
    try:
        path.write_bytes(data)
    except Exception:
        release_storage(db, campaign.org_id, len(data))
        raise
    obj = MailingCampaignAttachment(
        org_id=campaign.org_id,
        campaign_id=campaign.id,
        original_filename=Path(filename or "attachment").name,
        storage_path=str(path),
        mime_type=mime,
        bytes=len(data),
        uploaded_by_user_id=user_id,
    )
    db.add(obj)
    db.flush()
    return obj


def delete_attachment(db, attachment):
    try:
        Path(attachment.storage_path).unlink(missing_ok=True)
    finally:
        release_storage(db, attachment.org_id, attachment.bytes)
        db.delete(attachment)
        db.flush()


def resend_attachments(campaign, cache):
    import base64

    result = []
    for item in campaign.attachments:
        if item.storage_path not in cache:
            cache[item.storage_path] = Path(item.storage_path).read_bytes()
        raw = cache[item.storage_path]
        result.append({"filename": item.original_filename, "content": base64.b64encode(raw).decode("ascii")})
    return result
