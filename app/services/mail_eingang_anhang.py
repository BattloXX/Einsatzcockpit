"""Sicherer, quellenneutraler Zugriff auf Anhänge eingehender E-Mails."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.org_mail import OrgMailEingang
from app.services.mail_eingang_service import ereignis
from app.services.resend_inbound_service import (
    MAX_ATTACHMENT_BYTES,
    RESEND_ATTACHMENTS_URL,
    attachment_content_type,
    get_resend_attachment,
    resend_api_key_fuer_org,
    safe_attachment_filename,
    safe_attachment_url,
)

MAX_MAIL_BYTES = 35 * 1024 * 1024
_LOCAL_ATTACHMENT_ID = re.compile(r"l\d+\Z")


def _verzeichnis(row: OrgMailEingang) -> Path:
    return Path(settings.MEDIA_STORAGE_DIR) / "mail_eingang" / str(row.org_id) / str(row.id)


def _metadata(row: OrgMailEingang) -> list[dict]:
    try:
        value = json.loads(row.anhang_json or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def speichere_lokal(db: Session, row: OrgMailEingang, anhaenge: list[dict]) -> list[dict]:
    """Speichert MIME-Anhänge ohne vom Absender beeinflusste Dateipfade."""
    directory = _verzeichnis(row)
    total = 0
    result: list[dict] = []
    for number, item in enumerate(anhaenge, start=1):
        data = item.get("data") if isinstance(item, dict) else None
        data = data if isinstance(data, bytes) else b""
        filename = safe_attachment_filename(item.get("filename") if isinstance(item, dict) else None)
        content_type = attachment_content_type(item.get("content_type") if isinstance(item, dict) else None)
        entry = {
            "id": f"l{number}", "filename": filename, "content_type": content_type, "size": len(data),
            "lokal": True,
        }
        if len(data) > MAX_ATTACHMENT_BYTES or total + len(data) > MAX_MAIL_BYTES:
            entry["zu_gross"] = True
            ereignis(db, row, "anhang_zu_gross", filename)
            result.append(entry)
            continue
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / str(number)).write_bytes(data)
            total += len(data)
        except OSError:
            entry["fehler"] = True
            ereignis(db, row, "anhang_fehler", filename)
        result.append(entry)
    row.anhang_json = json.dumps(result, ensure_ascii=False)
    row.anhang_speicher = "lokal"
    return result


def _lokaler_anhang(row: OrgMailEingang, anhang_id: str) -> tuple[bytes, str, str] | None:
    if not _LOCAL_ATTACHMENT_ID.fullmatch(anhang_id):
        return None
    item = next((item for item in _metadata(row) if item.get("id") == anhang_id), None)
    if not isinstance(item, dict) or item.get("zu_gross") or item.get("fehler"):
        return None
    directory = _verzeichnis(row).resolve()
    path = (directory / anhang_id[1:]).resolve()
    if path.parent != directory:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_ATTACHMENT_BYTES:
        return None
    return data, safe_attachment_filename(item.get("filename")), attachment_content_type(item.get("content_type"))


async def lade_anhang(db: Session, row: OrgMailEingang, anhang_id: str) -> tuple[bytes, str, str] | None:
    if row.anhang_speicher == "lokal":
        return _lokaler_anhang(row, anhang_id)
    if row.org_id is None:
        return None
    api_key = resend_api_key_fuer_org(db, row.org_id)
    if not api_key:
        return None
    attachment = await get_resend_attachment(api_key, row.extern_id, anhang_id[:200])
    if not attachment:
        return None
    url = attachment.get("download_url")
    if not isinstance(url, str):
        return None
    data = bytearray()
    try:
        async with httpx.AsyncClient(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
            async with client.stream("GET", url) as response:
                if not response.is_success:
                    return None
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    # Wie bisher beim Download abbrechen, nicht erst nach dem vollständigen Laden.
                    if len(data) > MAX_ATTACHMENT_BYTES:
                        return None
    except httpx.HTTPError:
        return None
    return bytes(data), safe_attachment_filename(attachment.get("filename")), attachment_content_type(
        attachment.get("content_type")
    )


def _get_resend_attachment_sync(api_key: str, email_id: str, attachment_id: str) -> dict | None:
    after: str | None = None
    with httpx.Client(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
        for _ in range(100):
            params: dict[str, str | int] = {"limit": 100}
            if after:
                params["after"] = after
            response = client.get(
                RESEND_ATTACHMENTS_URL.format(email_id=email_id), params=params,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if not response.is_success:
                return None
            payload = response.json()
            items = payload.get("data", payload.get("attachments", [])) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                return None
            for item in items:
                if isinstance(item, dict) and str(item.get("id")) == attachment_id:
                    if safe_attachment_url(str(item.get("download_url") or ""), from_resend_api=True):
                        return item
                    return None
            after = payload.get("next_after") or payload.get("after") if isinstance(payload, dict) else None
            if not after or len(items) < 100:
                return None
    return None


def lade_anhang_sync(db: Session, row: OrgMailEingang, anhang_id: str) -> tuple[bytes, str, str] | None:
    if row.anhang_speicher == "lokal":
        return _lokaler_anhang(row, anhang_id)
    if row.org_id is None:
        return None
    api_key = resend_api_key_fuer_org(db, row.org_id)
    if not api_key:
        return None
    attachment = _get_resend_attachment_sync(api_key, row.extern_id, anhang_id[:200])
    if not attachment:
        return None
    url = attachment.get("download_url")
    if not isinstance(url, str):
        return None
    data = bytearray()
    try:
        with httpx.Client(timeout=settings.RESEND_HTTP_TIMEOUT) as client:
            with client.stream("GET", url) as response:
                if not response.is_success:
                    return None
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_ATTACHMENT_BYTES:
                        return None
    except httpx.HTTPError:
        return None
    return bytes(data), safe_attachment_filename(attachment.get("filename")), attachment_content_type(
        attachment.get("content_type")
    )
