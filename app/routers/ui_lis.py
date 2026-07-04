"""LIS/IPR-Admin-UI: /admin/lis – Org-Admin konfiguriert die Leitstellen-Anbindung.

Muster: ui_sso.py (Config-Tabelle 1:1 je Org, Fernet-verschlüsseltes Secret,
"secret_changed=1"-Idiom, Verbindungstest).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import encrypt_secret
from app.core.permissions import require_role, require_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.lis import OrgLisConfig
from app.models.master import FireDept
from app.models.user import User

router = APIRouter(prefix="/admin")


def _get_org_id(user: User, target_org_id: int | None = None) -> int | None:
    from app.core.permissions import has_role
    if has_role(user, "system_admin") and target_org_id:
        return target_org_id
    return user.org_id


def _get_or_create_config(db, org_id: int) -> OrgLisConfig:
    cfg = db.query(OrgLisConfig).filter(OrgLisConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgLisConfig(
            org_id=org_id,
            enabled=False,
            site="LIS",
            poll_interval_seconds=30,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


# ── GET /admin/lis ─────────────────────────────────────────────────────────
@router.get("/lis", response_class=HTMLResponse)
def lis_settings_page(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    from app.core.permissions import has_role
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = _get_org_id(user, org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    config = (
        db.query(OrgLisConfig).filter(OrgLisConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )

    captures = []
    capture_retention_days = None
    if is_sysadmin:
        from app.services.lis.lis_capture import CAPTURE_RETENTION_DAYS, list_captures
        capture_retention_days = CAPTURE_RETENTION_DAYS
        if effective_org_id:
            captures = list_captures(effective_org_id)

    return templates.TemplateResponse(request, "admin/settings_lis.html", {
        "user": user,
        "org": org,
        "config": config,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "captures": captures,
        "capture_retention_days": capture_retention_days,
        "flash": request.query_params.get("flash"),
        "lis_globally_enabled": settings.LIS_ENABLED,
    })


# ── POST /admin/lis/save ──────────────────────────────────────────────────────
@router.post("/lis/save")
async def lis_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    base_url: str = Form(""),
    site: str = Form("LIS"),
    organization_id: str = Form(""),
    poll_interval_seconds: int = Form(30),
    username: str = Form(""),
    password: str = Form(""),
    secret_changed: str = Form(""),   # "1" = neues Passwort vorhanden
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/lis?flash=error_no_org", status_code=302)

    cfg = _get_or_create_config(db, effective_org_id)

    cfg.enabled = enabled == "1"
    cfg.base_url = base_url.strip().rstrip("/") or None
    cfg.site = site.strip() or "LIS"
    cfg.organization_id = organization_id.strip() or None
    cfg.poll_interval_seconds = max(10, poll_interval_seconds or 30)
    cfg.username = username.strip() or None
    cfg.updated_at = datetime.now(UTC)

    # Passwort nur ersetzen, wenn secret_changed=1 explizit gesetzt ist (analog SSO F-02)
    if secret_changed == "1":
        raw_password = password.strip()
        if raw_password:
            cfg.password_enc = encrypt_secret(raw_password)
            write_audit(db, "lis.config.credentials_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "lis.config.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    if effective_org_id != user.org_id:
        redirect_url = f"/admin/lis?org_id={effective_org_id}&flash=saved"
    else:
        redirect_url = "/admin/lis?flash=saved"
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/lis/test ──────────────────────────────────────────────────────
@router.post("/lis/test")
async def lis_test_connection(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    cfg = db.query(OrgLisConfig).filter(OrgLisConfig.org_id == effective_org_id).first() if effective_org_id else None
    if not cfg or not cfg.is_fully_configured:
        return JSONResponse({"ok": False, "message": "Konfiguration unvollständig (URL, Organisation, Zugangsdaten)."})

    from app.core.crypto import decrypt_secret
    from app.services.lis.lis_client import LisAuthError, LisClientError, LisClient

    try:
        password = decrypt_secret(cfg.password_enc)
        client = LisClient(cfg.base_url, cfg.site, cfg.username, password)
        await client.login()
        operations = await client.get_operations_in_range(cfg.organization_id, operation_filter="ActiveParticipation")
        write_audit(db, "lis.config.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": True, "active_operations": len(operations)})
        db.commit()
        return JSONResponse({
            "ok": True,
            "message": f"Verbindung erfolgreich. {len(operations)} aktive(r) Einsatz/Einsätze im LIS gefunden.",
        })
    except LisAuthError as exc:
        return JSONResponse({"ok": False, "message": f"Login fehlgeschlagen: {exc}"})
    except LisClientError as exc:
        return JSONResponse({"ok": False, "message": f"Fehler: {exc}"})
    except Exception as exc:  # noqa: BLE001 – Testergebnis soll nie 500 werfen
        return JSONResponse({"ok": False, "message": f"Unerwarteter Fehler: {exc}"})


# ── Diagnose: echte Rohdaten aufzeichnen (NUR system_admin — Personenbezug!) ───
# Siehe app/services/lis/lis_capture.py: Zugriff bewusst nicht über require_role
# ("org_admin","admin"), sondern require_system_admin — die Aufzeichnungen
# enthalten Anrufer-/Mannschaftsdaten im Klartext und werden lokal gespeichert.

def _captures_response(request: Request, db, user: User, org_id: int | None):
    from app.services.lis.lis_capture import CAPTURE_RETENTION_DAYS, list_captures
    org = db.query(FireDept).filter(FireDept.id == org_id).first() if org_id else None
    return templates.TemplateResponse(request, "admin/_lis_captures.html", {
        "user": user,
        "org": org,
        "captures": list_captures(org_id) if org_id else [],
        "capture_retention_days": CAPTURE_RETENTION_DAYS,
    })


@router.get("/lis/capture/status", response_class=HTMLResponse)
def lis_capture_status(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    org_id: int | None = None,
):
    return _captures_response(request, db, user, org_id)


@router.post("/lis/capture/start")
async def lis_capture_start(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int = Form(...),
    duration_minutes: float = Form(120),
):
    from app.services.lis.lis_capture import start_capture_for_org
    duration = max(5.0, min(duration_minutes or 120, 480.0))  # 5 min .. 8 h
    try:
        run_id = await start_capture_for_org(target_org_id, duration)
        write_audit(db, "lis.capture.started", org_id=target_org_id, user_id=user.id,
                    payload={"run_id": run_id, "duration_minutes": duration},
                    ip=request.client.host if request.client else None)
        db.commit()
    except ValueError:
        pass  # z.B. "läuft bereits" oder unvollständige Config — Liste zeigt aktuellen Stand ohnehin
    return _captures_response(request, db, user, target_org_id)


@router.post("/lis/capture/{run_id}/cancel")
async def lis_capture_cancel(
    run_id: str,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int = Form(...),
):
    from app.services.lis.lis_capture import cancel_capture
    cancel_capture(target_org_id)
    write_audit(db, "lis.capture.cancelled", org_id=target_org_id, user_id=user.id,
                payload={"run_id": run_id}, ip=request.client.host if request.client else None)
    db.commit()
    return _captures_response(request, db, user, target_org_id)


@router.post("/lis/capture/{run_id}/delete")
async def lis_capture_delete(
    run_id: str,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int = Form(...),
):
    from app.services.lis.lis_capture import delete_capture
    delete_capture(target_org_id, run_id)
    write_audit(db, "lis.capture.deleted", org_id=target_org_id, user_id=user.id,
                payload={"run_id": run_id}, ip=request.client.host if request.client else None)
    db.commit()
    return _captures_response(request, db, user, target_org_id)

# Bewusst KEIN HTTP-Download-Endpoint für die Aufzeichnungen: Die Rohdaten
# enthalten personenbezogene Daten (Anrufer, Mannschaft) — Abruf ausschließlich
# über direkten Server-/Dateizugriff auf app_storage/lis_capture/{org_id}/{run_id}/
# durch einen system_admin, nicht über einen Browser-Download.
