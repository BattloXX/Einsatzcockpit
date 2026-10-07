"""Oeffentlicher Endpunkt fuer kurzlebige MCP-Dokumentdownloads."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import limiter
from app.db import get_db
from app.services.mcp_download_service import MCPDownloadFehler, lade_download

router = APIRouter()


@router.get("/api/mcp/downloads/{token}")
@(limiter.limit(settings.MCP_DOWNLOAD_RATELIMIT) if limiter else lambda f: f)
def download(request: Request, token: str, db: Session = Depends(get_db)):
    try:
        pfad, dateiname, mime = lade_download(db, token)
    except MCPDownloadFehler as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return FileResponse(
        pfad,
        media_type=mime,
        filename=dateiname,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
