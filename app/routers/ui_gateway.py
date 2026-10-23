"""ECPG – Web-UI zur Gateway-/Drucker-/Druckregel-Verwaltung + manueller Druck.

Verwaltung ist org_admin-only (Guard require_role). Alle Routen zusätzlich hinter
require_gateway_enabled (HTTP 404 wenn Modul inaktiv). Prefix: /gateway
Der manuelle Druck (/print/job) ist für jeden Nutzer mit Zugriff auf den Bezug
(Einsatz/GSL/Objekt) erlaubt und liegt daher unter eigenem Guard.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session, object_session

from app.core.async_bridge import run_async
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.gateway import (
    DOC_OBJEKT_DOKUMENT,
    DOCUMENT_TYPE_LABELS,
    GATEWAY_KIND_LABELS,
    GATEWAY_KIND_LINUX,
    OBJEKT_ELEMENT_LABELS,
    RULE_DOCUMENT_LABELS,
    TRIGGER_DOCUMENT_TYPES,
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
    """Ist ein Gateway dieser Org online? (Live-WS-Registry ODER DB-Heartbeat).

    Delegiert an ws.gateway_online – nötig bei mehreren Workern, da die In-Memory-
    Registry pro Prozess liegt und ein Request sonst fälschlich "offline" meldet,
    obwohl das Gateway an einem anderen Worker verbunden ist."""
    from app.routers.ws import gateway_online
    return gateway_online(org_id)


def _prepare_printer_status(printers: list[Printer], gateways: dict[int, Gateway]) -> None:
    """Bereitet gateway-genaue, abgeleitete Statuswerte fuer Templates vor.

    Druckziele mit Windows-Druckagent-Routen gelten als erreichbar, sobald mindestens eine
    Route bereit ist (Agent online, Drucker erreichbar, Identität passt)."""
    from app.services.gateway_service import printer_checked_at, printer_reachable
    from app.services.print_agent_service import hat_agent_routen, route_kandidaten
    for printer in printers:
        gateway = gateways.get(printer.gateway_id)
        session = object_session(printer)
        if session is not None and hat_agent_routen(session, printer.id):
            setattr(printer, "reachable_derived", bool(route_kandidaten(session, printer)))
        else:
            setattr(printer, "reachable_derived", (
                printer_reachable(printer, gateway) if gateway is not None else None
            ))
        setattr(printer, "checked_at_derived", printer_checked_at(printer))


def _historie_context(db: Session, gw: Gateway) -> dict:
    """Gateway-genauer Kontext fuer Detailseite, Partial und Abbruchantwort."""
    printers = db.query(Printer).filter(Printer.gateway_id == gw.id).order_by(Printer.name).all()
    _prepare_printer_status(printers, {gw.id: gw})
    jobs = (
        db.query(PrintJob).filter(PrintJob.gateway_id == gw.id)
        .order_by(PrintJob.erstellt_am.desc()).limit(30).all()
    )
    return {"gw": gw, "printers": printers, "jobs": jobs}


# ── Windows-Druckagent ─────────────────────────────────────────────────────────

def _agent_detail(
    request: Request, db: Session, user: User, gw: Gateway,
    *, pairing_code: str | None = None, device_token: str | None = None,
) -> HTMLResponse:
    """Detailseite eines Windows-Druckagents: Inventar, Routen, Redundanz, Historie."""
    from app.models.gateway import RENDER_MODES, PrinterAgentRoute
    from app.routers.ws import gateway_online_id

    eigene = (
        db.query(PrinterAgentRoute)
        .filter(PrinterAgentRoute.gateway_id == gw.id)
        .order_by(PrinterAgentRoute.priority, PrinterAgentRoute.id)
        .all()
    )
    org_printers = db.query(Printer).filter(Printer.org_id == user.org_id).order_by(Printer.name).all()
    gateways = {g.id: g for g in db.query(Gateway).filter(Gateway.org_id == user.org_id).all()}
    _prepare_printer_status(org_printers, gateways)
    printer_by_id = {p.id: p for p in org_printers}
    alle_routen = (
        db.query(PrinterAgentRoute)
        .filter(PrinterAgentRoute.printer_id.in_([r.printer_id for r in eigene] or [-1]))
        .order_by(PrinterAgentRoute.priority, PrinterAgentRoute.id)
        .all()
    )
    routen_je_drucker: dict[int, list] = {}
    for route in alle_routen:
        routen_je_drucker.setdefault(route.printer_id, []).append(route)
    queues = (gw.agent_info or {}).get("queues") or []
    gebundene_queues = {route.local_queue for route in eigene}
    jobs = (
        db.query(PrintJob).filter(PrintJob.gateway_id == gw.id)
        .order_by(PrintJob.erstellt_am.desc()).limit(30).all()
    )
    return templates.TemplateResponse(request, "gateway/agent_detail.html", {
        "user": user,
        "gw": gw,
        "connected": gateway_online_id(gw.id, user.org_id),
        "routen": eigene,
        "routen_je_drucker": routen_je_drucker,
        "printer_by_id": printer_by_id,
        "org_printers": org_printers,
        "gateways": gateways,
        "queues": [q for q in queues if isinstance(q, dict)],
        "gebundene_queues": gebundene_queues,
        "render_modes": RENDER_MODES,
        # Druckhistorie-Partial: Druckernamen aller Druckziele (auch fremd beheimatete)
        "printers": org_printers,
        "jobs": jobs,
        "cloud_url": str(request.base_url).rstrip("/"),
        "pairing_code": pairing_code,
        "device_token": device_token,
    }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def _agent_config_push(db: Session, gw: Gateway) -> None:
    """Schickt dem Agent nach einer Routenänderung sofort die neue Konfiguration."""
    from app.routers.ws import push_agent_message
    from app.services.print_agent_service import agent_config_sync

    assert gw.org_id is not None
    await push_agent_message(gw.org_id, [gw.id], {"type": "config_sync", "payload": agent_config_sync(db, gw)})


_ROUTE_OPTION_KEYS = ("tray", "orientation", "scaling", "force_color", "dpi", "target_dir", "target_uri")


def _route_options(form) -> dict:
    """Druckweg-Optionen aus dem Formular (leere Felder entfallen, Befehl als Zeilenliste)."""
    import json as _json

    optionen: dict = {}
    for key in _ROUTE_OPTION_KEYS:
        wert = str(form.get(key) or "").strip()
        if not wert:
            continue
        if key == "dpi":
            try:
                optionen[key] = max(72, min(int(wert), 1200))
            except ValueError:
                continue
        else:
            optionen[key] = wert[:500]
    if str(form.get("collate") or "") == "1":
        optionen["collate"] = True
    befehl = str(form.get("command") or "").strip()
    if befehl:
        # Eine Zeile je Argument; erste Zeile = Programmpfad (muss lokal in agent.json erlaubt sein).
        try:
            liste = _json.loads(befehl)
        except ValueError:
            liste = [zeile.strip() for zeile in befehl.splitlines() if zeile.strip()]
        if isinstance(liste, list) and liste:
            optionen["command"] = [str(teil)[:500] for teil in liste][:20]
    return optionen


@router.post("/{gateway_id}/routes/add")
async def agent_route_add(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Ordnet eine lokale Queue des Agents einem Druckziel zu (neu oder bestehend)."""
    from app.models.gateway import RENDER_MODES, PrinterAgentRoute

    gw = _gw_or_404(db, user.org_id, gateway_id)
    if not gw.is_windows_agent:
        raise HTTPException(status_code=400, detail="Kein Windows-Druckagent")
    form = await request.form()
    queue = str(form.get("local_queue") or "").strip()[:255]
    render_mode = str(form.get("render_mode") or "driver")
    if render_mode not in RENDER_MODES:
        render_mode = "driver"
    if not queue and render_mode in ("driver", "pdf_direct"):
        return RedirectResponse(f"/gateway/{gw.id}?error=queue#routen", status_code=303)
    try:
        prioritaet = max(1, min(int(str(form.get("priority") or "10")), 999))
    except ValueError:
        prioritaet = 10
    inventar = next(
        (q for q in (gw.agent_info or {}).get("queues") or [] if isinstance(q, dict) and q.get("queue") == queue),
        {},
    )
    ziel = str(form.get("ziel") or "neu")
    if ziel == "neu":
        name = str(form.get("name") or "").strip()[:150] or queue or "Druckziel"
        printer = Printer(
            org_id=user.org_id, gateway_id=gw.id, name=name, uri=f"agent://{queue or render_mode}"[:300],
            modell=(str(inventar.get("driver") or "")[:150] or None), aktiv=True,
            capabilities=inventar.get("capabilities") if isinstance(inventar.get("capabilities"), dict) else None,
            identity=inventar.get("identity") if isinstance(inventar.get("identity"), dict) else None,
            activated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(printer)
        db.flush()
    else:
        try:
            printer_id = int(ziel)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ungültiges Druckziel")
        bestehend = db.get(Printer, printer_id)
        if bestehend is None or bestehend.org_id != user.org_id:
            raise HTTPException(status_code=404, detail="Druckziel nicht gefunden")
        printer = bestehend
        vorhanden = (
            db.query(PrinterAgentRoute)
            .filter(PrinterAgentRoute.printer_id == printer.id, PrinterAgentRoute.gateway_id == gw.id)
            .first()
        )
        if vorhanden is not None:
            return RedirectResponse(f"/gateway/{gw.id}?error=route_doppelt#routen", status_code=303)
    db.add(PrinterAgentRoute(
        org_id=user.org_id, printer_id=printer.id, gateway_id=gw.id, priority=prioritaet,
        local_queue=queue or render_mode, render_mode=render_mode, options=_route_options(form) or None,
        identity_check=str(form.get("identity_check") or "1") == "1", aktiv=True,
    ))
    db.commit()
    await _agent_config_push(db, gw)
    return RedirectResponse(f"/gateway/{gw.id}?saved=1#routen", status_code=303)


def _route_or_404(db: Session, user: User, route_id: int):
    from app.models.gateway import PrinterAgentRoute

    route = db.get(PrinterAgentRoute, route_id)
    if route is None or route.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Route nicht gefunden")
    return route


@router.post("/routes/{route_id}/save")
async def agent_route_save(
    route_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    from app.models.gateway import RENDER_MODES

    route = _route_or_404(db, user, route_id)
    form = await request.form()
    try:
        route.priority = max(1, min(int(str(form.get("priority") or route.priority)), 999))
    except ValueError:
        pass
    render_mode = str(form.get("render_mode") or route.render_mode)
    if render_mode in RENDER_MODES:
        route.render_mode = render_mode
    queue = str(form.get("local_queue") or "").strip()[:255]
    if queue:
        route.local_queue = queue
    route.options = _route_options(form) or None
    route.identity_check = str(form.get("identity_check") or "") == "1"
    route.aktiv = str(form.get("aktiv") or "") == "1"
    db.commit()
    gw = _gw_or_404(db, user.org_id, route.gateway_id)
    await _agent_config_push(db, gw)
    return RedirectResponse(f"/gateway/{gw.id}?saved=1#routen", status_code=303)


@router.post("/routes/{route_id}/delete")
async def agent_route_delete(
    route_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    route = _route_or_404(db, user, route_id)
    gw = _gw_or_404(db, user.org_id, route.gateway_id)
    db.delete(route)
    db.commit()
    await _agent_config_push(db, gw)
    return RedirectResponse(f"/gateway/{gw.id}?route_deleted=1#routen", status_code=303)


@router.post("/printers/{printer_id}/identity-reset")
def printer_identity_reset(
    printer_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Soll-Identität eines Druckziels verwerfen (z. B. nach Drucker-Tausch); die nächste
    Bereitschaftsmeldung einer Route übernimmt die neue Identität."""
    printer = db.get(Printer, printer_id)
    if printer is None or printer.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Drucker nicht gefunden")
    printer.identity = None
    db.commit()
    return RedirectResponse(f"/gateway/{printer.gateway_id}?saved=1#routen", status_code=303)


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
    from app.routers.ws import gateway_online_id
    connected_ids = {gw.id for gw in gateways if gateway_online_id(gw.id, user.org_id)}
    return templates.TemplateResponse(request, "gateway/liste.html", {
        "user": user,
        "gateways": gateways,
        "connected_ids": connected_ids,
    })


@router.get("/{gateway_id:int}", response_class=HTMLResponse)
def gateway_detail(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    if gw.is_windows_agent:
        return _agent_detail(request, db, user, gw)
    historie = _historie_context(db, gw)
    printers = historie["printers"]
    rules = (
        db.query(PrintRule).filter(PrintRule.org_id == user.org_id)
        .order_by(PrintRule.sort_order, PrintRule.name).all()
    )
    from app.routers.ws import gateway_online_id, get_passthrough_status
    from app.services import ws_bus
    return templates.TemplateResponse(request, "gateway/detail.html", {
        "user": user,
        "gw": gw,
        "printers": printers,
        "rules": rules,
        "jobs": historie["jobs"],
        "connected": gateway_online_id(gw.id, user.org_id),
        "doc_labels": RULE_DOCUMENT_LABELS,
        "objekt_element_labels": OBJEKT_ELEMENT_LABELS,
        "trigger_labels": TRIGGER_LABELS,
        "passthrough_status": get_passthrough_status(user.org_id),
        "redis_status": ws_bus.status(),
    })


@router.get("/{gateway_id:int}/historie", response_class=HTMLResponse)
def gateway_historie(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    context = _historie_context(db, gw)
    context["user"] = user
    return templates.TemplateResponse(request, "gateway/_druckhistorie.html", context)


@router.get("/{gateway_id:int}/druckregeln", response_class=HTMLResponse)
def gateway_druckregeln(
    gateway_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    org_id = user.org_id
    if org_id is None:
        raise HTTPException(status_code=404, detail="Organisation nicht gefunden")
    # Alle Drucker der Org (nicht nur die dieses Gateways): Regeln sind org-weit, und das
    # Speichern ersetzt printer_ids – sonst fielen Druckziele anderer Gateways/Agents heraus.
    printers = (
        db.query(Printer).filter(Printer.org_id == org_id).order_by(Printer.name).all()
    )
    rules = (
        db.query(PrintRule).filter(PrintRule.org_id == user.org_id)
        .order_by(PrintRule.sort_order, PrintRule.name).all()
    )
    from app.services.druckregel_uebersicht import (
        alarmstufen_katalog,
        alarmstufen_matrix,
        regel_beschreibung,
    )
    from app.services.print_dispatcher import objekt_element_optionen

    objekt_labels = objekt_element_optionen(db, org_id)
    for regel in rules:
        abweichungen = (regel.options or {}).get("je_alarmstufe", {})
        abweichungs_elemente = []
        if isinstance(abweichungen, dict):
            for wert in abweichungen.values():
                if isinstance(wert, dict) and isinstance(wert.get("objekt_elements"), list):
                    abweichungs_elemente.extend(wert["objekt_elements"])
        for code in (regel.objekt_elements or []) + abweichungs_elemente:
            objekt_labels.setdefault(
                code, OBJEKT_ELEMENT_LABELS.get(code, f"{code} (nicht mehr vorhanden)")
            )
    drucker_namen = {drucker.id: drucker.name for drucker in printers}
    matrix = alarmstufen_matrix(db, org_id, rules, objekt_labels=objekt_labels)
    from app.core.timezones import org_tz
    from app.models.master import FireDept
    org = db.get(FireDept, org_id)
    return templates.TemplateResponse(request, "gateway/druckregeln.html", {
        "user": user, "gw": gw, "printers": printers, "rules": rules,
        "connected": _is_connected(user.org_id),
        "doc_labels": RULE_DOCUMENT_LABELS,
        "objekt_element_labels": objekt_labels,
        "alarmstufen_katalog": alarmstufen_katalog(db, org_id),
        "trigger_document_types": {
            trigger: sorted(document_types)
            for trigger, document_types in TRIGGER_DOCUMENT_TYPES.items()
        },
        "trigger_document_labels": {
            document_type: DOCUMENT_TYPE_LABELS.get(document_type, document_type)
            for document_type in sorted(set().union(*TRIGGER_DOCUMENT_TYPES.values()))
            # Objekt-Dokumentseiten laufen über die Objekt-Inhalte, nicht als eigene Spalte.
            if document_type != DOC_OBJEKT_DOKUMENT
        },
        "trigger_labels": TRIGGER_LABELS,
        "matrix": matrix,
        "regel_beschreibungen": {
            regel.id: regel_beschreibung(
                regel, drucker_namen=drucker_namen, objekt_labels=objekt_labels,
            ) for regel in rules
        },
        "simulation_default_uhrzeit": datetime.now(UTC).astimezone(org_tz(org)).strftime("%H:%M"),
    })


@router.get("/{gateway_id:int}/druckregeln/simulation", response_class=HTMLResponse)
def gateway_druckregeln_simulation(
    gateway_id: int,
    request: Request,
    trigger: str = "einsatz_created",
    alarm_code: str = "",
    uebung: str = "echt",
    bma: str = "",
    uhrzeit: str = "",
    stichwort: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    _gw_or_404(db, user.org_id, gateway_id)  # Gateway-Guard (Org)
    org_id = user.org_id
    if org_id is None:
        raise HTTPException(status_code=404, detail="Organisation nicht gefunden")
    from app.services.druckregel_uebersicht import EINSATZ_TRIGGER, simuliere
    from app.services.print_dispatcher import objekt_element_optionen

    if trigger not in EINSATZ_TRIGGER:
        raise HTTPException(status_code=422, detail="Ungültiger Auslöser")
    if not re.fullmatch(r"\d{2}:\d{2}", uhrzeit or ""):
        from app.core.timezones import org_tz
        from app.models.master import FireDept
        uhrzeit = datetime.now(UTC).astimezone(org_tz(db.get(FireDept, org_id))).strftime("%H:%M")
    rules = (db.query(PrintRule).filter(PrintRule.org_id == org_id)
             .order_by(PrintRule.sort_order, PrintRule.name).all())
    printers = db.query(Printer).filter(Printer.org_id == org_id).all()
    ergebnisse = simuliere(
        db, org_id, rules, trigger=trigger, alarm_code=alarm_code, is_exercise=uebung == "uebung",
        nur_bma=bma == "1", uhrzeit=uhrzeit, stichwort=stichwort,
        drucker_namen={drucker.id: drucker.name for drucker in printers},
        objekt_labels=objekt_element_optionen(db, org_id),
    )
    return templates.TemplateResponse(request, "gateway/_druckregel_simulation.html", {
        "ergebnisse": ergebnisse,
    })


# ── Gateway-CRUD + Pairing ─────────────────────────────────────────────────────

@router.post("/create")
def gateway_create(
    request: Request,
    name: str = Form(...),
    standort: str = Form(""),
    kind: str = Form(GATEWAY_KIND_LINUX),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    name = name.strip()[:150]
    if not name:
        return RedirectResponse("/gateway?error=name", status_code=303)
    if kind not in GATEWAY_KIND_LABELS:
        kind = GATEWAY_KIND_LINUX
    gw = Gateway(org_id=user.org_id, name=name, standort=(standort.strip()[:200] or None), kind=kind)
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
    if gw.is_windows_agent:
        # Keine Secrets in URLs: weder Access-Logs noch Referrer/Browser-History.
        return _agent_detail(request, db, user, gw, pairing_code=code)
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
    if gw.is_windows_agent:
        return _agent_detail(request, db, user, gw, device_token=token)
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
    health_interval_s: int = Form(60),
    passthrough_enabled: str = Form(""),
    passthrough_port: int = Form(0),
    passthrough_bind: str = Form("0.0.0.0"),
    passthrough_max_clients: int = Form(8),
    passthrough_allowlist: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    gw = _gw_or_404(db, user.org_id, gateway_id)
    allowlist = [a.strip() for a in passthrough_allowlist.replace(";", ",").split(",") if a.strip()]
    gw.wut_config = {
        "host": host.strip(),
        "port": int(port),
        "idle_ms": int(idle_ms),
        "charset": charset.strip() or "cp850",
        "datagram_strategy": datagram_strategy,
        "notfalldruck_printer_id": notfalldruck_printer_id,
        # Drucker-Health-Check-Intervall (Sekunden)
        "health_interval_s": max(15, int(health_interval_s)),
        # Serial-Fan-Out (Durchschleifen des W&T-Stroms an mehrere Clients)
        "passthrough_enabled": passthrough_enabled in ("1", "true", "on"),
        "passthrough_port": int(passthrough_port),
        "passthrough_bind": passthrough_bind.strip() or "0.0.0.0",
        "passthrough_max_clients": max(1, int(passthrough_max_clients)),
        "passthrough_allowlist": allowlist,
    }
    db.commit()
    from app.routers.ws import push_config_sync
    assert user.org_id is not None  # _gw_or_404 oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # _gw_or_404 oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # Guard oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # Guard oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # Guard oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # Guard oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # _gw_or_404 oben hat bereits org-gebunden gefunden
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
    assert user.org_id is not None  # Guard oben hat bereits org-gebunden gefunden
    await push_gateway_command(user.org_id, {"type": "test_page", "printer_id": p.id})
    return RedirectResponse(f"/gateway/{p.gateway_id}?test=1#drucker", status_code=303)


# ── Druckregeln (Phase 4) ──────────────────────────────────────────────────────

def _rule_return(gw: int | None, suffix: str) -> str:
    base = f"/gateway/{gw}/druckregeln" if gw else "/gateway"
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
    rule = PrintRule(org_id=user.org_id, name=name, trigger=trigger, aktiv=trigger != "mail_eingang")
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
    alarmstufen: list[str] = Form(default=[]),
    min_alarmstufe: int | None = Form(None),
    stichwort: str = Form(""),
    nur_bma: str = Form(""),
    filter_uebung: str = Form("alle"),
    zeit_von: str = Form(""),
    zeit_bis: str = Form(""),
    copies: int = Form(1),
    page_range: str = Form(""),
    duplex: str = Form(""),
    color: str = Form(""),
    media: str = Form(""),
    mail_nur_zugeordnet: str = Form(""),
    mail_nur_freigegeben: str = Form(""),
    mail_attachments: str = Form(""),
    mail_max_pages: int = Form(50),
    stelle_nur_erste_disposition: str = Form(""),
    stelle_auftragsart: str = Form("alle"),
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    rule = db.get(PrintRule, rule_id)
    if rule is None or rule.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    org_id = user.org_id
    if org_id is None:
        raise HTTPException(status_code=404, detail="Organisation nicht gefunden")

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

    ausgewaehlte_dokumente = [d for d in documents if d in RULE_DOCUMENT_LABELS]
    erlaubte_dokumente = TRIGGER_DOCUMENT_TYPES.get(rule.trigger, frozenset())
    if any(d not in erlaubte_dokumente for d in ausgewaehlte_dokumente):
        return RedirectResponse(
            _rule_return(gw, f"error=rule_documents#regel-{rule.id}"), status_code=303,
        )
    from app.services.druckregel_uebersicht import alarmstufen_katalog
    from app.services.print_dispatcher import objekt_element_optionen

    erlaubte_objekt_elemente = objekt_element_optionen(db, org_id)
    rule.documents = ausgewaehlte_dokumente
    rule.objekt_elements = [e for e in objekt_elements if e in erlaubte_objekt_elemente]
    rule.printer_ids = [int(p) for p in printer_ids]
    rule.fallback_printer_id = fallback_printer_id

    # Filter: nur gesetzte Schlüssel speichern (leere = kein Filter)
    filters: dict = {}
    if filter_uebung in ("nur_echt", "nur_uebung"):
        filters["uebung"] = filter_uebung
    katalog = alarmstufen_katalog(db, org_id)
    katalog_codes = [eintrag["code"] for eintrag in katalog]
    angeforderte_alarmstufen = []
    for code in alarmstufen:
        normalisiert = code.strip().upper()[:10]
        if normalisiert and normalisiert not in angeforderte_alarmstufen:
            angeforderte_alarmstufen.append(normalisiert)
    ausgewaehlte_alarmstufen = (
        [code for code in katalog_codes if code in angeforderte_alarmstufen]
        + [code for code in angeforderte_alarmstufen if code not in katalog_codes]
    )
    form = run_async(request.form)
    if ausgewaehlte_alarmstufen:
        filters["alarmstufen"] = ausgewaehlte_alarmstufen
    elif "min_alarmstufe" in form and min_alarmstufe:
        filters["min_alarmstufe"] = int(min_alarmstufe)
    stichworte = [s.strip() for s in stichwort.replace(";", ",").split(",") if s.strip()]
    if stichworte:
        filters["stichwort"] = stichworte
    if nur_bma in ("1", "true", "on"):
        filters["nur_bma"] = True
    if zeit_von.strip() and zeit_bis.strip():
        filters["zeitfenster"] = {"von": zeit_von.strip()[:5], "bis": zeit_bis.strip()[:5]}
    if rule.trigger == "mail_eingang":
        if mail_nur_zugeordnet in ("1", "true", "on"):
            filters["mail_nur_zugeordnet"] = True
        if mail_nur_freigegeben in ("1", "true", "on"):
            filters["mail_nur_freigegeben"] = True
    if rule.trigger == "gsl_stelle_disponiert":
        if stelle_nur_erste_disposition in ("1", "true", "on"):
            filters["stelle_nur_erste_disposition"] = True
        if stelle_auftragsart in ("erkundung", "abarbeitung"):
            filters["stelle_auftragsart"] = stelle_auftragsart
    rule.filters = filters

    options: dict = {"copies": max(1, int(copies))}
    if duplex:
        options["duplex"] = duplex
    if color:
        options["color"] = color
    if media in ("A3", "A4"):
        options["media"] = media
    if page_range.strip():
        options["page_range"] = page_range.strip()[:40]
    if rule.trigger == "mail_eingang":
        options["mail_attachments"] = mail_attachments in ("1", "true", "on")
        options["mail_max_pages"] = max(1, min(int(mail_max_pages), 200))
    if rule.trigger in ("einsatz_created", "einsatz_created_gsl", "einsatz_updated"):
        abweichungen: dict[str, dict] = {}
        bisherige_abweichungen = (rule.options or {}).get("je_alarmstufe", {})
        bisherige_codes = (
            list(bisherige_abweichungen)
            if isinstance(bisherige_abweichungen, dict) else []
        )
        pruef_codes = katalog_codes + [
            code for code in ausgewaehlte_alarmstufen + bisherige_codes
            if code not in katalog_codes
        ]
        if ausgewaehlte_alarmstufen:
            pruef_codes = [
                code for code in pruef_codes
                if code in ausgewaehlte_alarmstufen
                or (code in bisherige_codes and code not in katalog_codes)
            ]
        grund_copies = options["copies"]
        for code in pruef_codes:
            abweichung: dict = {}
            copies_wert = str(form.get(f"stufe_{code}_copies", "")).strip()
            if copies_wert:
                try:
                    stufen_copies = int(copies_wert)
                except ValueError:
                    stufen_copies = 0
                if 1 <= stufen_copies <= 20 and stufen_copies != grund_copies:
                    abweichung["copies"] = stufen_copies
            media_wert = str(form.get(f"stufe_{code}_media", "")).strip()
            if media_wert in ("A3", "A4") and media_wert != options.get("media"):
                abweichung["media"] = media_wert
            stufen_dokumente = list(rule.documents)
            dokument_abweichend = False
            for dokument in erlaubte_dokumente:
                wert = str(form.get(f"stufe_{code}_doc_{dokument}", "")).strip()
                if wert == "ja" and dokument not in stufen_dokumente:
                    stufen_dokumente.append(dokument)
                    dokument_abweichend = True
                elif wert == "nein" and dokument in stufen_dokumente:
                    stufen_dokumente.remove(dokument)
                    dokument_abweichend = True
            if dokument_abweichend:
                abweichung["documents"] = stufen_dokumente
            if str(form.get(f"stufe_{code}_custom_elements", "")) == "1":
                elemente = [
                    element for element in form.getlist(f"stufe_{code}_objekt_elements")
                    if element in erlaubte_objekt_elemente
                ]
                if elemente != rule.objekt_elements:
                    abweichung["objekt_elements"] = elemente
            if abweichung:
                abweichungen[code] = abweichung
        if abweichungen:
            options["je_alarmstufe"] = abweichungen
    rule.options = options
    db.commit()
    return RedirectResponse(_rule_return(gw, f"rule_saved=1#regel-{rule.id}"), status_code=303)


@router.post("/rules/reorder")
def rule_reorder(
    request: Request,
    order: str = Form(...),
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Regel-Reihenfolge per Drag&Drop: `order` = JSON-Liste der Regel-IDs in neuer
    Reihenfolge. sort_order = Position (0 = zuerst ausgewertet)."""
    import json
    try:
        ids = [int(i) for i in json.loads(order)]
    except (ValueError, TypeError):
        return JSONResponse({"ok": False}, status_code=400)
    rules = {
        r.id: r for r in db.query(PrintRule).filter(
            PrintRule.org_id == user.org_id, PrintRule.id.in_(ids)
        ).all()
    }
    for pos, rid in enumerate(ids):
        if rid in rules:
            rules[rid].sort_order = pos
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/rules/{rule_id}/test")
async def rule_test(
    rule_id: int,
    request: Request,
    gw: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Testdruck: wertet die Regel gegen den juengsten passenden Bezug der Org aus."""
    from app.services.print_dispatcher import (
        TRIGGER_TEST_BEZUG,
        build_test_jobs,
        dispatch_job,
        paired_gateway,
        resolve_test_context,
    )

    rule = db.get(PrintRule, rule_id)
    if rule is None or rule.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    if not rule.printer_ids:
        return RedirectResponse(_rule_return(gw, f"test_err=printer#regel-{rule.id}"), status_code=303)

    if user.org_id is None or paired_gateway(db, user.org_id) is None:
        return RedirectResponse(_rule_return(gw, f"test_err=gateway#regel-{rule.id}"), status_code=303)
    bezug = resolve_test_context(db, rule)
    if bezug is None:
        art = TRIGGER_TEST_BEZUG.get(rule.trigger, "einsatz")
        return RedirectResponse(_rule_return(gw, f"test_err={art}#regel-{rule.id}"), status_code=303)

    jobs = build_test_jobs(db, rule, bezug.context)
    if not jobs:
        return RedirectResponse(
            _rule_return(gw, f"test_err=leer&test_art={bezug.art}&test_ref={bezug.ref_id}#regel-{rule.id}"),
            status_code=303,
        )
    db.commit()
    for job in jobs:
        try:
            await dispatch_job(db, job)
        except Exception:
            logger.exception("Testdruck: Job %s nicht zustellbar", job.id)
    return RedirectResponse(
        _rule_return(gw, f"test_ok={len(jobs)}&test_art={bezug.art}&test_ref={bezug.ref_id}#regel-{rule.id}"),
        status_code=303,
    )


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
    """Aktive Drucker der Org – für den manuellen Druck-Dialog.

    Explizit org-gefiltert + include_all_tenants: die Druckerliste hängt so allein am
    expliziten org_id-Filter und nicht am (evtl. nicht gesetzten) Tenant-Kontext des
    AJAX-Requests – verhindert eine fälschlich leere Liste ("kein Drucker verbunden")."""
    printers = (
        db.query(Printer)
        .filter(Printer.org_id == user.org_id, Printer.aktiv == True)  # noqa: E712
        .order_by(Printer.name)
        .execution_options(include_all_tenants=True)
        .all()
    )
    gateways = (
        db.query(Gateway)
        .filter(Gateway.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .all()
    )
    gateway_by_id = {gateway.id: gateway for gateway in gateways}
    _prepare_printer_status(printers, gateway_by_id)
    connected = _is_connected(user.org_id)
    return JSONResponse({
        "connected": connected,
        "printers": [
            {
                "id": p.id,
                "name": p.name,
                "reachable": getattr(p, "reachable_derived", None),
                "checked_at": (p.status or {}).get("checked_at"),
                # Unterstützte Papiergrößen (aus der Discovery); A3 nur wenn der Drucker es kann.
                "media": (p.capabilities or {}).get("media") or ["A4"],
                "color": bool((p.capabilities or {}).get("color")),
            }
            for p in printers
        ],
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
    duplex: str = Form(""),
    media: str = Form(""),
    color: str = Form(""),
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

    # Druckervorgaben bilden die Basis; ``role`` ist kein Druckparameter.
    defaults = {
        key: value for key, value in (printer.defaults or {}).items()
        if key in ("duplex", "color", "media")
    }
    opts = {"copies": int(copies), "duplex": defaults.get("duplex", "off")}
    if duplex:
        opts["duplex"] = duplex

    effective_media = media or defaults.get("media", "A4")
    supported_media = (printer.capabilities or {}).get("media") or ["A4"]
    if effective_media in ("A3", "A4") and effective_media in supported_media:
        opts["media"] = effective_media
    effective_color = color or defaults.get("color", "color")
    if effective_color in ("color", "monochrome") and (printer.capabilities or {}).get("color"):
        opts["color"] = effective_color

    from app.services.print_dispatcher import create_print_job, dispatch_job

    assert user.org_id is not None  # authentifizierter Nutzer hat immer eine Org
    # Kein roher 500: unerwartete Fehler beim Anlegen/Zustellen als klare JSON-Meldung
    # zurückgeben (der Dialog zeigt sie an) und den Traceback loggen.
    try:
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
            options=opts,
            created_by_id=user.id,
        )
        db.commit()
        result = await dispatch_job(db, job)
    except Exception:
        logger.exception(
            "Manueller Druck fehlgeschlagen (document_type=%s, printer_id=%s, org_id=%s)",
            document_type, printer_id, user.org_id,
        )
        return JSONResponse(
            {"ok": False, "error": "Druckauftrag konnte nicht gesendet werden (siehe Server-Log)."},
            status_code=200,
        )

    ok = result.get("status") not in ("failed",)
    return JSONResponse({
        "ok": ok,
        "job_id": job.id,
        "status": result.get("status"),
        "error": result.get("error"),
        "printer": printer.name,
    })


@router.post("/jobs/{job_id}/cancel")
async def cancel_print_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Bricht einen (noch nicht abgeschlossenen) Druckauftrag ab: setzt den Cloud-Status
    auf 'canceled' und sendet dem Gateway das cancel_job-Kommando (CUPS-Job + Spool)."""
    from app.models.gateway import JOB_CANCELED, JOB_TERMINAL, PrintJob
    from app.routers.ws import push_gateway_command
    from app.services.broadcast import broadcast_org

    job = db.get(PrintJob, job_id)
    if job is None or job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Druckauftrag nicht gefunden")
    gw_id = job.gateway_id
    if job.status not in JOB_TERMINAL:
        previous_status = job.status
        job.status = JOB_CANCELED
        job.error = "Abgebrochen (manuell)"
        db.commit()
        logger.info(
            "Druckauftrag %s manuell abgebrochen (user_id=%s, org_id=%s, vorher=%s)",
            job_id, user.id, user.org_id, previous_status,
        )
        assert user.org_id is not None  # Guard oben hat bereits org-gebunden gefunden
        await push_gateway_command(user.org_id, {"type": "cancel_job", "payload": {"job_id": job_id}})
        if job.lease_gateway_id is not None:
            # Windows-Druckagents erreicht der Org-Broadcast nicht – gezielt an den Halter.
            from app.routers.ws import push_agent_message
            await push_agent_message(user.org_id, [job.lease_gateway_id],
                                     {"type": "cancel_job", "payload": {"job_id": job_id}})
        await broadcast_org(user.org_id, {"type": "print_job_status", "job_id": job_id,
                                          "status": JOB_CANCELED})
    if request.headers.get("HX-Request") == "true":
        gw = _gw_or_404(db, user.org_id, gw_id)
        context = _historie_context(db, gw)
        context["user"] = user
        return templates.TemplateResponse(request, "gateway/_druckhistorie.html", context)
    return RedirectResponse(f"/gateway/{gw_id}?job_canceled=1#historie", status_code=303)


AUFLOESUNG_LABELS = {
    "reprint": "Erneut drucken",
    "done": "Wurde gedruckt",
    "discard": "Nicht drucken",
}


@router.post("/jobs/{job_id}/resolve")
async def resolve_uncertain_job(
    job_id: int,
    request: Request,
    action: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_gateway_enabled),
):
    """Löst einen unklaren Agent-Druckauftrag ('uncertain') durch einen Menschen auf.

    reprint: neuer manueller Auftrag auf dasselbe Druckziel (bewusster Nachdruck),
    done: als gedruckt markieren, discard: als abgebrochen markieren. Ohne Prüfung am
    Drucker darf das System nie selbst nachdrucken (kein blinder Doppeldruck)."""
    from app.models.gateway import JOB_CANCELED, JOB_DONE, JOB_UNCERTAIN, PrintJob
    from app.services.broadcast import broadcast_org
    from app.services.print_dispatcher import create_print_job, dispatch_job

    if action not in AUFLOESUNG_LABELS:
        raise HTTPException(status_code=400, detail="Unbekannte Aktion")
    job = db.get(PrintJob, job_id)
    if job is None or job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Druckauftrag nicht gefunden")
    assert user.org_id is not None
    gw_id = job.gateway_id
    if job.status == JOB_UNCERTAIN:
        hinweis = f"{AUFLOESUNG_LABELS[action]} (geprüft von {user.display_name or user.username})"
        job.status = JOB_DONE if action == "done" else JOB_CANCELED
        job.error = (hinweis + (f" – {job.error}" if job.error else ""))[:500]
        db.commit()
        logger.info("Unklarer Druckauftrag %s aufgelöst: %s (user_id=%s)", job.id, action, user.id)
        await broadcast_org(user.org_id, {"type": "print_job_status", "job_id": job.id,
                                          "status": job.status})
        if action == "reprint":
            ersatz, _created = create_print_job(
                db, org_id=user.org_id, gateway_id=job.gateway_id, printer_id=job.printer_id,
                document_type=job.document_type, source="manual", incident_id=job.incident_id,
                gsl_id=job.gsl_id, objekt_id=job.objekt_id, artifact_ref=job.artifact_ref,
                options=dict(job.options or {}), created_by_id=user.id, fallback_of_job_id=job.id,
            )
            db.commit()
            await dispatch_job(db, ersatz)
    if request.headers.get("HX-Request") == "true":
        gw = _gw_or_404(db, user.org_id, gw_id)
        context = _historie_context(db, gw)
        context["user"] = user
        return templates.TemplateResponse(request, "gateway/_druckhistorie.html", context)
    return RedirectResponse(f"/gateway/{gw_id}#historie", status_code=303)
