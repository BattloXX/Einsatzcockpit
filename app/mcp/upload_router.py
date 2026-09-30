"""Oeffentlicher, ausschliesslich Bearer-authentifizierter MCP-Upload-Endpunkt."""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import limiter
from app.db import get_db
from app.services.mcp_upload_service import MCPUploadFehler, speichere_upload

router = APIRouter()


@router.post("/api/mcp/uploads/{upload_id}")
@(limiter.limit(settings.MCP_UPLOAD_RATELIMIT) if limiter else lambda f: f)
async def upload(
    request: Request,
    upload_id: str,
    datei: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer-Upload-Token erforderlich.")
    try:
        row = speichere_upload(db, upload_id, token, datei.file)
    except MCPUploadFehler as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        await datei.close()
    return {
        "upload_id": row.upload_id,
        "sha256": row.sha256,
        "groesse_bytes": row.groesse_bytes,
        "seitenzahl": row.seitenzahl,
    }
