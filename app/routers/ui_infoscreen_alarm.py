"""Alarm-Infoscreen (Wandmonitor im Geraetehaus) — Objektverwaltung PR6.

- Oeffentliche Token-Route /infoscreen/alarm/{token} (Muster Wetter-Infoscreen):
  Vollbild, dunkel, keine Interaktion. Bei aktivem Alarm: Stichwort/Adresse gross,
  verknuepftes Objekt mit Gefahren-Piktogrammen, Karte mit Objektsymbolen,
  FSD/BMZ/FBF-Standorte. Sonst konfigurierbarer Idle-Modus (Uhr/Wetter/Einsatzliste).
- WS /ws/infoscreen/{token}: abonniert nach Token-Pruefung den Org-Kanal
  (incident_created/objekt_match → sofortiger Wechsel in die Alarmansicht).
- Verwaltung unter /infoscreen-alarm/verwaltung (org_admin): Tokens + Idle-Konfig.

DSGVO: Wohnanlagen-Hinweise werden NIE an den Infoscreen ausgeliefert (fest).
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.security import hash_api_key
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    AUSWAHL_PIKTOGRAMM,
    OBJEKT_EINSATZ_BESTAETIGT,
    AlarmInfoscreenToken,
    Objekt,
    ObjektEinsatz,
)
from app.models.user import User
from app.services.objekt_service import lade_auswahl

router = APIRouter(tags=["infoscreen-alarm"])

IDLE_MODI = {
    "uhr": "Uhr",
    "wetter": "Wetter-Infoscreen",
    "einsatzliste": "Letzte Einsätze",
}


def _token_org(db: Session, token: str) -> tuple[AlarmInfoscreenToken, FireDept]:
    """Validiert den Token (Hash-Lookup) und liefert Token-Zeile + Org."""
    token_hash = hash_api_key(token)
    eintrag = (
        db.query(AlarmInfoscreenToken)
        .execution_options(include_all_tenants=True)
        .filter(AlarmInfoscreenToken.token_hash == token_hash,
                AlarmInfoscreenToken.aktiv.is_(True))
        .first()
    )
    if eintrag is None or eintrag.org_id is None:
        raise HTTPException(status_code=401, detail="Ungueltiger oder gesperrter Token")
    org = db.query(FireDept).filter(FireDept.id == eintrag.org_id).first()
    if org is None:
        raise HTTPException(status_code=404)
    return eintrag, org


def _org_settings(db: Session, org_id: int) -> OrgSettings | None:
    return (
        db.query(OrgSettings)
        .execution_options(include_all_tenants=True)
        .filter(OrgSettings.org_id == org_id)
        .first()
    )


# ── Oeffentliche Infoscreen-Seite ──────────────────────────────────────────────

@router.get("/infoscreen/alarm/{token}", response_class=HTMLResponse, include_in_schema=False)
def infoscreen_seite(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _, org = _token_org(db, token)
    return templates.TemplateResponse(request, "objekt/infoscreen_alarm.html", {
        "org": org,
        "token": token,
    })


@router.get("/infoscreen/alarm/{token}/daten", include_in_schema=False)
def infoscreen_daten(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Aktueller Zustand als JSON (Polling-Fallback + Initial-Load)."""
    import json as _json

    from app.models.incident import Incident

    _, org = _token_org(db, token)
    settings_row = _org_settings(db, org.id)
    dauer_min = settings_row.alarm_infoscreen_alarm_dauer_min if settings_row else 60
    idle_modus = settings_row.alarm_infoscreen_idle_modus if settings_row else "uhr"
    wetter_url = settings_row.alarm_infoscreen_wetter_url if settings_row else None

    grenze = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=dauer_min)
    incident = (
        db.query(Incident)
        .filter(
            Incident.primary_org_id == org.id,
            Incident.status == "active",
            Incident.started_at >= grenze,
        )
        .order_by(Incident.started_at.desc())
        .first()
    )

    from app.services.objekt_symbol_service import symbol_katalog_json
    daten: dict = {
        "org_name": org.name,
        "modus": "idle",
        "idle_modus": idle_modus,
        "wetter_url": wetter_url if idle_modus == "wetter" else None,
        # Org-Symbolkatalog fuer das clientseitige Rendering (auch eigene Bildsymbole)
        "symbole": symbol_katalog_json(db, org.id),
    }

    if incident is not None:
        adresse = " ".join(
            t for t in [incident.address_street, incident.address_no] if t
        )
        if incident.address_city:
            adresse = f"{adresse}, {incident.address_city}".strip(", ")
        daten.update({
            "modus": "alarm",
            "incident": {
                "id": incident.id,
                "stichwort": incident.alarm_type_code,
                "adresse": adresse,
                "meldung": incident.report_text or incident.reason or "",
                "beginn": incident.started_at.isoformat() + "Z" if incident.started_at else None,
                "lat": incident.lat,
                "lng": incident.lng,
            },
        })

        # Verknuepftes Objekt (bestaetigt bevorzugt, sonst erster Vorschlag)
        verknuepfung = (
            db.query(ObjektEinsatz)
            .options(
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.gefahren),
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.bma),
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.karten_objekte),
            )
            .execution_options(include_all_tenants=True)
            .filter(ObjektEinsatz.incident_id == incident.id,
                    ObjektEinsatz.org_id == org.id)
            .order_by(ObjektEinsatz.status)  # "bestaetigt" < "vorschlag"
            .first()
        )
        if verknuepfung is not None and verknuepfung.objekt is not None:
            o = verknuepfung.objekt
            piktogramme = lade_auswahl(db, org.id, AUSWAHL_PIKTOGRAMM)
            bma = o.bma
            karten = []
            for k in o.karten_objekte:
                geometry = None
                if k.geometry_json:
                    try:
                        geometry = _json.loads(k.geometry_json)
                    except (ValueError, TypeError):
                        geometry = None
                karten.append({
                    "typ": k.typ, "lat": k.lat, "lng": k.lng,
                    "geometry": geometry, "label": k.label,
                })
            daten["objekt"] = {
                "nummer": o.anzeige_nummer,
                "name": o.name,
                "vulgoname": o.vulgoname,
                "bestaetigt": verknuepfung.status == OBJEKT_EINSATZ_BESTAETIGT,
                "lat": o.lat,
                "lng": o.lng,
                "gefahren": [
                    {
                        "name": g.gefahr.name if g.gefahr else "",
                        "piktogramm": piktogramme.get(
                            g.gefahr.piktogramm_typ if g.gefahr else "sonstig", "⚠️"
                        )[:2].strip(),
                        "un_nummer": g.un_nummer,
                    }
                    for g in o.gefahren
                ],
                "bma": {
                    "bma_nummer": bma.bma_nummer,
                    "bmz_standort": bma.bmz_standort,
                    "fbf_standort": bma.fbf_standort,
                    "laufkarten_ablageort": bma.laufkarten_ablageort,
                    "fsd_standort": bma.schluesselsafe_standort if bma.schluesselsafe_vorhanden else None,
                    "fsd_inhalt": bma.schluesselsafe_inhalt if bma.schluesselsafe_vorhanden else None,
                } if bma else None,
                "karten_objekte": karten,
                # DSGVO: bewusst KEINE Wohnanlagen-Hinweise am Wandmonitor
            }

    # Idle: letzte Einsaetze (reduziert: Stichwort/Adresse/Zeit)
    if daten["modus"] == "idle" and idle_modus == "einsatzliste":
        letzte = (
            db.query(Incident)
            .filter(Incident.primary_org_id == org.id)
            .order_by(Incident.started_at.desc())
            .limit(5)
            .all()
        )
        daten["einsaetze"] = [
            {
                "stichwort": i.alarm_type_code,
                "adresse": " ".join(t for t in [i.address_street, i.address_no] if t),
                "beginn": i.started_at.isoformat() + "Z" if i.started_at else None,
            }
            for i in letzte
        ]

    return daten


# ── WebSocket: Org-Kanal nach Token-Pruefung ──────────────────────────────────

@router.websocket("/ws/infoscreen/{token}")
async def infoscreen_ws(websocket: WebSocket, token: str):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.broadcast import ORG_WS_OFFSET, manager

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        try:
            _, org = _token_org(db, token)
        except HTTPException:
            await websocket.close(code=4401)
            return
        org_id = org.id
    finally:
        db.close()

    channel = ORG_WS_OFFSET + org_id
    await manager.connect(channel, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(channel, websocket)


# ── Verwaltung (org_admin) ─────────────────────────────────────────────────────

@router.get("/infoscreen-alarm/verwaltung", response_class=HTMLResponse)
def verwaltung(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    from app.routers.ui_objekt import require_objekt_enabled
    require_objekt_enabled(request)

    tokens = (
        db.query(AlarmInfoscreenToken)
        .order_by(AlarmInfoscreenToken.erstellt_am.desc())
        .all()
    )
    settings_row = _org_settings(db, user.org_id) if user.org_id else None
    return templates.TemplateResponse(request, "objekt/infoscreen_verwaltung.html", {
        "user": user,
        "tokens": tokens,
        "einstellungen": settings_row,
        "idle_modi": IDLE_MODI,
        "neuer_token": request.query_params.get("neuer_token"),
        "basis_url": str(request.base_url).rstrip("/"),
    })


@router.post("/infoscreen-alarm/verwaltung/token/neu")
def token_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    name: str = Form(...),
):
    from app.routers.ui_objekt import require_objekt_enabled
    require_objekt_enabled(request)

    token = secrets.token_urlsafe(32)
    db.add(AlarmInfoscreenToken(
        org_id=user.org_id,
        token_hash=hash_api_key(token),
        name=name.strip() or "Infoscreen",
        aktiv=True,
    ))
    write_audit(db, "objekt.infoscreen_token_created", org_id=user.org_id, user_id=user.id,
                payload={"name": name.strip()})
    db.commit()
    # Token einmalig im Klartext anzeigen (danach nur noch Hash gespeichert)
    return RedirectResponse(
        url=f"/infoscreen-alarm/verwaltung?neuer_token={token}", status_code=303
    )


@router.post("/infoscreen-alarm/verwaltung/token/{token_id}/deaktivieren")
def token_deaktivieren(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    from app.routers.ui_objekt import require_objekt_enabled
    require_objekt_enabled(request)

    eintrag = db.query(AlarmInfoscreenToken).filter(AlarmInfoscreenToken.id == token_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    eintrag.aktiv = False
    write_audit(db, "objekt.infoscreen_token_deactivated", org_id=user.org_id, user_id=user.id,
                payload={"name": eintrag.name})
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung", status_code=303)


@router.post("/infoscreen-alarm/verwaltung/einstellungen")
def einstellungen_speichern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    idle_modus: str = Form("uhr"),
    alarm_dauer_min: int = Form(60),
    wetter_url: str = Form(""),
):
    from app.routers.ui_objekt import require_objekt_enabled
    require_objekt_enabled(request)

    if idle_modus not in IDLE_MODI:
        idle_modus = "uhr"
    settings_row = _org_settings(db, user.org_id) if user.org_id else None
    if settings_row is None:
        raise HTTPException(status_code=404, detail="Org-Einstellungen nicht gefunden")
    settings_row.alarm_infoscreen_idle_modus = idle_modus
    settings_row.alarm_infoscreen_alarm_dauer_min = max(5, min(alarm_dauer_min, 720))
    settings_row.alarm_infoscreen_wetter_url = wetter_url.strip() or None
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung?saved=1", status_code=303)
