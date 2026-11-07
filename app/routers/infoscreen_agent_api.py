"""Geraete-API ohne User-Session: Code bzw. Bearer beweist die Organisation."""
from __future__ import annotations

from json import JSONDecodeError
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator
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
    request.state.infoscreen_device_id = device.id
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
    name, channel = data.get("name"), data.get("channel", "stable")
    if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100
                             or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise HTTPException(400, "name_invalid")
    if channel not in ("stable", "beta"):
        raise HTTPException(400, "channel_invalid")
    result = service.pair(db, code)
    if result is None:
        raise HTTPException(401, "Ungueltiger oder abgelaufener Code")
    device, token = result
    if name is not None:
        device.name = name.strip()
    device.channel = channel
    service.mark_seen(db, device, ip=request.client.host if request.client else None)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"device_token": token, "device_id": device.id, "name": device.name}


def _etag_matches(request: Request, etag: str) -> bool:
    return any(value.strip().removeprefix("W/") in (etag, "*")
               for value in request.headers.get("if-none-match", "").split(","))


@router.get("/state")
@(limiter.limit("120/minute") if limiter else lambda fn: fn)
def state(request: Request, display: str, db: Session = Depends(get_db),
          device: InfoscreenDevice = Depends(require_device)):
    from datetime import UTC, datetime

    from fastapi.responses import JSONResponse

    from app.models.infoscreen_agent import InfoscreenDisplay
    from app.services.infoscreen_agent_snapshot import state as resolve_state

    row = db.query(InfoscreenDisplay).filter(
        InfoscreenDisplay.org_id == device.org_id, InfoscreenDisplay.device_id == device.id,
        InfoscreenDisplay.display_key == display).first()
    if row is None:
        raise HTTPException(404, "Display nicht gefunden")
    data = resolve_state(db, device, row, datetime.now(UTC))
    etag = f'"{data["revision"]}"'
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if _etag_matches(request, etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(data, headers=headers)


@router.get("/config")
@(limiter.limit("60/minute") if limiter else lambda fn: fn)
def config(request: Request, db: Session = Depends(get_db), device: InfoscreenDevice = Depends(require_device)):
    import hashlib
    import json
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    from fastapi.responses import JSONResponse

    from app.config import settings
    from app.models.infoscreen_agent import InfoscreenDisplay, InfoscreenUrlAllowlist
    from app.services.infoscreen_agent_resolver import timeline
    from app.services.infoscreen_agent_snapshot import alarm_url, lade_schedule

    now = datetime.now(UTC)
    # Stundenraster haelt den Snapshot cachebar und garantiert mindestens 72 h.
    anchor = now.replace(minute=0, second=0, microsecond=0)
    displays = db.query(InfoscreenDisplay).filter(
        InfoscreenDisplay.org_id == device.org_id, InfoscreenDisplay.device_id == device.id,
    ).order_by(InfoscreenDisplay.id).all()
    server_host = urlsplit(settings.effective_public_base_url).hostname
    data = {
        "config_version": device.config_version,
        "displays": [{"display_key": d.display_key, "name": d.name, "edid_id": d.edid_id,
                      "aktiv": d.aktiv, "power_mode": d.power_mode, "nachlauf_min": d.nachlauf_min,
                      "offline_wake_after_min": d.offline_wake_after_min,
                      "url": alarm_url(db, d), "timeline": timeline(lade_schedule(db, d), anchor, hours=73)}
                     for d in displays],
        "allowlist": sorted(({server_host} if server_host else set()) | {
            r.host for r in db.query(InfoscreenUrlAllowlist).filter(
                InfoscreenUrlAllowlist.org_id == device.org_id).all()}),
        "intervals": {"poll_sec": 20, "heartbeat_sec": 90, "ping_sec": 25},
        "update_policy": {"enabled": True, "channel": device.channel,
                          "locked": device.update_locked, "pinned_version": device.update_pinned_version},
    }
    etag = '"' + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if _etag_matches(request, etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(data, headers=headers)


DisplayKey = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")]
Revision = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
ErrorCode = Literal["backend_unavailable", "power_failed", "power_unverified", "browser_failed",
                    "page_unreachable", "renderer_failed", "offline", "clock_skew", "update_failed"]


class DisplayHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_key: DisplayKey
    power: Literal["on", "off", "unknown"]
    verified: StrictBool
    backend: Literal["sway", "gnome-mutter", "x11", "cec", "ddc", "logical_blank", "unknown"]
    page_state: Literal["ready", "loading", "failed", "offline", "blank", "unknown"]
    errors: list[ErrorCode] = Field(default_factory=list, max_length=16)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_version: str = Field(min_length=1, max_length=100)
    os_version: str = Field(min_length=1, max_length=100)
    hostname: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.-]+$")
    last_revision: dict[DisplayKey, Revision] = Field(max_length=32)
    config_version: Revision
    displays: list[DisplayHeartbeat] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_displays(self):
        keys = [d.display_key for d in self.displays]
        if len(keys) != len(set(keys)):
            raise ValueError("Doppelte Display-Keys")
        return self


def _device_limit_key(request: Request) -> str:
    # Dependencies authentifizieren vor dem Endpoint-Wrapper; gemeinsame NAT-IP
    # darf mehreren Monitor-PCs nicht das Heartbeat-Kontingent wegnehmen.
    return f"infoscreen:{request.state.infoscreen_device_id}"


@router.post("/heartbeat")
@(limiter.limit("6/minute", key_func=_device_limit_key) if limiter else lambda fn: fn)
def heartbeat(request: Request, body: Heartbeat, db: Session = Depends(get_db),
              device: InfoscreenDevice = Depends(require_device)):
    from app.models.infoscreen_agent import InfoscreenDisplay

    rows = db.query(InfoscreenDisplay).filter(
        InfoscreenDisplay.org_id == device.org_id, InfoscreenDisplay.device_id == device.id).all()
    displays = {d.display_key: d for d in rows}
    if (set(body.last_revision) | {d.display_key for d in body.displays}) - displays.keys():
        raise HTTPException(404, "Display nicht gefunden")
    service.mark_seen(db, device, agent_version=body.agent_version,
                      os_version=body.os_version, hostname=body.hostname)
    device.reported_config_version = body.config_version
    for report in body.displays:
        displays[report.display_key].reported_status = report.model_dump(exclude={"display_key"})
    for key, revision in body.last_revision.items():
        displays[key].last_revision = revision
    db.commit()
    return {"config_version": device.config_version,
            "revisions": {d.display_key: d.revision for d in rows}}
