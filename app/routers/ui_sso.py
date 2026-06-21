"""SSO-Admin-UI: /admin/sso – Org-Admin konfiguriert Entra-ID-Verbindung."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import decrypt_secret, encrypt_secret
from app.services.sso_service import validate_authority_base
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept
from app.models.sso import OrgSsoConfig, OrgSsoGroupMap
from app.models.user import Role, User

router = APIRouter(prefix="/admin")

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _get_org_id(user: User, target_org_id: int | None = None) -> int | None:
    from app.core.permissions import has_role
    if has_role(user, "system_admin") and target_org_id:
        return target_org_id
    return user.org_id


def _get_or_create_config(db, org_id: int) -> OrgSsoConfig:
    cfg = db.query(OrgSsoConfig).filter(OrgSsoConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgSsoConfig(
            org_id=org_id,
            enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


# ── GET /admin/sso ────────────────────────────────────────────────────────────

@router.get("/sso", response_class=HTMLResponse)
def sso_settings_page(
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
    config = db.query(OrgSsoConfig).filter(OrgSsoConfig.org_id == effective_org_id).first() if effective_org_id else None

    roles = db.query(Role).filter(Role.code != "system_admin").order_by(Role.label).all()
    redirect_uri = (
        f"{settings.effective_public_base_url}/sso/{org.slug}/callback" if org else ""
    )
    return templates.TemplateResponse(request, "admin/settings_sso.html", {
        "user": user,
        "org": org,
        "config": config,
        "roles": roles,
        "redirect_uri": redirect_uri,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "flash": request.query_params.get("flash"),
        "sso_globally_enabled": settings.SSO_ENABLED,
        "settings_public_base_url": settings.effective_public_base_url,
    })


# ── POST /admin/sso/save ──────────────────────────────────────────────────────

@router.post("/sso/save")
async def sso_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    tenant_id: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    secret_changed: str = Form(""),    # F-02: "1" = neues Secret vorhanden
    authority_base: str = Form(""),
    allowed_domains: str = Form(""),
    default_role_id: str = Form(""),
    deny_if_no_group: str = Form(""),
    sync_profile: str = Form(""),
    enforce_sso: str = Form(""),
    allow_email_linking: str = Form(""),  # F-07
):
    from fastapi.responses import RedirectResponse
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/sso?flash=error_no_org", status_code=302)

    # F-01: authority_base Whitelist-Validierung
    try:
        validated_authority = validate_authority_base(authority_base)
    except ValueError:
        return RedirectResponse("/admin/sso?flash=error_authority", status_code=302)

    cfg = _get_or_create_config(db, effective_org_id)

    tenant_id = tenant_id.strip()
    client_id = client_id.strip()

    cfg.enabled = enabled == "1"
    cfg.tenant_id = tenant_id or None
    cfg.client_id = client_id or None
    cfg.authority_base = validated_authority
    cfg.allowed_domains = allowed_domains.strip() or None

    # F-13: default_role_id darf nicht system_admin sein
    if default_role_id.isdigit():
        role = db.get(Role, int(default_role_id))
        cfg.default_role_id = role.id if role and role.code != "system_admin" else None
    else:
        cfg.default_role_id = None

    cfg.deny_if_no_group = deny_if_no_group == "1"
    cfg.sync_profile = sync_profile == "1"       # F-14: explizites Opt-in
    cfg.enforce_sso = enforce_sso == "1"
    cfg.allow_email_linking = allow_email_linking == "1"  # F-07
    cfg.updated_at = datetime.now(UTC)

    # F-02: Secret nur ersetzen wenn secret_changed=1 explizit gesetzt
    if secret_changed == "1":
        raw_secret = client_secret.strip()
        if raw_secret:
            cfg.client_secret_enc = encrypt_secret(raw_secret)
            write_audit(db, "sso.config.secret_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "sso.config.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    if effective_org_id != user.org_id:
        redirect_url = f"/admin/sso?org_id={effective_org_id}&flash=saved"
    else:
        redirect_url = "/admin/sso?flash=saved"
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/sso/test ──────────────────────────────────────────────────────

@router.post("/sso/test")
async def sso_test_connection(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    cfg = db.query(OrgSsoConfig).filter(OrgSsoConfig.org_id == effective_org_id).first() if effective_org_id else None
    if not cfg or not cfg.tenant_id:
        return JSONResponse({"ok": False, "message": "Keine Konfiguration vorhanden."})

    try:
        import httpx
        from app.services.sso_service import _authority
        authority = _authority(cfg.tenant_id, cfg.authority_base)
        async with httpx.AsyncClient(timeout=settings.SSO_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{authority}/.well-known/openid-configuration")
        if resp.status_code == 200:
            data = resp.json()
            issuer = data.get("issuer", "?")
            return JSONResponse({"ok": True, "message": f"Verbindung erfolgreich. Issuer: {issuer}"})
        return JSONResponse({"ok": False, "message": f"HTTP {resp.status_code} von Microsoft erhalten."})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"Fehler: {exc}"})


# ── POST /admin/sso/group-map/add ─────────────────────────────────────────────

@router.post("/sso/group-map/add")
async def sso_group_map_add(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    entra_group_id: str = Form(""),
    label: str = Form(""),
    role_id: int = Form(...),
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return HTMLResponse("<p class='text-error'>Keine Organisation.</p>", status_code=400)

    entra_group_id = entra_group_id.strip()
    if not _GUID_RE.match(entra_group_id):
        return HTMLResponse("<p class='text-error'>Ungültige Group Object ID (kein GUID-Format).</p>", status_code=400)

    cfg = _get_or_create_config(db, effective_org_id)

    mapping = OrgSsoGroupMap(
        config_id=cfg.id,
        entra_group_id=entra_group_id,
        label=label.strip() or None,
        role_id=role_id,
    )
    db.add(mapping)
    write_audit(db, "sso.groupmap.added", org_id=effective_org_id, user_id=user.id,
                payload={"group_id": entra_group_id, "role_id": role_id})
    db.commit()

    return await _group_map_table(db, cfg.id, effective_org_id, request, user)


# ── POST /admin/sso/group-map/{map_id}/delete ─────────────────────────────────

@router.post("/sso/group-map/{map_id}/delete")
async def sso_group_map_delete(
    map_id: int,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    mapping = db.get(OrgSsoGroupMap, map_id)
    if not mapping:
        return HTMLResponse("", status_code=200)
    cfg = db.get(OrgSsoConfig, mapping.config_id)
    if not cfg or cfg.org_id != effective_org_id:
        return HTMLResponse("", status_code=403)

    write_audit(db, "sso.groupmap.removed", org_id=effective_org_id, user_id=user.id,
                payload={"group_id": mapping.entra_group_id, "role_id": mapping.role_id})
    db.delete(mapping)
    db.commit()

    return await _group_map_table(db, cfg.id, effective_org_id, request, user)


async def _group_map_table(db, config_id: int, org_id: int, request: Request, user: User) -> HTMLResponse:
    cfg = db.get(OrgSsoConfig, config_id)
    roles = db.query(Role).filter(Role.code != "system_admin").order_by(Role.label).all()
    return templates.TemplateResponse(request, "admin/_sso_group_map.html", {
        "user": user,
        "config": cfg,
        "roles": roles,
        "org_id": org_id,
    })
