"""Per-Org Konfig-Backup: JSON-Export und -Import mit Dry-Run-Diff (PR 8)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept, OrgSettings

router = APIRouter(prefix="/admin")

_ORG_FIELDS = [
    "name", "color", "bos", "contact_email", "contact_phone",
    "street", "city", "timezone", "fallback_lat", "fallback_lng", "short_code",
]
_SETTINGS_FIELDS = [
    "primary_color", "footer_text", "mi_auto_adopt",
    "autoclose_enabled", "autoclose_after_hours", "autoclose_grace_minutes",
]


def _build_export(db: Session, org_id: int) -> dict:
    org = db.get(FireDept, org_id)
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return {
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "org_id": org_id,
        "org": {f: getattr(org, f, None) for f in _ORG_FIELDS} if org else {},
        "settings": {f: getattr(org_s, f, None) for f in _SETTINGS_FIELDS} if org_s else {},
    }


def _diff_dicts(current: dict, incoming: dict, allowed_fields: list[str]) -> list[dict]:
    changes = []
    for k in allowed_fields:
        if k not in incoming:
            continue
        v_new = incoming[k]
        v_old = current.get(k)
        if v_old != v_new:
            changes.append({"field": k, "old": v_old, "new": v_new})
    return changes


def _render(request: Request, user, org, is_sysadmin: bool, all_orgs,
            result: str | None = None, diff: dict | None = None,
            pending_import: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/konfig.html", {
        "user": user,
        "org": org,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "import_result": result,
        "diff": diff,
        "pending_import": pending_import,
    })


def _resolve_org(request: Request, user, org_id_param: int | None) -> int | None:
    is_sysadmin = has_role(user, "system_admin")
    return org_id_param if (is_sysadmin and org_id_param) else user.org_id


@router.get("/konfig", response_class=HTMLResponse)
async def konfig_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    user = request.state.user
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = org_id if (is_sysadmin and org_id) else user.org_id
    org = db.get(FireDept, effective_org_id) if effective_org_id else None
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    return _render(request, user, org, is_sysadmin, all_orgs)


@router.get("/konfig/export.json")
async def export_org_config(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    user = request.state.user
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = org_id if (is_sysadmin and org_id) else user.org_id
    if not effective_org_id:
        raise HTTPException(400, "Keine Organisation zugeordnet")
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(403)

    data = _build_export(db, effective_org_id)
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    org_slug = (data["org"].get("name") or str(effective_org_id)).replace(" ", "-").lower()
    filename = f"konfig-{org_slug}-{datetime.now(UTC).strftime('%Y%m%d')}.json"
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/konfig/import", response_class=HTMLResponse)
async def import_org_config(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    konfig_file: UploadFile = File(...),
    dry_run: str = Form(""),
    target_org_id: int | None = Form(None),
):
    user = request.state.user
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        raise HTTPException(400, "Keine Organisation zugeordnet")
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(403)

    org = db.get(FireDept, effective_org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    try:
        raw = await konfig_file.read()
        incoming = json.loads(raw)
    except Exception as exc:
        return _render(request, user, org, is_sysadmin, all_orgs,
                       result=f"Fehler: Ungültige JSON-Datei – {exc}")

    if incoming.get("version") != 1:
        return _render(request, user, org, is_sysadmin, all_orgs,
                       result="Fehler: Unbekannte Konfig-Version (erwartet: 1)")

    current = _build_export(db, effective_org_id)
    diff_org = _diff_dicts(current["org"], incoming.get("org", {}), _ORG_FIELDS)
    diff_settings = _diff_dicts(current["settings"], incoming.get("settings", {}), _SETTINGS_FIELDS)
    diff = {"org": diff_org, "settings": diff_settings}

    if dry_run == "1":
        n = len(diff_org) + len(diff_settings)
        result = f"Vorschau: {n} Änderung(en) gefunden." if n else "Keine Änderungen."
        return _render(request, user, org, is_sysadmin, all_orgs,
                       result=result, diff=diff, pending_import=json.dumps(incoming))

    # Apply directly (dry_run not set)
    if org and diff_org:
        for change in diff_org:
            setattr(org, change["field"], change["new"])

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if diff_settings:
        if not org_s:
            org_s = OrgSettings(org_id=effective_org_id)
            db.add(org_s)
        for change in diff_settings:
            setattr(org_s, change["field"], change["new"])

    db.commit()
    n = len(diff_org) + len(diff_settings)
    return _render(request, user, org, is_sysadmin, all_orgs,
                   result=f"Import abgeschlossen: {n} Feld(er) aktualisiert.")


@router.post("/konfig/import-apply", response_class=HTMLResponse)
async def apply_pending_import(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
    konfig_json: str = Form(...),
    target_org_id: int | None = Form(None),
):
    user = request.state.user
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        raise HTTPException(400, "Keine Organisation zugeordnet")
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(403)

    org = db.get(FireDept, effective_org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    try:
        incoming = json.loads(konfig_json)
    except Exception as exc:
        return _render(request, user, org, is_sysadmin, all_orgs,
                       result=f"Fehler: Ungültige Daten – {exc}")

    current = _build_export(db, effective_org_id)
    diff_org = _diff_dicts(current["org"], incoming.get("org", {}), _ORG_FIELDS)
    diff_settings = _diff_dicts(current["settings"], incoming.get("settings", {}), _SETTINGS_FIELDS)

    if org and diff_org:
        for change in diff_org:
            setattr(org, change["field"], change["new"])

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if diff_settings:
        if not org_s:
            org_s = OrgSettings(org_id=effective_org_id)
            db.add(org_s)
        for change in diff_settings:
            setattr(org_s, change["field"], change["new"])

    db.commit()
    n = len(diff_org) + len(diff_settings)
    return _render(request, user, org, is_sysadmin, all_orgs,
                   result=f"Import angewendet: {n} Feld(er) aktualisiert.")
