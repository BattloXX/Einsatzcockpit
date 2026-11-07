"""CI-Upload, Installer und Updatevertrag S6; Auth ausschliesslich per Bearer."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.rate_limit import limiter
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDeviceEvent, InfoscreenRelease
from app.routers.infoscreen_agent_api import require_device
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_releases as releases
from app.services import infoscreen_agent_service as devices

router = APIRouter(tags=["infoscreen-agent-releases"])
BASE = "/api/v1/infoscreen-agent"


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or len(token) > 256:
        raise HTTPException(401, "unauthorized")
    return token.strip()


def pairing_device(request: Request, db: Session) -> InfoscreenDevice:
    set_tenant_context(db, None)
    device = devices.peek_pairing_code(db, bearer(request))
    if device is None:
        raise HTTPException(401, "unauthorized")
    set_tenant_context(db, device.org_id)
    return device


@router.post(BASE + "/releases")
@(limiter.limit("10/minute") if limiter else lambda fn: fn)
def upload(request: Request, db: Session = Depends(get_db)):
    set_tenant_context(db, None)
    if releases.resolve_upload_token(db, bearer(request)) is None:
        raise HTTPException(401, "unauthorized")
    form = run_async(request.form, max_files=3, max_fields=3, max_part_size=releases.MAX_SCRIPT)
    try:
        metadata = releases.ReleaseMetadata.model_validate_json(str(form.get("metadata", "")))
        package, script = form.get("package"), form.get("install_sh")
        if not isinstance(package, UploadFile) or not isinstance(script, UploadFile):
            raise ValueError("upload_invalid")
        signature = form.get("signature_install_sh")
        if isinstance(signature, UploadFile):
            signature = signature.file.read(101).decode("ascii").strip()
        if not isinstance(signature, str) or len(signature) > 100:
            raise ValueError("signature_invalid")
        row = releases.store_release(db, metadata, package.file, script.file.read(releases.MAX_SCRIPT + 1), signature)
        write_audit(db, "infoscreen_agent.release_uploaded", entity_type="infoscreen_release", entity_id=row.id)
        db.commit()
        return {"id": row.id}
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "release_duplicate") from exc
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(422, "metadata_invalid") from exc
    except UnicodeError as exc:
        db.rollback()
        raise HTTPException(422, "signature_invalid") from exc
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        raise HTTPException(409 if code == "release_duplicate" else 422, code) from exc
    finally:
        run_async(form.close)


@router.get(BASE + "/download/latest")
@(limiter.limit("10/minute") if limiter else lambda fn: fn)
def latest(request: Request, arch: Literal["amd64", "arm64"], channel: Literal["stable", "beta"] = "stable",
           db: Session = Depends(get_db)):
    pairing_device(request, db)
    # Erstinstallation kennt noch keinen Geraetekanal; --channel wird erst beim Pairing gespeichert.
    row = releases.select_release(db, SimpleNamespace(channel=channel), arch, initial=True)  # type: ignore[arg-type]
    if row is None:
        raise HTTPException(404, "release_missing")
    return releases.manifest(row)


@router.get(BASE + "/download/artifact/{artifact_id}")
@(limiter.limit("10/minute") if limiter else lambda fn: fn)
def artifact(artifact_id: int, request: Request, current: str | None = None, db: Session = Depends(get_db)):
    set_tenant_context(db, None)
    token = bearer(request)
    device = devices.resolve_device_token(db, token)
    initial = device is None
    if device is None:
        device = pairing_device(request, db)
    set_tenant_context(db, device.org_id)
    row = db.query(InfoscreenRelease).filter(InfoscreenRelease.id == artifact_id).first()
    if row is None:
        raise HTTPException(404, "release_missing")
    if initial:
        expected = releases.select_release(db, SimpleNamespace(channel=row.channel), row.arch, initial=True)  # type: ignore[arg-type]
        permitted = expected is not None and expected.id == row.id
    else:
        try:
            reported_current = releases.valid_version(current or device.agent_version or "0")
        except ValueError as exc:
            raise HTTPException(422, "version_invalid") from exc
        permitted = releases.eligible(row, device, current=reported_current)
    if not permitted:
        raise HTTPException(404, "release_missing")
    try:
        path = releases.artifact_path(row.storage_path)
        if path.stat().st_size != row.size:
            raise ValueError("artifact_missing")
    except ValueError as exc:
        raise HTTPException(404, "release_missing") from exc
    # Range-Requests duerfen keine Teilpakete liefern: der Vertrag verlangt exakt size Bytes.
    return FileResponse(path, media_type="application/vnd.debian.binary",
                        headers={"Cache-Control": "no-store", "Content-Length": str(row.size)},
                        filename="ec-infoscreen-agent.deb")


@router.get(BASE + "/update/manifest")
@(limiter.limit("30/minute") if limiter else lambda fn: fn)
def update_manifest(request: Request, arch: Literal["amd64", "arm64"], current: str,
                    db: Session = Depends(get_db), device: InfoscreenDevice = Depends(require_device)):
    try:
        releases.valid_version(current)
    except ValueError as exc:
        raise HTTPException(422, "version_invalid") from exc
    row = releases.select_release(db, device, arch, current=current)
    return {"update_available": row is not None,
            "manifest": releases.manifest(row, update=True, current=current) if row else None,
            **releases.update_policy(db, device)}


UpdateError = Literal["download_failed", "hash_mismatch", "signature_invalid", "arch_mismatch", "package_mismatch",
                      "downgrade_blocked", "install_failed", "health_failed", "rollback_failed", "power_loss_recovered"]


class UpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    status: Literal["installing", "ok", "failed", "rolled_back"]
    error_code: UpdateError | None

    _version = field_validator("version")(releases.valid_version)


@router.post(BASE + "/update/result")
@(limiter.limit("30/minute") if limiter else lambda fn: fn)
def update_result(request: Request, body: UpdateResult, db: Session = Depends(get_db),
                  device: InfoscreenDevice = Depends(require_device)):
    # Nur "ok" bestaetigt eine installierte Version. Failed/rolled_back benennen die Zielversion.
    if body.status == "ok":
        device.agent_version = body.version
    device.update_status, device.update_error_code = body.status, body.error_code
    device.update_reported_at = datetime.now(UTC).replace(tzinfo=None)
    codes = {"status": body.status, "error_code": body.error_code}
    db.add(InfoscreenDeviceEvent(org_id=device.org_id, device_id=device.id, typ="update_result", payload_json=codes))
    write_audit(db, "infoscreen_agent.update_result", org_id=device.org_id,
                entity_type="infoscreen_device", entity_id=device.id, payload=codes)
    db.commit()
    return {}


@router.post(BASE + "/unpair")
def unpair(request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db),
           device: InfoscreenDevice = Depends(require_device)):
    result = db.connection().execute(update(InfoscreenDevice).where(
        InfoscreenDevice.id == device.id, InfoscreenDevice.org_id == device.org_id,
        InfoscreenDevice.device_token_hash == hash_api_key(bearer(request)),
        InfoscreenDevice.status == "active").values(
            status="revoked", device_token_hash=None, pairing_code_hash=None, pairing_expires_at=None,
            agent_connection_id=None, agent_worker_id=None, agent_presence_expires_at=None))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "unauthorized")
    db.refresh(device)
    db.add(InfoscreenDeviceEvent(org_id=device.org_id, device_id=device.id, typ="token_revoked", payload_json={}))
    write_audit(db, "infoscreen_agent.unpaired", org_id=device.org_id,
                entity_type="infoscreen_device", entity_id=device.id)
    db.commit()
    assert device.org_id is not None
    tasks.add_task(events.disconnect_device, device.org_id, device.id)
    return {}


@router.get("/infoscreen-agent/install.sh")
@(limiter.limit("30/minute") if limiter else lambda fn: fn)
def installer(request: Request, db: Session = Depends(get_db)):
    from app.config import settings

    if not settings.INFOSCREEN_AGENT_INSTALLER_ENABLED:
        raise HTTPException(404, "installer_missing")
    row = releases.latest_installer(db)
    if row is None:
        raise HTTPException(404, "installer_missing")
    return Response(releases.read_installer(row), media_type="text/x-shellscript",
                    headers={"Cache-Control": "public, max-age=60"})
