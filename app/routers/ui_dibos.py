"""DIBOS-EventHub-Admin-UI: /admin/dibos - Org-Admin konfiguriert die Elvis-Anbindung.

Muster: ui_lis.py (Config-Tabelle 1:1 je Org, zwei Fernet-verschlüsselte Secrets,
"secret_changed=1"-Idiom, Verbindungstest, system_admin-only Diagnose-Sektion).
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
from app.models.dibos import OrgDibosConfig
from app.models.master import FireDept
from app.models.user import User

router = APIRouter(prefix="/admin")


def _get_org_id(user: User, target_org_id: int | None = None) -> int | None:
    from app.core.permissions import has_role
    if has_role(user, "system_admin") and target_org_id:
        return target_org_id
    return user.org_id


def _get_or_create_config(db, org_id: int) -> OrgDibosConfig:
    cfg = db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgDibosConfig(
            org_id=org_id,
            enabled=False,
            base_url="https://dibos.lwz-vorarlberg.at/Z_EventHub",
            host="einsatzcockpit",
            ag="FW",
            poll_interval_seconds=20,
            auto_trace_on_event=True,
            auto_trace_duration_minutes=120,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


# ── GET /admin/dibos ────────────────────────────────────────────────────────
@router.get("/dibos", response_class=HTMLResponse)
def dibos_settings_page(
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
        db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )

    traces = []
    trace_retention_days = None
    if is_sysadmin:
        from app.services.dibos.dibos_capture import TRACE_RETENTION_DAYS, list_traces
        trace_retention_days = TRACE_RETENTION_DAYS
        if effective_org_id:
            traces = list_traces(effective_org_id)

    return templates.TemplateResponse(request, "admin/settings_dibos.html", {
        "user": user,
        "org": org,
        "config": config,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "traces": traces,
        "trace_retention_days": trace_retention_days,
        "flash": request.query_params.get("flash"),
        "dibos_globally_enabled": settings.DIBOS_TRACE_ENABLED,
    })


# ── GET /admin/dibos/einsaetze ───────────────────────────────────────────────
# Eigene Infoseite: zeigt je Einsatz, was die DIBOS-Anreicherung (dibos_enrich.py,
# Org-Opt-in enrich_incidents) tatsächlich beigetragen hat — Einsatzcode/Diagnose,
# BMA-Nr., DIBOS-Kommentar, Anzahl übernommener Meldungen. Gleiche Berechtigung
# wie die Einstellungsseite selbst (org_admin sieht nur bereits ohnehin
# zugängliche Einsatzdaten der eigenen Org, keine zusätzliche PII).
@router.get("/dibos/einsaetze", response_class=HTMLResponse)
def dibos_einsaetze_page(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    from sqlalchemy import func, or_

    from app.core.permissions import has_role
    from app.models.incident import Incident
    from app.models.lis import LisSyncedObject

    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = _get_org_id(user, org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    config = (
        db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )

    _EINSATZ_LIMIT = 100
    einsaetze = []
    truncated = False
    if effective_org_id:
        incidents = (
            db.query(Incident)
            .filter(
                Incident.primary_org_id == effective_org_id,
                or_(
                    Incident.dibos_tycod.isnot(None),
                    Incident.dibos_diagnose.isnot(None),
                    Incident.dibos_bma_no.isnot(None),
                    Incident.dibos_event_comment.isnot(None),
                ),
            )
            .execution_options(include_all_tenants=True)
            .order_by(Incident.started_at.desc())
            .limit(_EINSATZ_LIMIT + 1)
            .all()
        )
        truncated = len(incidents) > _EINSATZ_LIMIT
        incidents = incidents[:_EINSATZ_LIMIT]

        incident_ids = [i.id for i in incidents]
        comment_counts: dict[int, int] = {}
        if incident_ids:
            comment_counts = dict(
                db.query(LisSyncedObject.incident_id, func.count(LisSyncedObject.id))
                .filter(
                    LisSyncedObject.obj_type == "dibos_comment",
                    LisSyncedObject.incident_id.in_(incident_ids),
                )
                .group_by(LisSyncedObject.incident_id)
                .all()
            )
        einsaetze = [
            {"incident": i, "dibos_message_count": comment_counts.get(i.id, 0)}
            for i in incidents
        ]

    return templates.TemplateResponse(request, "admin/dibos_einsaetze.html", {
        "user": user,
        "org": org,
        "config": config,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "einsaetze": einsaetze,
        "einsatz_limit": _EINSATZ_LIMIT,
        "truncated": truncated,
    })


# ── POST /admin/dibos/save ──────────────────────────────────────────────────
@router.post("/dibos/save")
async def dibos_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    base_url: str = Form(""),
    host: str = Form("einsatzcockpit"),
    ag: str = Form("FW"),
    poll_interval_seconds: int = Form(20),
    auto_trace_on_event: str = Form(""),
    auto_trace_duration_minutes: int = Form(120),
    enrich_incidents: str = Form(""),
    gateway_user: str = Form(""),
    gateway_password: str = Form(""),
    gateway_secret_changed: str = Form(""),   # "1" = neues Gateway-Passwort vorhanden
    service_user: str = Form(""),
    service_password: str = Form(""),
    service_secret_changed: str = Form(""),   # "1" = neues Service-Passwort vorhanden
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/dibos?flash=error_no_org", status_code=302)

    cfg = _get_or_create_config(db, effective_org_id)

    cfg.enabled = enabled == "1"
    cfg.base_url = base_url.strip().rstrip("/") or None
    cfg.host = host.strip() or "einsatzcockpit"
    cfg.ag = ag.strip() or "FW"
    cfg.poll_interval_seconds = max(10, poll_interval_seconds or 20)
    cfg.auto_trace_on_event = auto_trace_on_event == "1"
    cfg.auto_trace_duration_minutes = max(5, auto_trace_duration_minutes or 120)
    cfg.enrich_incidents = enrich_incidents == "1"
    cfg.gateway_user = gateway_user.strip() or None
    cfg.service_user = service_user.strip() or None
    cfg.updated_at = datetime.now(UTC)

    # Passwörter nur ersetzen, wenn *_secret_changed=1 explizit gesetzt ist (analog LIS/SSO)
    if gateway_secret_changed == "1":
        raw = gateway_password.strip()
        if raw:
            cfg.gateway_password_enc = encrypt_secret(raw)
            write_audit(db, "dibos.config.gateway_credentials_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)
    if service_secret_changed == "1":
        raw = service_password.strip()
        if raw:
            cfg.service_password_enc = encrypt_secret(raw)
            write_audit(db, "dibos.config.service_credentials_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "dibos.config.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    if effective_org_id != user.org_id:
        redirect_url = f"/admin/dibos?org_id={effective_org_id}&flash=saved"
    else:
        redirect_url = "/admin/dibos?flash=saved"
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/dibos/test ───────────────────────────────────────────────────
@router.post("/dibos/test")
async def dibos_test_connection(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    cfg = (
        db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    if not cfg or not cfg.is_fully_configured:
        return JSONResponse({"ok": False, "message": "Konfiguration unvollständig (URL, Gateway-/Servicekonto)."})

    from app.core.crypto import decrypt_secret
    from app.services.dibos.dibos_client import DibosClient

    client = DibosClient(
        cfg.base_url,
        cfg.gateway_user, decrypt_secret(cfg.gateway_password_enc),
        cfg.service_user, decrypt_secret(cfg.service_password_enc),
        host=cfg.host, ag=cfg.ag,
    )
    try:
        ok, message = await client.test_connection()
    finally:
        await client.aclose()

    write_audit(db, "dibos.config.test", org_id=effective_org_id, user_id=user.id, payload={"ok": ok})
    db.commit()
    return JSONResponse({"ok": ok, "message": message})


# ── Diagnose: echte Rohdaten aufzeichnen (NUR system_admin — Personenbezug!) ───
# Siehe app/services/dibos/dibos_capture.py: Zugriff bewusst nicht über require_role
# ("org_admin","admin"), sondern require_system_admin — die Aufzeichnungen
# enthalten Anruferdaten im Klartext und werden lokal gespeichert.

def _traces_response(request: Request, db, user: User, org_id: int | None):
    from app.services.dibos.dibos_capture import TRACE_RETENTION_DAYS, list_traces
    org = db.query(FireDept).filter(FireDept.id == org_id).first() if org_id else None
    return templates.TemplateResponse(request, "admin/_dibos_traces.html", {
        "user": user,
        "org": org,
        "traces": list_traces(org_id) if org_id else [],
        "trace_retention_days": TRACE_RETENTION_DAYS,
    })


@router.get("/dibos/trace/status", response_class=HTMLResponse)
def dibos_trace_status(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    org_id: int | None = None,
):
    return _traces_response(request, db, user, org_id)


@router.post("/dibos/trace/start")
async def dibos_trace_start(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int = Form(...),
    duration_minutes: float = Form(120),
):
    from app.services.dibos.dibos_capture import start_trace_for_org
    duration = max(5.0, min(duration_minutes or 120, 480.0))  # 5 min .. 8 h
    try:
        run_id = await start_trace_for_org(target_org_id, duration)
        write_audit(db, "dibos.trace.started", org_id=target_org_id, user_id=user.id,
                    payload={"run_id": run_id, "duration_minutes": duration},
                    ip=request.client.host if request.client else None)
        db.commit()
    except ValueError:
        pass  # z.B. "läuft bereits" oder unvollständige Config — Liste zeigt aktuellen Stand ohnehin
    return _traces_response(request, db, user, target_org_id)


@router.post("/dibos/trace/{run_id}/cancel")
async def dibos_trace_cancel(
    run_id: str,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int = Form(...),
):
    from app.services.dibos.dibos_capture import cancel_trace
    cancel_trace(target_org_id)
    write_audit(db, "dibos.trace.cancelled", org_id=target_org_id, user_id=user.id,
                payload={"run_id": run_id}, ip=request.client.host if request.client else None)
    db.commit()
    return _traces_response(request, db, user, target_org_id)


@router.post("/dibos/trace/{run_id}/delete")
async def dibos_trace_delete(
    run_id: str,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int = Form(...),
):
    from app.services.dibos.dibos_capture import delete_trace
    delete_trace(target_org_id, run_id)
    write_audit(db, "dibos.trace.deleted", org_id=target_org_id, user_id=user.id,
                payload={"run_id": run_id}, ip=request.client.host if request.client else None)
    db.commit()
    return _traces_response(request, db, user, target_org_id)


@router.get("/dibos/trace/{run_id}/live", response_class=HTMLResponse)
def dibos_trace_live(
    run_id: str,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    target_org_id: int | None = None,
):
    from app.services.dibos.dibos_capture import read_latest
    latest = read_latest(target_org_id, run_id) if target_org_id else None
    return templates.TemplateResponse(request, "admin/_dibos_live.html", {
        "user": user,
        "run_id": run_id,
        "target_org_id": target_org_id,
        "latest": latest,
    })

# Bewusst KEIN HTTP-Download-Endpoint für die Aufzeichnungen: Die Rohdaten
# enthalten personenbezogene Daten (Anrufer) — Abruf ausschließlich über
# direkten Server-/Dateizugriff auf app_storage/dibos_trace/{org_id}/{run_id}/
# durch einen system_admin, nicht über einen Browser-Download.
