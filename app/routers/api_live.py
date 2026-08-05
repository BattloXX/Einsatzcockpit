"""Live-Einsatzstatus fuer Browser- und PWA-Nutzer."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.einsatz_live_service import build_live_state

router = APIRouter(prefix="/api/v1/live", tags=["live"])


@router.get("/state")
def get_live_state(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")
    incident, count = build_live_state(db, user, None)
    return JSONResponse(
        {
            "server_time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "incident_count": count,
            "incident": incident,
        },
        headers={"Cache-Control": "no-store"},
    )
