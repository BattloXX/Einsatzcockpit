"""Systemadmin-Übersicht und Retention für serverweite Datenbank-Dumps."""
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import FileResponse

from app.config import settings
from app.core.permissions import require_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.master import SystemSettings
from app.models.user import User
from app.services.backup_service import lade_dump_policy, parse_dump_dateiname

router = APIRouter(prefix="/admin")


def _dump_liste() -> list[dict]:
    verzeichnis = Path(settings.BACKUP_DIR)
    if not verzeichnis.is_dir():
        return []
    dumps = []
    for pfad in verzeichnis.iterdir():
        parsed = parse_dump_dateiname(pfad.name)
        if not parsed or not pfad.is_file() or pfad.is_symlink():
            continue
        label, zeitpunkt = parsed
        dumps.append({
            "name": pfad.name,
            "label": label,
            "zeitpunkt": zeitpunkt,
            "groesse": pfad.stat().st_size,
        })
    return sorted(dumps, key=lambda item: item["zeitpunkt"], reverse=True)


@router.get("/db-backups", response_class=HTMLResponse)
def db_backups(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_system_admin),
):
    retention_days, max_count = lade_dump_policy(db)
    return templates.TemplateResponse(request, "admin/db_backups.html", {
        "user": user,
        "is_sysadmin": True,
        "retention_days": retention_days,
        "max_count": max_count,
        "dumps": _dump_liste(),
        "saved": request.query_params.get("saved"),
    })


@router.post("/db-backups")
def db_backups_speichern(
    retention_days: int = Form(..., ge=1),
    max_count: int = Form(..., ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_system_admin),
):
    for key, value in (
        ("backup_retention_days", retention_days),
        ("backup_max_count", max_count),
    ):
        row = db.get(SystemSettings, key)
        if row is None:
            row = SystemSettings(key=key)
            db.add(row)
        row.value = str(value)
        row.updated_at = datetime.now(UTC)
        row.updated_by_user_id = user.id
    db.commit()
    return RedirectResponse("/admin/db-backups?saved=1", status_code=303)


@router.get("/db-backups/{dateiname}")
def db_backup_download(
    dateiname: str,
    user: User = Depends(require_system_admin),
):
    del user
    erlaubte_namen = {item["name"] for item in _dump_liste()}
    if dateiname not in erlaubte_namen:
        raise HTTPException(status_code=404, detail="DB-Dump nicht gefunden")
    basis = Path(settings.BACKUP_DIR).resolve()
    pfad = (basis / dateiname).resolve()
    if pfad.parent != basis or not pfad.is_file() or pfad.is_symlink():
        raise HTTPException(status_code=404, detail="DB-Dump nicht gefunden")
    return FileResponse(pfad, media_type="application/gzip", filename=dateiname)
