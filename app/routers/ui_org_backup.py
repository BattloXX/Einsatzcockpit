"""Self-Service-Backup je Organisation (PR 2): Download des Org-Datenarchivs.

`/admin/org-backup`: Der Org-Admin laedt sein tenant-gescoptes Voll-Archiv (ZIP)
herunter. Ein System-Admin kann per org_id-Param eine Org waehlen (wie ui_backup).
Das Remote-Ziel + die Zeitsteuerung kommen in PR 3, der Restore (Sysadmin) in PR 4.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.core.crypto import encrypt_secret
from app.core.permissions import (
    is_system_admin,
    require_role,
    require_system_admin,
    same_org_or_system_admin,
)
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.master import FireDept
from app.models.org_backup import OrgBackupConfig

logger = logging.getLogger("einsatzleiter.org_backup")

router = APIRouter(prefix="/admin")

_PROTOKOLLE = ("sftp", "scp", "rsync", "ftp", "ftps", "rclone")


def _lade_config(db: Session, org_id: int) -> OrgBackupConfig | None:
    return (db.query(OrgBackupConfig)
            .filter(OrgBackupConfig.org_id == org_id)
            .execution_options(include_all_tenants=True).first())


def _effective_org_id(user, org_id_param: int | None) -> int | None:
    # Nur echte System-Admins duerfen eine Org waehlen; org_admin/admin sind an die
    # eigene Org gebunden (has_role() waere hier zu grob -> siehe permissions.has_role).
    return org_id_param if (is_system_admin(user) and org_id_param) else user.org_id


@router.get("/org-backup", response_class=HTMLResponse)
async def org_backup_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    from app.config import settings
    from app.services.org_export_service import AREA_LABELS, areas_aus_string
    user = request.state.user
    is_sysadmin = is_system_admin(user)
    effective = _effective_org_id(user, org_id)
    org = db.get(FireDept, effective) if effective else None
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    cfg = _lade_config(db, effective) if effective else None
    gewaehlte_areas = areas_aus_string(cfg.include_areas) if cfg else None
    return templates.TemplateResponse(request, "admin/org_backup.html", {
        "user": user,
        "org": org,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "org_backup_enabled": settings.ORG_BACKUP_ENABLED,
        "cfg": cfg,
        "protokolle": _PROTOKOLLE,
        "area_labels": AREA_LABELS,
        "gewaehlte_areas": gewaehlte_areas,   # None = alle
        "flash": request.query_params.get("flash"),
    })


@router.get("/org-backup/export.zip")
async def org_backup_export(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    from app.config import settings
    from app.services.org_export_service import export_org

    if not settings.ORG_BACKUP_ENABLED:
        raise HTTPException(404, "Org-Backup ist deaktiviert")
    user = request.state.user
    effective = _effective_org_id(user, org_id)
    if not effective:
        raise HTTPException(400, "Keine Organisation zugeordnet")
    # Nur die eigene Org (bzw. system_admin org-uebergreifend).
    if not same_org_or_system_admin(user, effective):
        raise HTTPException(403)

    org = db.get(FireDept, effective)
    slug = (org.slug if org and org.slug else str(effective))

    from app.services.org_export_service import areas_aus_string
    cfg = _lade_config(db, effective)
    areas = areas_aus_string(cfg.include_areas) if cfg else None

    tmp = Path(tempfile.mkdtemp(prefix="orgbackup_"))
    set_tenant_context(db, None)  # System-Modus: export_org filtert selbst explizit
    try:
        ziel = export_org(db, effective, tmp, include_media=True, areas=areas)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    stamp = ziel.name.replace(f"org-backup-{effective}-", "")
    return FileResponse(
        ziel,
        media_type="application/zip",
        filename=f"org-backup-{slug}-{stamp}",
        background=BackgroundTask(shutil.rmtree, str(tmp), ignore_errors=True),
    )


def _redirect(user, effective: int, flash: str) -> RedirectResponse:
    ziel = f"/admin/org-backup?flash={flash}"
    if is_system_admin(user):
        ziel = f"/admin/org-backup?org_id={effective}&flash={flash}"
    return RedirectResponse(ziel, status_code=303)


def _guard(user, target_org_id: int | None) -> int:
    effective = _effective_org_id(user, target_org_id)
    if not effective:
        raise HTTPException(400, "Keine Organisation zugeordnet")
    if not same_org_or_system_admin(user, effective):
        raise HTTPException(403)
    return effective


@router.post("/org-backup/save")
async def org_backup_save(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    protocol: str = Form("sftp"),
    host: str = Form(""),
    port: int = Form(0),
    username: str = Form(""),
    password: str = Form(""),
    ssh_key: str = Form(""),
    clear_password: str = Form(""),
    clear_key: str = Form(""),
    remote_path: str = Form(""),
    ssh_strict: str = Form("accept-new"),
    rclone_remote: str = Form(""),
    schedule: str = Form("daily"),
    hour: int = Form(3),
    weekday: int | None = Form(None),
    keep_count: int = Form(7),
    include_media: str = Form(""),
    areas: list[str] = Form(default=[]),
):
    from app.services.org_export_service import AREA_ROOTS
    user = request.state.user
    effective = _guard(user, target_org_id)
    if protocol not in _PROTOKOLLE:
        raise HTTPException(400, "Ungueltiges Protokoll")
    gewaehlt = sorted(a for a in areas if a in AREA_ROOTS)

    cfg = _lade_config(db, effective)
    if cfg is None:
        cfg = OrgBackupConfig(org_id=effective)
        db.add(cfg)
    cfg.enabled = enabled == "1"
    cfg.protocol = protocol
    cfg.host = host.strip() or None
    cfg.port = port or 0
    cfg.username = username.strip() or None
    cfg.remote_path = remote_path.strip() or None
    cfg.ssh_strict = ssh_strict.strip() or "accept-new"
    cfg.rclone_remote = rclone_remote.strip() or None
    cfg.schedule = "weekly" if schedule == "weekly" else "daily"
    cfg.hour = max(0, min(23, hour))
    cfg.weekday = weekday if (cfg.schedule == "weekly" and weekday is not None) else None
    cfg.keep_count = max(1, keep_count)
    cfg.include_media = include_media == "1"
    cfg.include_areas = ",".join(gewaehlt)  # "" = nur Kern; alle gewaehlt = vollstaendig
    # Secrets nur bei neuer Eingabe ueberschreiben; leeres Feld laesst Bestand unberuehrt.
    if password:
        cfg.password_enc = encrypt_secret(password)
    elif clear_password == "1":
        cfg.password_enc = None
    if ssh_key.strip():
        cfg.ssh_key_enc = encrypt_secret(ssh_key.strip())
    elif clear_key == "1":
        cfg.ssh_key_enc = None
    db.commit()
    return _redirect(user, effective, "saved")


@router.post("/org-backup/test")
async def org_backup_test(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    from app.services import remote_backup_service as rbs
    user = request.state.user
    effective = _guard(user, target_org_id)
    cfg = _lade_config(db, effective)
    if cfg is None or not cfg.is_fully_configured:
        return _redirect(user, effective, "test_incomplete")
    with tempfile.TemporaryDirectory(prefix="orgbackup_test_") as td:
        probe = Path(td) / "ec-backup-verbindungstest.txt"
        probe.write_text("Einsatzcockpit Org-Backup Verbindungstest\n", encoding="utf-8")
        try:
            with rbs.org_remote_config(cfg) as remote:
                await asyncio.to_thread(rbs.upload, remote, [probe], Path(td))
            return _redirect(user, effective, "test_ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Org-Backup-Verbindungstest fehlgeschlagen (org %s): %s", effective, exc)
            return _redirect(user, effective, "test_error")


@router.post("/org-backup/run-now")
async def org_backup_run_now(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    from app.config import settings
    from app.services.org_backup_loop import run_org_backup_sync
    if not settings.ORG_BACKUP_ENABLED:
        raise HTTPException(404)
    user = request.state.user
    effective = _guard(user, target_org_id)
    cfg = _lade_config(db, effective)
    if cfg is None or not cfg.is_fully_configured:
        return _redirect(user, effective, "run_incomplete")
    status = await asyncio.to_thread(run_org_backup_sync, cfg.id)
    return _redirect(user, effective, "run_ok" if status == "ok" else "run_error")


# ── Restore (nur system_admin) ────────────────────────────────────────────────

def _unique_slug(db: Session, name: str) -> str:
    basis = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "restore"
    slug, n = basis, 1
    while db.query(FireDept).filter(FireDept.slug == slug).first():
        slug = f"{basis}-{n}"
        n += 1
    return slug


def _restore_in_thread(zip_path: str, org_name: str) -> dict:
    from app.core.tenant import set_tenant_context as _ctx
    from app.db import SessionLocal
    from app.services.org_import_service import import_org
    db = SessionLocal()
    _ctx(db, None)
    try:
        org = FireDept(slug=_unique_slug(db, org_name), name=org_name)
        db.add(org)
        db.flush()
        summary = import_org(db, Path(zip_path), org.id)
        return {**summary, "org_name": org.name, "slug": org.slug}
    finally:
        db.close()


def _restore_inplace_in_thread(zip_path: str, ziel_org_id: int) -> dict:
    from app.core.tenant import set_tenant_context as _ctx
    from app.db import SessionLocal
    from app.services.org_import_service import import_org
    db = SessionLocal()
    _ctx(db, None)
    try:
        org = db.get(FireDept, ziel_org_id)
        summary = import_org(db, Path(zip_path), ziel_org_id, replace=True)
        return {**summary, "org_name": org.name if org else str(ziel_org_id),
                "slug": org.slug if org else ""}
    finally:
        db.close()


@router.get("/org-backup/restore", response_class=HTMLResponse)
async def org_restore_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_system_admin),
):
    all_orgs = db.query(FireDept).order_by(FireDept.name).all()
    return templates.TemplateResponse(request, "admin/org_restore.html", {
        "user": request.state.user, "preview": None, "summary": None, "error": None,
        "all_orgs": all_orgs,
    })


@router.post("/org-backup/restore", response_class=HTMLResponse)
async def org_restore_apply(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_system_admin),
    archiv: UploadFile = File(...),
    modus: str = Form("new"),
    neue_org_name: str = Form(""),
    ziel_org_id: int | None = Form(None),
    confirm: str = Form(""),
    confirm_replace: str = Form(""),
):
    from app.config import settings
    from app.services.org_import_service import read_manifest

    if not settings.ORG_BACKUP_ENABLED:
        raise HTTPException(404)

    rohdaten = await archiv.read()
    if settings.ORG_BACKUP_MAX_BYTES and len(rohdaten) > settings.ORG_BACKUP_MAX_BYTES:
        raise HTTPException(413, "Archiv zu gross")

    tmp = Path(tempfile.mkdtemp(prefix="orgrestore_"))
    zpath = tmp / "archiv.zip"
    zpath.write_bytes(rohdaten)

    def _ctx(**extra):
        base = {"user": request.state.user, "preview": None, "summary": None, "error": None,
                "all_orgs": db.query(FireDept).order_by(FireDept.name).all()}
        return {**base, **extra}

    try:
        manifest = read_manifest(zpath)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        return templates.TemplateResponse(request, "admin/org_restore.html",
                                          _ctx(error="Ungueltiges oder beschaedigtes Archiv."))

    if confirm != "1":
        # Vorschau (Dry-Run): Manifest anzeigen, noch nichts importieren.
        shutil.rmtree(tmp, ignore_errors=True)
        return templates.TemplateResponse(request, "admin/org_restore.html", _ctx(preview=manifest))

    try:
        if modus == "replace":
            if not ziel_org_id or confirm_replace != "1":
                shutil.rmtree(tmp, ignore_errors=True)
                return templates.TemplateResponse(request, "admin/org_restore.html",
                    _ctx(error="Ersetzen erfordert eine Ziel-Organisation UND die Bestaetigung."))
            summary = await asyncio.to_thread(_restore_inplace_in_thread, str(zpath), ziel_org_id)
        else:
            name = neue_org_name.strip() or f"Restore Org {manifest.get('org_id')}"
            summary = await asyncio.to_thread(_restore_in_thread, str(zpath), name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Org-Restore fehlgeschlagen")
        return templates.TemplateResponse(request, "admin/org_restore.html",
                                          _ctx(error=f"Import fehlgeschlagen: {exc}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return templates.TemplateResponse(request, "admin/org_restore.html", _ctx(summary=summary))
