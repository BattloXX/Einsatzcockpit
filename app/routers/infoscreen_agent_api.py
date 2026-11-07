"""Geraete-API ohne User-Session: Code bzw. Bearer beweist die Organisation."""
from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.rate_limit import limiter
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.infoscreen_agent import InfoscreenDevice
from app.services import infoscreen_agent_service as service

router = APIRouter(prefix="/api/v1/infoscreen-agent", tags=["infoscreen-agent"])


def require_device(request: Request, db: Session = Depends(get_db)) -> InfoscreenDevice:
    """Bearer pruefen und Folgeselektionen an die bewiesene Org binden."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    device = service.resolve_device_token(db, token.strip()) if scheme.lower() == "bearer" else None
    if device is None:
        raise HTTPException(401, "Ungueltiges Device-Token", headers={"WWW-Authenticate": "Bearer"})
    assert device.org_id is not None
    set_tenant_context(db, device.org_id)
    return device


@router.post("/pair")
@(limiter.limit("5/minute") if limiter else lambda fn: fn)
def pair(request: Request, response: Response, db: Session = Depends(get_db)):
    """Einmal-Code einloesen; Drossel pro IP gilt auch fuer erfolglose Versuche."""
    set_tenant_context(db, None)
    try:
        data = run_async(request.json)
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Ungueltiges JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON-Objekt erforderlich")
    code = data.get("code") or data.get("pairing_code")
    if not isinstance(code, str) or not code.strip():
        raise HTTPException(400, "code fehlt")
    result = service.pair(db, code)
    if result is None:
        raise HTTPException(401, "Ungueltiger oder abgelaufener Code")
    device, token = result
    service.mark_seen(db, device, ip=request.client.host if request.client else None)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"device_token": token, "device_id": device.id, "name": device.name}
