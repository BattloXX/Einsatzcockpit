"""Self-Service-Backup je Organisation (PR 2): Download des Org-Datenarchivs.

`/admin/org-backup`: Der Org-Admin laedt sein tenant-gescoptes Voll-Archiv (ZIP)
herunter. Ein System-Admin kann per org_id-Param eine Org waehlen (wie ui_backup).
Das Remote-Ziel + die Zeitsteuerung kommen in PR 3, der Restore (Sysadmin) in PR 4.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.core.permissions import is_system_admin, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.master import FireDept

router = APIRouter(prefix="/admin")


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
    user = request.state.user
    is_sysadmin = is_system_admin(user)
    effective = _effective_org_id(user, org_id)
    org = db.get(FireDept, effective) if effective else None
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    return templates.TemplateResponse(request, "admin/org_backup.html", {
        "user": user,
        "org": org,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "org_backup_enabled": settings.ORG_BACKUP_ENABLED,
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

    tmp = Path(tempfile.mkdtemp(prefix="orgbackup_"))
    set_tenant_context(db, None)  # System-Modus: export_org filtert selbst explizit
    try:
        ziel = export_org(db, effective, tmp, include_media=True)
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
