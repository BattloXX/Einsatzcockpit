"""ECPG – Web-UI zur Gateway-/Drucker-/Druckregel-Verwaltung + manueller Druck.

Verwaltung ist org_admin-only (Guard require_role). Alle Routen zusätzlich hinter
require_gateway_enabled (HTTP 404 wenn Modul inaktiv). Prefix: /gateway
Der manuelle Druck (/print/job) ist für jeden Nutzer mit Zugriff auf den Bezug
(Einsatz/GSL/Objekt) erlaubt und liegt daher unter eigenem Guard.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.gateway import (
    DOCUMENT_TYPE_LABELS,
    OBJEKT_ELEMENT_LABELS,
    TRIGGER_LABELS,
    Gateway,
    Printer,
    PrintJob,
    PrintRule,
)
from app.models.user import User

logger = logging.getLogger("einsatzleiter.gateway")
router = APIRouter(prefix="/gateway", tags=["gateway-ui"])


# ── Guards ─────────────────────────────────────────────────────────────────────

def require_gateway_enabled(request: Request) -> None:
    if not getattr(request.state, "gateway_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _gw_or_404(db: Session, org_id: int | None, gateway_id: int) -> Gateway:
    gw = db.get(Gateway, gateway_id)
    if gw is None or gw.org_id != org_id:
        raise HTTPException(status_code=404, detail="Gateway nicht gefunden")
    return gw


def _is_connected(org_id: int | None) -> bool:
    if org_id is None:
        return False
    from app.routers.ws import is_gateway_connected
    return is_gateway_connected(org_id)


# ── Übersicht ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def gateway_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gateways = (
        db.query(Gateway).filter(Gateway.org_id == user.org_id)
        .order_by(Gateway.name).all()
    )
    return templates.TemplateResponse(request, "gateway/liste.html", {
        "user": user,
        "gateways": gateways,
        "connected": _is_connected(user.org_id),
    })


@router.get("/{gateway_id}", response_class=HTMLResponse)
def gateway_detail(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    printers = db.query(Printer).filter(Printer.gateway_id == gw.id).order_by(Printer.name).all()
    rules = (
        db.query(PrintRule).filter(PrintRule.org_id == user.org_id)
        .order_by(PrintRule.sort_order, PrintRule.name).all()
    )
    jobs = (
        db.query(PrintJob).filter(PrintJob.gateway_id == gw.id)
        .order_by(PrintJob.erstellt_am.desc()).limit(30).all()
    )
    return templates.TemplateResponse(request, "gateway/detail.html", {
        "user": user,
        "gw": gw,
        "printers": printers,
        "rules": rules,
        "jobs": jobs,
        "connected": _is_connected(user.org_id),
        "doc_labels": DOCUMENT_TYPE_LABELS,
        "objekt_element_labels": OBJEKT_ELEMENT_LABELS,
        "trigger_labels": TRIGGER_LABELS,
    })


# ── Gateway-CRUD + Pairing ─────────────────────────────────────────────────────

@router.post("/create")
def gateway_create(
    request: Request,
    name: str = Form(...),
    standort: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    name = name.strip()[:150]
    if not name:
        return RedirectResponse("/gateway?error=name", status_code=303)
    gw = Gateway(org_id=user.org_id, name=name, standort=(standort.strip()[:200] or None))
    db.add(gw)
    db.commit()
    return RedirectResponse(f"/gateway/{gw.id}?created=1", status_code=303)


@router.post("/{gateway_id}/pair-code")
def gateway_pair_code(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    from app.services.gateway_service import erzeuge_pairing_code
    code = erzeuge_pairing_code(db, gw)
    db.commit()
    # Code nur einmalig anzeigen (per Query, da nur Hash gespeichert wird)
    return RedirectResponse(f"/gateway/{gw.id}?pairing_code={code}", status_code=303)


@router.post("/{gateway_id}/rotate-token")
def gateway_rotate(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    from app.services.gateway_service import rotate_token
    token = rotate_token(db, gw)
    db.commit()
    return RedirectResponse(f"/gateway/{gw.id}?device_token={token}", status_code=303)


@router.post("/{gateway_id}/revoke")
def gateway_revoke(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    from app.services.gateway_service import revoke_token
    revoke_token(gw)
    db.commit()
    return RedirectResponse(f"/gateway/{gw.id}?revoked=1", status_code=303)


@router.post("/{gateway_id}/delete")
def gateway_delete(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    db.delete(gw)
    db.commit()
    return RedirectResponse("/gateway?deleted=1", status_code=303)


@router.post("/{gateway_id}/wut")
async def gateway_wut_config(
    gateway_id: int,
    request: Request,
    host: str = Form(""),
    port: int = Form(8000),
    idle_ms: int = Form(2000),
    charset: str = Form("cp850"),
    datagram_strategy: str = Form("idle"),
    notfalldruck_printer_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    gw.wut_config = {
        "host": host.strip(),
        "port": int(port),
        "idle_ms": int(idle_ms),
        "charset": charset.strip() or "cp850",
        "datagram_strategy": datagram_strategy,
        "notfalldruck_printer_id": notfalldruck_printer_id,
    }
    db.commit()
    from app.routers.ws import push_config_sync
    await push_config_sync(user.org_id, gw.id)
    return RedirectResponse(f"/gateway/{gw.id}?saved=1#wut", status_code=303)


# ── Drucker ────────────────────────────────────────────────────────────────────

@router.post("/{gateway_id}/printers/add-ip")
async def printer_add_ip(
    gateway_id: int,
    request: Request,
    name: str = Form(...),
    ip: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    ip = ip.strip()
    name = name.strip()[:150]
    if not name or not ip:
        return RedirectResponse(f"/gateway/{gw.id}?error=printer#drucker", status_code=303)
    uri = f"ipp://{ip}/ipp/print"
    p = Printer(org_id=user.org_id, gateway_id=gw.id, name=name, uri=uri,
                identity={"ip": ip}, aktiv=True)
    from datetime import UTC, datetime
    p.activated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(p)
    db.commit()
    from app.routers.ws import push_config_sync
    await push_config_sync(user.org_id, gw.id)
    return RedirectResponse(f"/gateway/{gw.id}?saved=1#drucker", status_code=303)


@router.post("/printers/{printer_id}/rename")
async def printer_rename(
    printer_id: int,
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    p = db.get(Printer, printer_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Drucker nicht gefunden")
    name = name.strip()[:150]
    if not name:
        return RedirectResponse(f"/gateway/{p.gateway_id}?error=printer#drucker", status_code=303)
    p.name = name
    db.commit()
    # Gateway kennt Drucker per Identität/URI, der Anzeigename ist rein Cloud-seitig —
    # trotzdem Config-Sync, damit CUPS-Queue-Beschriftung ggf. mitzieht.
    from app.routers.ws import push_config_sync
    await push_config_sync(user.org_id, p.gateway_id)
    return RedirectResponse(f"/gateway/{p.gateway_id}?saved=1#drucker", status_code=303)


@router.post("/printers/{printer_id}/defaults")
async def printer_defaults(
    printer_id: int,
    request: Request,
    role: str = Form("standard"),
    duplex: str = Form("off"),
    color: str = Form("color"),
    media: str = Form("A4"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Rolle (standard/backup) + Standard-Druckoptionen je Drucker."""
    p = db.get(Printer, printer_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Drucker nicht gefunden")
    defaults = dict(p.defaults or {})
    defaults.update({
        "role": role if role in ("standard", "backup") else "standard",
        "duplex": duplex,
        "color": color,
        "media": media.strip()[:20] or "A4",
    })
    p.defaults = defaults
    db.commit()
    from app.routers.ws import push_config_sync
    await push_config_sync(user.org_id, p.gateway_id)
    return RedirectResponse(f"/gateway/{p.gateway_id}?saved=1#drucker", status_code=303)


@router.post("/printers/{printer_id}/toggle")
async def printer_toggle(
    printer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    p = db.get(Printer, printer_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Drucker nicht gefunden")
    from datetime import UTC, datetime
    p.aktiv = not p.aktiv
    if p.aktiv and p.activated_at is None:
        p.activated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    from app.routers.ws import push_config_sync
    await push_config_sync(user.org_id, p.gateway_id)
    return RedirectResponse(f"/gateway/{p.gateway_id}?saved=1#drucker", status_code=303)


@router.post("/printers/{printer_id}/delete")
async def printer_delete(
    printer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    p = db.get(Printer, printer_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Drucker nicht gefunden")
    gwid = p.gateway_id
    db.delete(p)
    db.commit()
    from app.routers.ws import push_config_sync
    await push_config_sync(user.org_id, gwid)
    return RedirectResponse(f"/gateway/{gwid}?deleted=1#drucker", status_code=303)


@router.post("/{gateway_id}/discover")
async def printer_discover(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    _gw_or_404(db, user.org_id, gateway_id)
    from app.routers.ws import push_gateway_command
    await push_gateway_command(user.org_id, {"type": "discover_printers"})
    return RedirectResponse(f"/gateway/{gateway_id}?discover=1#drucker", status_code=303)


@router.post("/printers/{printer_id}/test")
async def printer_test(
    printer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    p = db.get(Printer, printer_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Drucker nicht gefunden")
    from app.routers.ws import push_gateway_command
    await push_gateway_command(user.org_id, {"type": "test_page", "printer_id": p.id})
    return RedirectResponse(f"/gateway/{p.gateway_id}?test=1#drucker", status_code=303)


# ── Druckregeln (Phase 4) ──────────────────────────────────────────────────────

def _rule_return(gw: int | None, suffix: str) -> str:
    """Regeln liegen org-weit, werden aber auf der Gateway-Detailseite bearbeitet →
    nach jeder Regel-Aktion zurück auf die Detailseite (Kontext erhalten)."""
    base = f"/gateway/{gw}" if gw else "/gateway"
    return f"{base}?{suffix}"


@router.post("/rules/create")
def rule_create(
    request: Request,
    name: str = Form(...),
    trigger: str = Form(...),
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    name = name.strip()[:150]
    if not name or trigger not in TRIGGER_LABELS:
        return RedirectResponse(_rule_return(gw, "error=rule#regeln"), status_code=303)
    existing = (
        db.query(PrintRule)
        .filter(PrintRule.org_id == user.org_id, PrintRule.name == name).first()
    )
    if existing:
        return RedirectResponse(_rule_return(gw, "error=rule_dup#regeln"), status_code=303)
    rule = PrintRule(org_id=user.org_id, name=name, trigger=trigger, aktiv=True)
    db.add(rule)
    db.commit()
    return RedirectResponse(_rule_return(gw, f"rule_created={rule.id}#regel-{rule.id}"), status_code=303)


@router.post("/rules/{rule_id}/save")
def rule_save(
    rule_id: int,
    request: Request,
    name: str = Form(""),
    trigger: str = Form(""),
    documents: list[str] = Form(default=[]),
    objekt_elements: list[str] = Form(default=[]),
    printer_ids: list[int] = Form(default=[]),
    fallback_printer_id: int | None = Form(None),
    min_alarmstufe: int | None = Form(None),
    stichwort: str = Form(""),
    nur_bma: str = Form(""),
    zeit_von: str = Form(""),
    zeit_bis: str = Form(""),
    copies: int = Form(1),
    page_range: str = Form(""),
    duplex: str = Form("off"),
    color: str = Form("color"),
    sort_order: int = Form(0),
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    rule = db.get(PrintRule, rule_id)
    if rule is None or rule.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")

    # Name/Auslöser optional änderbar (Editor). Name-Kollision mit anderer Regel abfangen.
    name = name.strip()[:150]
    if name and name != rule.name:
        dup = (
            db.query(PrintRule.id)
            .filter(PrintRule.org_id == user.org_id, PrintRule.name == name, PrintRule.id != rule.id)
            .first()
        )
        if dup:
            return RedirectResponse(_rule_return(gw, f"error=rule_dup#regel-{rule.id}"), status_code=303)
        rule.name = name
    if trigger in TRIGGER_LABELS:
        rule.trigger = trigger

    rule.documents = [d for d in documents if d in DOCUMENT_TYPE_LABELS]
    rule.objekt_elements = [e for e in objekt_elements if e in OBJEKT_ELEMENT_LABELS]
    rule.printer_ids = [int(p) for p in printer_ids]
    rule.fallback_printer_id = fallback_printer_id

    # Filter: nur gesetzte Schlüssel speichern (leere = kein Filter)
    filters: dict = {}
    if min_alarmstufe:
        filters["min_alarmstufe"] = int(min_alarmstufe)
    stichworte = [s.strip() for s in stichwort.replace(";", ",").split(",") if s.strip()]
    if stichworte:
        filters["stichwort"] = stichworte
    if nur_bma in ("1", "true", "on"):
        filters["nur_bma"] = True
    if zeit_von.strip() and zeit_bis.strip():
        filters["zeitfenster"] = {"von": zeit_von.strip()[:5], "bis": zeit_bis.strip()[:5]}
    rule.filters = filters

    options: dict = {"copies": max(1, int(copies)), "duplex": duplex, "color": color}
    if page_range.strip():
        options["page_range"] = page_range.strip()[:40]
    rule.options = options
    rule.sort_order = int(sort_order)
    db.commit()
    return RedirectResponse(_rule_return(gw, f"rule_saved=1#regel-{rule.id}"), status_code=303)


@router.post("/rules/{rule_id}/toggle")
def rule_toggle(
    rule_id: int,
    request: Request,
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    rule = db.get(PrintRule, rule_id)
    if rule is None or rule.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    rule.aktiv = not rule.aktiv
    db.commit()
    return RedirectResponse(_rule_return(gw, f"rule_toggled=1#regel-{rule.id}"), status_code=303)


@router.post("/rules/{rule_id}/delete")
def rule_delete(
    rule_id: int,
    request: Request,
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    rule = db.get(PrintRule, rule_id)
    if rule is None or rule.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    db.delete(rule)
    db.commit()
    return RedirectResponse(_rule_return(gw, "rule_deleted=1#regeln"), status_code=303)


@router.get("/printers.json")
def printers_json(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Aktive Drucker der Org – für den manuellen Druck-Dialog."""
    printers = (
        db.query(Printer)
        .filter(Printer.org_id == user.org_id, Printer.aktiv == True)  # noqa: E712
        .order_by(Printer.name).all()
    )
    connected = _is_connected(user.org_id)
    return JSONResponse({
        "connected": connected,
        "printers": [{"id": p.id, "name": p.name} for p in printers],
    })


# ── Manueller Druck (aus Einsatz/GSL/Objekt) ───────────────────────────────────

@router.post("/print/job")
async def manual_print(
    request: Request,
    document_type: str = Form(...),
    printer_id: int = Form(...),
    incident_id: int | None = Form(None),
    gsl_id: int | None = Form(None),
    objekt_id: int | None = Form(None),
    artifact_ref: str | None = Form(None),
    copies: int = Form(1),
    duplex: str = Form("off"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Legt einen manuellen Druckauftrag an und sendet ihn ans Gateway.

    Zugriff: recorder+ (jeder mit Bezug zum Einsatz/GSL/Objekt). Der Bezug wird
    org-scoped geprüft (Tenant-Listener greift auf print_job/printer)."""
    if document_type not in DOCUMENT_TYPE_LABELS:
        return JSONResponse({"ok": False, "error": "Unbekannter Dokumenttyp"}, status_code=400)
    printer = db.get(Printer, printer_id)
    if printer is None or printer.org_id != user.org_id or not printer.aktiv:
        return JSONResponse({"ok": False, "error": "Drucker nicht verfügbar"}, status_code=400)

    from app.services.print_dispatcher import create_print_job, dispatch_job

    job, _ = create_print_job(
        db,
        org_id=user.org_id,
        gateway_id=printer.gateway_id,
        printer_id=printer.id,
        document_type=document_type,
        source="manual",
        incident_id=incident_id,
        gsl_id=gsl_id,
        objekt_id=objekt_id,
        artifact_ref=artifact_ref,
        options={"copies": int(copies), "duplex": duplex},
        created_by_id=user.id,
    )
    db.commit()

    result = await dispatch_job(db, job)
    ok = result.get("status") not in ("failed",)
    return JSONResponse({
        "ok": ok,
        "job_id": job.id,
        "status": result.get("status"),
        "error": result.get("error"),
        "printer": printer.name,
    })
