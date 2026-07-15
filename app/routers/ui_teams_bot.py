"""Teams-Alarmierung-Admin-UI: /admin/teams-alarmierung – Org-Admin konfiguriert die
Teams-Alarmierung (Basis-Webhook + optionale Bot-Erweiterung).

Muster: ui_lis.py (Config-Tabelle 1:1 je Org, Fernet-verschlüsseltes Secret,
"secret_changed=1"-Idiom, "Test senden"-Button).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.audit import write_audit
from app.core.crypto import encrypt_secret
from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import AlarmType, FireDept
from app.models.teams_bot import TeamsAlarmConfig, TeamsChannelBinding
from app.models.user import User

router = APIRouter(prefix="/admin")


def _get_org_id(user: User, target_org_id: int | None = None) -> int | None:
    if has_role(user, "system_admin") and target_org_id:
        return target_org_id
    return user.org_id


def _get_or_create_config(db, org_id: int) -> TeamsAlarmConfig:
    cfg = db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == org_id).first()
    if not cfg:
        cfg = TeamsAlarmConfig(
            org_id=org_id,
            enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


# ── GET /admin/teams-alarmierung ─────────────────────────────────────────────
@router.get("/teams-alarmierung", response_class=HTMLResponse)
def teams_alarm_settings_page(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = _get_org_id(user, org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    config = (
        db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    bindings = (
        db.query(TeamsChannelBinding).filter(TeamsChannelBinding.org_id == effective_org_id).all()
        if effective_org_id else []
    )
    alarm_types = (
        db.query(AlarmType).filter(AlarmType.org_id == effective_org_id)
        .order_by(AlarmType.category, AlarmType.code).all()
        if effective_org_id else []
    )

    return templates.TemplateResponse(request, "admin/settings_teams_bot.html", {
        "user": user,
        "org": org,
        "config": config,
        "bindings": bindings,
        "alarm_types": alarm_types,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "flash": request.query_params.get("flash"),
    })


# ── POST /admin/teams-alarmierung/save ───────────────────────────────────────
@router.post("/teams-alarmierung/save")
async def teams_alarm_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    send_exercise: str = Form(""),
    include_map: str = Form(""),
    include_gmaps_link: str = Form(""),
    include_qr_link: str = Form(""),
    include_board_link: str = Form(""),
    webhook_url_alarm: str = Form(""),
    webhook_url_uebung: str = Form(""),
    bot_enabled: str = Form(""),
    bot_app_id: str = Form(""),
    bot_tenant_id: str = Form(""),
    bot_client_secret: str = Form(""),
    secret_changed: str = Form(""),   # "1" = neues Secret vorhanden
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/teams-alarmierung?flash=error_no_org", status_code=302)

    cfg = _get_or_create_config(db, effective_org_id)

    cfg.enabled = enabled == "1"
    cfg.send_exercise = send_exercise == "1"
    cfg.include_map = include_map == "1"
    cfg.include_gmaps_link = include_gmaps_link == "1"
    cfg.include_qr_link = include_qr_link == "1"
    cfg.include_board_link = include_board_link == "1"
    cfg.webhook_url_alarm = webhook_url_alarm.strip() or None
    cfg.webhook_url_uebung = webhook_url_uebung.strip() or None
    cfg.bot_enabled = bot_enabled == "1"
    cfg.bot_app_id = bot_app_id.strip() or None
    cfg.bot_tenant_id = bot_tenant_id.strip() or None
    cfg.updated_at = datetime.now(UTC)

    # Secret nur ersetzen, wenn secret_changed=1 explizit gesetzt ist (analog SSO/LIS)
    if secret_changed == "1":
        raw_secret = bot_client_secret.strip()
        if raw_secret:
            cfg.bot_client_secret_enc = encrypt_secret(raw_secret)
            write_audit(db, "teams_alarm.config.bot_secret_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "teams_alarm.config.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    if effective_org_id != user.org_id:
        redirect_url = f"/admin/teams-alarmierung?org_id={effective_org_id}&flash=saved"
    else:
        redirect_url = "/admin/teams-alarmierung?flash=saved"
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/teams-alarmierung/stichworte ─────────────────────────────────
# Einfacher als bei SMS-Einsatzinfo: kein Verteiler/Vorlagen-Override je Stichwort,
# nur ein An/Aus je Alarmtyp — alle Zeilen der Org werden in einem Formular gesammelt
# gespeichert (angehakt = aktiv, sonst deaktiviert).
@router.post("/teams-alarmierung/stichworte")
async def teams_alarm_settings_save_stichworte(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/teams-alarmierung?flash=error_no_org", status_code=302)

    form = await request.form()
    # Checkbox-Werte, nie Datei-Uploads -- isinstance-Guard narrowt str | UploadFile auf str.
    enabled_ids = {
        int(v) for k, v in form.multi_items() if k == "alarm_type_id" and isinstance(v, str)
    }

    alarm_types = db.query(AlarmType).filter(AlarmType.org_id == effective_org_id).all()
    for at in alarm_types:
        at.teams_alarm_enabled = at.id in enabled_ids

    write_audit(db, "teams_alarm.stichworte_updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    if effective_org_id != user.org_id:
        redirect_url = f"/admin/teams-alarmierung?org_id={effective_org_id}&flash=saved"
    else:
        redirect_url = "/admin/teams-alarmierung?flash=saved"
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/teams-alarmierung/kanalbindung/{binding_id} ──────────────────
# Ordnet eine per Bot eingefangene Kanalbindung einem Ziel zu (Echtalarm/Übung).
@router.post("/teams-alarmierung/kanalbindung/{binding_id}/loeschen")
async def teams_alarm_binding_delete(
    binding_id: int,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    binding = db.get(TeamsChannelBinding, binding_id)
    if binding and binding.org_id == effective_org_id:
        db.delete(binding)
        write_audit(db, "teams_alarm.binding.deleted", org_id=effective_org_id, user_id=user.id,
                    payload={"target": binding.target})
        db.commit()
    redirect_url = (
        f"/admin/teams-alarmierung?org_id={effective_org_id}&flash=saved"
        if effective_org_id != user.org_id else "/admin/teams-alarmierung?flash=saved"
    )
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/teams-alarmierung/test ───────────────────────────────────────
@router.post("/teams-alarmierung/test")
async def teams_alarm_test_send(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    target: str = Form("alarm"),  # "alarm" | "uebung"
):
    effective_org_id = _get_org_id(user, target_org_id)
    cfg = (
        db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    if not cfg or not cfg.enabled:
        return JSONResponse({"ok": False, "message": "Teams-Einsatzinfo ist deaktiviert."})

    webhook_url = cfg.webhook_url_uebung if target == "uebung" else cfg.webhook_url_alarm
    if not webhook_url:
        return JSONResponse({"ok": False, "message": f"Kein Webhook für Ziel '{target}' konfiguriert."})

    from app.services.teams_service import post_teams_karte
    ok = await post_teams_karte(
        webhook_url,
        "🚒 Test-Alarm – Einsatzcockpit",
        "Dies ist eine Testkarte der Teams-Einsatzinfo. Wenn du das hier siehst, "
        "funktioniert der Webhook für dieses Ziel.",
    )
    write_audit(db, "teams_alarm.config.test", org_id=effective_org_id, user_id=user.id,
                payload={"ok": ok, "target": target})
    db.commit()
    if ok:
        return JSONResponse({"ok": True, "message": "Testkarte gesendet — bitte im Teams-Kanal prüfen."})
    return JSONResponse({"ok": False, "message": "Senden fehlgeschlagen — Webhook-URL prüfen."})
