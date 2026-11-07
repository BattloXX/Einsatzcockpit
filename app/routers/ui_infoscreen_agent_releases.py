"""Plattformweite Releaseverwaltung als Tab von Updates & Backups."""
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenRelease, InfoscreenReleaseUploadToken
from app.models.user import User
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_releases as releases

router = APIRouter(prefix="/admin/agent-releases", tags=["infoscreen-agent-admin"])


def render(request, db, user, token=""):
    try:
        fingerprints = {key_id: releases.fingerprint(key) for key_id, key in releases.public_keys().items()}
    except ValueError:
        fingerprints = {"Konfigurationsfehler": "release_key_invalid"}
    return templates.TemplateResponse(request,
        "infoscreen_agent/_releases.html" if request.headers.get("HX-Request") == "true"
        else "infoscreen_agent/releases.html",
        {"user": user, "releases": db.query(InfoscreenRelease).order_by(InfoscreenRelease.id.desc()).all(),
         "tokens": db.query(InfoscreenReleaseUploadToken).order_by(InfoscreenReleaseUploadToken.id.desc()).all(),
         "fingerprints": fingerprints, "new_token": token}, headers={"Cache-Control": "no-store"})


@router.get("", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("system_admin"))):
    return render(request, db, user)


@router.post("/{release_id}/policy", response_class=HTMLResponse)
def policy(release_id: int, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db),
           user: User = Depends(require_role("system_admin"))):
    row = db.get(InfoscreenRelease, release_id)
    if row is None:
        raise HTTPException(404)
    form = run_async(request.form)
    if form.get("rollout_percent") not in ("0", "10", "50", "100"):
        raise HTTPException(422, "Rollout muss 0, 10, 50 oder 100 Prozent sein")
    row.rollout_percent = int(str(form["rollout_percent"]))
    row.blocked = form.get("blocked") == "1"
    row.critical = form.get("critical") == "1"
    write_audit(db, "infoscreen_agent.release_policy", user_id=user.id, entity_type="infoscreen_release",
                entity_id=row.id, payload={"rollout_percent": row.rollout_percent, "blocked": row.blocked,
                                           "critical": row.critical})
    set_tenant_context(db, None)
    targets = db.query(InfoscreenDevice).filter(InfoscreenDevice.channel == row.channel,
        InfoscreenDevice.status == "active", InfoscreenDevice.org_id.is_not(None)).all()
    db.commit()
    for device in targets:
        assert device.org_id is not None
        tasks.add_task(events.publish_update_available, device.org_id, device.id)
    return render(request, db, user)


@router.post("/tokens/create", response_class=HTMLResponse)
def token_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("system_admin"))):
    token = releases.create_upload_token(db)
    write_audit(db, "infoscreen_agent.upload_token_created", user_id=user.id)
    db.commit()
    return render(request, db, user, token)


@router.post("/tokens/{token_id}/revoke", response_class=HTMLResponse)
def token_revoke(token_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("system_admin"))):
    row = db.get(InfoscreenReleaseUploadToken, token_id)
    if row is None:
        raise HTTPException(404)
    row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    write_audit(db, "infoscreen_agent.upload_token_revoked", user_id=user.id, payload={"token_id": row.id})
    db.commit()
    return render(request, db, user)
