"""Plattformweite Releaseverwaltung als Tab von Updates & Backups."""
import re
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
from app.models.infoscreen_agent import (
    InfoscreenDevice,
    InfoscreenRelease,
    InfoscreenReleaseKey,
    InfoscreenReleaseUploadToken,
)
from app.models.user import User
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_github_import as github
from app.services import infoscreen_agent_releases as releases

router = APIRouter(prefix="/admin/agent-releases", tags=["infoscreen-agent-admin"])


def render(request, db, user, token="", *, preview=None, message=""):
    status = github.sync_status(db)
    last_sync = datetime.fromisoformat(status["last_sync"]) if status.get("last_sync") else None
    try:
        fingerprints = {key_id: releases.fingerprint(key) for key_id, key in releases.public_keys(db).items()}
    except ValueError:
        fingerprints = {"Konfigurationsfehler": "release_key_invalid"}
    return templates.TemplateResponse(request,
        "infoscreen_agent/_releases.html" if request.headers.get("HX-Request") == "true"
        else "infoscreen_agent/releases.html",
        {"user": user, "releases": db.query(InfoscreenRelease).order_by(InfoscreenRelease.id.desc()).all(),
         "tokens": db.query(InfoscreenReleaseUploadToken).order_by(InfoscreenReleaseUploadToken.id.desc()).all(),
         "fingerprints": fingerprints, "new_token": token, "key_preview": preview, "key_message": message,
         "keys": db.query(InfoscreenReleaseKey).order_by(InfoscreenReleaseKey.id).all(),
         "key_users": {u.id: u.display_name for u in db.query(User).execution_options(include_all_tenants=True).all()},
         "github_results": status.get("results", []), "last_sync": last_sync, "repo_url": github.repo_url()},
        headers={"Cache-Control": "no-store"})


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


@router.post("/github/import", response_class=HTMLResponse)
def github_import(request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role("system_admin"))):
    github.import_releases(db)
    return render(request, db, user)


@router.post("/keys/preview", response_class=HTMLResponse)
def key_preview(request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("system_admin"))):
    form = run_async(request.form)
    source = "repo" if form.get("quelle") == "repo" else "manuell"
    try:
        pem = github.repository_key(db) if source == "repo" else str(form.get("pem", ""))
        if len(pem.encode("utf-8")) > github.MAX_KEY:
            raise ValueError("size_limit")
        key = releases.parse_public_key(pem)
        key_id = "release" if source == "repo" else str(form.get("key_id", "release"))
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", key_id):
            raise ValueError("key_id_invalid")
        return render(request, db, user, preview={"pem": pem, "key_id": key_id, "quelle": source,
                                                 "fingerprint": releases.fingerprint(key)})
    except ValueError as exc:
        return render(request, db, user, message=str(exc))


@router.post("/keys/confirm", response_class=HTMLResponse)
def key_confirm(request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("system_admin"))):
    form = run_async(request.form)
    pem, key_id = str(form.get("pem", "")), str(form.get("key_id", ""))
    source = str(form.get("quelle", ""))
    try:
        if len(pem.encode("utf-8")) > github.MAX_KEY or source not in ("repo", "manuell"):
            raise ValueError("release_key_invalid")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", key_id) or (source == "repo" and key_id != "release"):
            raise ValueError("key_id_invalid")
        fp = releases.fingerprint(releases.parse_public_key(pem))
        if form.get("fingerprint") != fp:
            raise ValueError("fingerprint_mismatch")
        if db.query(InfoscreenReleaseKey.id).filter(InfoscreenReleaseKey.key_id == key_id,
                InfoscreenReleaseKey.aktiv.is_(True), InfoscreenReleaseKey.fingerprint != fp).first():
            raise ValueError("active_key_exists")
        row = db.query(InfoscreenReleaseKey).filter_by(key_id=key_id, fingerprint=fp).first()
        if row is None:
            row = InfoscreenReleaseKey(key_id=key_id, pem=pem, fingerprint=fp, quelle=source)
            db.add(row)
        row.aktiv = True
        row.bestaetigt_von = user.id
        row.bestaetigt_am = datetime.now(UTC).replace(tzinfo=None)
        db.flush()
        write_audit(db, "infoscreen_agent.key_confirmed", user_id=user.id,
                    entity_type="infoscreen_release_key", entity_id=row.id,
                    payload={"key_id": key_id, "fingerprint": fp, "quelle": source})
        db.commit()
        return render(request, db, user)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/keys/{key_id}/status", response_class=HTMLResponse)
def key_status(key_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role("system_admin"))):
    row = db.get(InfoscreenReleaseKey, key_id)
    if row is None:
        raise HTTPException(404)
    form = run_async(request.form)
    active = form.get("aktiv") == "1"
    if active and db.query(InfoscreenReleaseKey.id).filter(InfoscreenReleaseKey.key_id == row.key_id,
            InfoscreenReleaseKey.aktiv.is_(True), InfoscreenReleaseKey.id != row.id).first():
        raise HTTPException(422, "active_key_exists")
    row.aktiv = active
    write_audit(db, "infoscreen_agent.key_activated" if active else "infoscreen_agent.key_deactivated",
                user_id=user.id, entity_type="infoscreen_release_key", entity_id=row.id,
                payload={"key_id": row.key_id, "fingerprint": row.fingerprint})
    db.commit()
    return render(request, db, user)
