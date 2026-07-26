"""BMA-Webplattform-Import (Landeswarnzentrale Vorarlberg):
- /admin/bma-import: Org-Admin hinterlegt das Session-Cookie, testet die
  Verbindung, stoesst einen manuellen Lauf an (Muster: ui_dibos.py).
- /objekte/bma-import: Review-Queue fuer objekt_verwalter (offene Vorschlaege,
  nicht zugeordnete Anlagen, verschwundene Anlagen) - Muster fuer die
  Mischung aus /objekte/... und /admin/...-Pfaden in einem Router:
  ui_objekt_dokumente.py.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.crypto import encrypt_secret
from app.core.permissions import (
    is_system_admin,
    require_role,
    same_org_or_system_admin,
)
from app.core.templating import templates
from app.db import get_db
from app.models.bma_import import BMA_SATZ_VERSCHWUNDEN, BmaImportLauf, BmaImportSatz, OrgBmaImportConfig
from app.models.master import FireDept
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG, Objekt
from app.models.user import User
from app.routers.ui_objekt import require_objekt_enabled

router = APIRouter(tags=["bma-import"])


# ── Admin-Konfiguration (Muster: ui_dibos.py) ────────────────────────────────

def _get_org_id(user: User, target_org_id: int | None = None) -> int | None:
    if is_system_admin(user) and target_org_id:
        return target_org_id
    return user.org_id


def _get_or_create_config(db: Session, org_id: int) -> OrgBmaImportConfig:
    cfg = db.query(OrgBmaImportConfig).filter(OrgBmaImportConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgBmaImportConfig(
            org_id=org_id,
            enabled=False,
            base_url="https://dibos.lwz-vorarlberg.at/LWZ_BMA_Webplattform",
            sync_stunde=3,
            sync_minute=30,
            auto_anlegen=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


@router.get("/admin/bma-import", response_class=HTMLResponse)
def bma_import_settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    from app.config import settings

    is_sysadmin = is_system_admin(user)
    effective_org_id = _get_org_id(user, org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    config = (
        db.query(OrgBmaImportConfig).filter(OrgBmaImportConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    laeufe = (
        db.query(BmaImportLauf)
        .filter(BmaImportLauf.org_id == effective_org_id)
        .execution_options(include_all_tenants=True)
        .order_by(BmaImportLauf.gestartet_am.desc())
        .limit(20)
        .all()
        if effective_org_id else []
    )

    return templates.TemplateResponse(request, "admin/settings_bma_import.html", {
        "user": user,
        "org": org,
        "config": config,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "laeufe": laeufe,
        "flash": request.query_params.get("flash"),
        "bma_import_globally_enabled": settings.BMA_IMPORT_ENABLED,
    })


@router.post("/admin/bma-import/save")
async def bma_import_settings_save(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    base_url: str = Form(""),
    sync_stunde: int = Form(3),
    sync_minute: int = Form(30),
    auto_anlegen: str = Form(""),
    import_rechnungsadresse: str = Form(""),
    keepalive_aktiv: str = Form(""),
    session_cookie: str = Form(""),
    session_secret_changed: str = Form(""),  # "1" = neues Cookie eingegeben
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/bma-import?flash=error_no_org", status_code=302)
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")

    cfg = _get_or_create_config(db, effective_org_id)
    cfg.enabled = enabled == "1"
    cfg.base_url = base_url.strip().rstrip("/") or None
    cfg.sync_stunde = max(0, min(sync_stunde, 23))
    cfg.sync_minute = max(0, min(sync_minute, 59))
    cfg.auto_anlegen = auto_anlegen == "1"
    cfg.import_rechnungsadresse = import_rechnungsadresse == "1"
    cfg.keepalive_aktiv = keepalive_aktiv == "1"
    cfg.updated_at = datetime.now(UTC)

    if session_secret_changed == "1":
        raw = session_cookie.strip()
        if raw:
            cfg.session_cookie_enc = encrypt_secret(raw)
            cfg.session_gesetzt_am = datetime.now(UTC)
            write_audit(db, "bma_import.config.cookie_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "bma_import.config.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    redirect_url = "/admin/bma-import?flash=saved"
    if effective_org_id != user.org_id:
        redirect_url = f"/admin/bma-import?org_id={effective_org_id}&flash=saved"
    return RedirectResponse(redirect_url, status_code=302)


@router.post("/admin/bma-import/test")
async def bma_import_test_connection(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    effective_org_id = _get_org_id(user, target_org_id)
    if effective_org_id and not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")
    cfg = (
        db.query(OrgBmaImportConfig).filter(OrgBmaImportConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    if not cfg or not cfg.is_fully_configured:
        return JSONResponse({"ok": False, "message": "Konfiguration unvollständig (Basis-URL/Session-Cookie fehlt)."})

    from app.core.crypto import decrypt_secret
    from app.services.bma_import.bma_client import BmaClient

    assert cfg.base_url and cfg.session_cookie_enc
    client = BmaClient(cfg.base_url, decrypt_secret(cfg.session_cookie_enc))
    try:
        ok, message = await client.test_connection()
    finally:
        await client.aclose()

    write_audit(db, "bma_import.config.test", org_id=effective_org_id, user_id=user.id, payload={"ok": ok})
    db.commit()
    return JSONResponse({"ok": ok, "message": message})


@router.post("/admin/bma-import/sync-jetzt", response_class=HTMLResponse)
async def bma_import_sync_jetzt(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    from app.core.tenant import set_tenant_context
    from app.services.bma_import.bma_sync import sync_org_bma

    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewählt")
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first()
    cfg = db.query(OrgBmaImportConfig).filter(OrgBmaImportConfig.org_id == effective_org_id).first()
    if org and cfg:
        # sync_org_bma filtert Tenant-Tabellen selbst explizit auf org.id (siehe
        # Docstring dort) - erfordert deshalb einen neutralen (None) Tenant-Kontext,
        # sonst wuerde der automatische Filter der Session zusaetzlich zuschlagen.
        set_tenant_context(db, None)
        await sync_org_bma(db, org, cfg, ausloeser="manuell")

    return RedirectResponse(
        f"/admin/bma-import?org_id={effective_org_id}&flash=saved"
        if effective_org_id != user.org_id else "/admin/bma-import?flash=saved",
        status_code=302,
    )


# ── Review-Queue (/objekte/bma-import) ───────────────────────────────────────

def _queue_context(request: Request, db: Session, user: User) -> dict:
    from app.services.bma_import.bma_sync import baue_diff

    org_id = user.org_id
    saetze = (
        db.query(BmaImportSatz)
        .filter(BmaImportSatz.org_id == org_id)
        .order_by(BmaImportSatz.bezeichnung)
        .all()
    )

    vorschlaege = []
    nicht_zugeordnet = []
    verschwunden = []
    for satz in saetze:
        if satz.status == BMA_SATZ_VERSCHWUNDEN:
            verschwunden.append(satz)
            continue
        if satz.objekt_id is None:
            nicht_zugeordnet.append(satz)
            continue
        objekt = db.query(Objekt).filter(Objekt.id == satz.objekt_id).first()
        if objekt is None:
            nicht_zugeordnet.append(satz)
            continue
        if objekt.status in (OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG):
            if satz.bestaetigt_hash != satz.quell_hash:
                vorschlaege.append((satz, objekt, baue_diff(satz, objekt)))

    objekte_zur_auswahl = (
        db.query(Objekt)
        .filter(Objekt.org_id == org_id)
        .order_by(Objekt.name)
        .all()
    )

    return {
        "user": user,
        "vorschlaege": vorschlaege,
        "nicht_zugeordnet": nicht_zugeordnet,
        "verschwunden": verschwunden,
        "objekte_zur_auswahl": objekte_zur_auswahl,
    }


@router.get("/objekte/bma-import", response_class=HTMLResponse)
def bma_import_queue_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    return templates.TemplateResponse(request, "objekt/bma_import.html", _queue_context(request, db, user))


def _satz_or_404(db: Session, satz_id: int, user: User) -> BmaImportSatz:
    satz = db.query(BmaImportSatz).filter(BmaImportSatz.id == satz_id).first()
    if satz is None:
        raise HTTPException(status_code=404, detail="Satz nicht gefunden")
    return satz


@router.post("/objekte/bma-import/{satz_id}/uebernehmen", response_class=HTMLResponse)
def bma_import_uebernehmen(
    satz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.bma_import.bma_sync import uebernehme_vorschlag

    satz = _satz_or_404(db, satz_id, user)
    try:
        uebernehme_vorschlag(db, satz, user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "objekt/_bma_import_content.html", _queue_context(request, db, user))


@router.post("/objekte/bma-import/{satz_id}/ignorieren", response_class=HTMLResponse)
def bma_import_ignorieren(
    satz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.bma_import.bma_sync import ignoriere_vorschlag

    satz = _satz_or_404(db, satz_id, user)
    ignoriere_vorschlag(db, satz, user)
    db.commit()
    return templates.TemplateResponse(request, "objekt/_bma_import_content.html", _queue_context(request, db, user))


@router.post("/objekte/bma-import/{satz_id}/objekt-anlegen", response_class=HTMLResponse)
def bma_import_objekt_anlegen(
    satz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.bma_import.bma_sync import lege_objekt_fuer_satz_an

    satz = _satz_or_404(db, satz_id, user)
    try:
        lege_objekt_fuer_satz_an(db, satz, user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "objekt/_bma_import_content.html", _queue_context(request, db, user))


@router.post("/objekte/bma-import/{satz_id}/zuordnen", response_class=HTMLResponse)
def bma_import_zuordnen(
    satz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    ziel_objekt_id: int = Form(...),
):
    from app.services.bma_import.bma_sync import ordne_satz_objekt_zu

    satz = _satz_or_404(db, satz_id, user)
    objekt = db.query(Objekt).filter(Objekt.id == ziel_objekt_id).first()
    if objekt is None:
        raise HTTPException(status_code=404, detail="Zielobjekt nicht gefunden")
    try:
        ordne_satz_objekt_zu(db, satz, objekt, user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "objekt/_bma_import_content.html", _queue_context(request, db, user))
