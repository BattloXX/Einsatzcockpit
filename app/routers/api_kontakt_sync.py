"""Offline synchronization contract for the central contact directory."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_api_key
from app.db import get_db
from app.models.user import ApiKey
from app.services.kontakt_sync_service import delta, snapshot

router = APIRouter(prefix="/api/v1/kontakte", tags=["Kontakte"])


@router.get("/sync")
def sync_kontakte(
    cursor: int | None = Query(None, ge=0),
    page_after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Return a versioned full snapshot or append-only delta for this API key's org."""
    if api_key.org_id is None:
        raise HTTPException(403, "API-Key ist keiner Organisation zugeordnet")
    if cursor is None:
        return snapshot(db, api_key.org_id, page_after, limit)
    return delta(db, api_key.org_id, cursor, limit)
