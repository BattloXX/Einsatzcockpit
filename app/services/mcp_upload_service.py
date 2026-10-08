"""Kurzlebige PDF-Ablage fuer den zweistufigen MCP-Dokument-Upload."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import SystemSettings
from app.models.mcp import MCPUpload
from app.models.user import User
from app.services.objekt_dokument_service import _detect_mime

logger = logging.getLogger("einsatzleiter.mcp_upload")
_CHUNK_BYTES = 1024 * 1024


class MCPUploadFehler(ValueError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def effektives_limit(db: Session) -> int:
    row = db.query(SystemSettings).filter(SystemSettings.key == "objekt_pdf_max_bytes").first()
    objekt_limit = settings.OBJEKT_PDF_MAX_BYTES
    if row and row.value:
        try:
            objekt_limit = int(row.value)
        except ValueError:
            pass
    return min(settings.MCP_UPLOAD_MAX_BYTES, objekt_limit)


def _upload_pfad(org_id: int, upload_id: str) -> tuple[Path, str]:
    relativ = f"_mcp_uploads/{org_id}/{upload_id}.pdf"
    return Path(settings.OBJEKT_MEDIA_DIR) / relativ, relativ


def erstelle_upload(
    db: Session, user: User, org_id: int, objekt_id: int | None, dateiname: str, groesse: int | None,
    *, road_closure_id: int | None = None, zweck: str = "objekt"
) -> tuple[MCPUpload, str]:
    if groesse is not None and (not isinstance(groesse, int) or groesse < 0):
        raise MCPUploadFehler("groesse_bytes muss eine nicht-negative Ganzzahl sein.")
    limit = effektives_limit(db)
    if groesse is not None and groesse > limit:
        raise MCPUploadFehler(f"Datei zu gross (max. {limit} Bytes).", 413)
    token = secrets.token_urlsafe(32)
    row = MCPUpload(
        upload_id=secrets.token_hex(16), token_hash=hash_api_key(token), org_id=org_id,
        user_id=user.id, objekt_id=objekt_id, road_closure_id=road_closure_id, zweck=zweck,
        dateiname=(dateiname or "dokument.pdf")[:255],
        erwartete_bytes=groesse, expires_at=_now() + timedelta(minutes=settings.MCP_UPLOAD_TOKEN_MINUTEN),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, token


def speichere_upload(db: Session, upload_id: str, token: str, stream: BinaryIO) -> MCPUpload:
    """Prueft den Bearer-Token und schreibt den Stream mit harter Obergrenze."""
    set_tenant_context(db, None)
    row = db.query(MCPUpload).filter(MCPUpload.upload_id == upload_id).first()
    if row is None or not hmac.compare_digest(row.token_hash, hash_api_key(token)):
        raise MCPUploadFehler("Upload-Token ungueltig.", 401)
    if row.expires_at < _now():
        raise MCPUploadFehler("Upload-Token ist abgelaufen.", 410)
    if row.hochgeladen_am is not None:
        raise MCPUploadFehler("Dieser Upload wurde bereits hochgeladen.", 409)

    limit = effektives_limit(db)
    ziel, relativ = _upload_pfad(row.org_id, row.upload_id)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    digest = hashlib.sha256()
    first = b""
    try:
        with ziel.open("xb") as out:
            while chunk := stream.read(_CHUNK_BYTES):
                total += len(chunk)
                if total > limit:
                    raise MCPUploadFehler(f"Datei zu gross (max. {limit} Bytes).", 413)
                if len(first) < 4096:
                    first += chunk[: 4096 - len(first)]
                digest.update(chunk)
                out.write(chunk)
        if not first.startswith(b"%PDF") or _detect_mime(first) != "application/pdf":
            raise MCPUploadFehler("Nur PDF-Dateien erlaubt.", 415)
        try:
            from pypdf import PdfReader
            seitenzahl = len(PdfReader(str(ziel)).pages)
        except Exception as exc:
            raise MCPUploadFehler("PDF konnte nicht gelesen werden.", 415) from exc
        row.hochgeladen_am = _now()
        row.pfad = relativ
        row.sha256 = digest.hexdigest()
        row.groesse_bytes = total
        row.seitenzahl = seitenzahl
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        ziel.unlink(missing_ok=True)
        db.rollback()
        raise


def lade_upload_fuer_uebergabe(
    db: Session, org_id: int, user_id: int, upload_id: str, zweck: str | None = None
) -> MCPUpload:
    row = db.query(MCPUpload).filter(
        MCPUpload.upload_id == upload_id, MCPUpload.org_id == org_id, MCPUpload.user_id == user_id
    ).first()
    if row is None:
        raise MCPUploadFehler("Upload nicht gefunden.", 404)
    if zweck is not None and row.zweck != zweck:
        raise MCPUploadFehler("Upload gehört zu einem anderen Zweck.", 403)
    if row.expires_at < _now():
        raise MCPUploadFehler("Upload ist abgelaufen.", 410)
    if row.hochgeladen_am is None or not row.pfad:
        raise MCPUploadFehler("Upload wurde noch nicht hochgeladen.")
    if row.uebergeben_am is not None:
        raise MCPUploadFehler("Upload wurde bereits uebergeben.", 409)
    return row


def purge_alte_uploads() -> int:
    cutoff = _now() - timedelta(hours=settings.MCP_UPLOAD_RETENTION_STUNDEN)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(MCPUpload).filter(
            (MCPUpload.created_at < cutoff) | (MCPUpload.uebergeben_am.is_not(None))
        ).all()
        for row in rows:
            if row.pfad:
                (Path(settings.OBJEKT_MEDIA_DIR) / row.pfad).unlink(missing_ok=True)
            db.delete(row)
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        logger.exception("MCP-Upload-Retention fehlgeschlagen")
        raise
    finally:
        db.close()


async def mcp_upload_retention_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(purge_alte_uploads)
        except Exception:
            logger.exception("Fehler im MCP-Upload-Retention-Loop")
