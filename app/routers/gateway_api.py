"""ECPG Gateway-API (Container-zugewandt, ohne User-Session).

- POST /api/v1/gateway/pair       – Einmal-Code → langlebiges Device-Token
- POST /api/v1/gateway/alarms     – seriellen Alarm ingesten (idempotent, Device-Token)
- GET  /api/v1/print/artifacts/id – kurzlebige signierte PDF-URL (Gateway lädt PDF)

Sicherheit: Diese Endpunkte laufen ohne Tenant-Kontext (set_tenant_context(db, None))
und scopen selbst – Pairing über den Code, Alarme über das Device-Token, Artefakte
über die HMAC-Signatur. Siehe SEC-11-Hinweis in app/core/dependencies.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.gateway import Gateway

logger = logging.getLogger("einsatzleiter.gateway")
router = APIRouter(prefix="/api/v1", tags=["gateway"])


def _resolve_gateway_from_bearer(request: Request, db: Session) -> Gateway | None:
    raw = request.headers.get("authorization", "")
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()
    if not raw:
        return None
    return (
        db.query(Gateway)
        .filter(Gateway.device_token_hash == hash_api_key(raw))
        .first()
    )


# ── Pairing ────────────────────────────────────────────────────────────────────

@router.post("/gateway/pair")
async def pair(request: Request, db: Session = Depends(get_db)):
    """Löst einen Pairing-Code ein und gibt ein langlebiges Device-Token zurück."""
    set_tenant_context(db, None)
    data = await request.json()
    code = (data.get("code") or data.get("pairing_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code fehlt")

    from app.services.gateway_service import pair_gateway

    result = pair_gateway(db, code)
    if result is None:
        raise HTTPException(status_code=401, detail="Ungültiger oder abgelaufener Code")
    gateway, raw_token = result
    db.commit()
    logger.info("Gateway %s gekoppelt (org_id=%s)", gateway.id, gateway.org_id)
    return {
        "device_token": raw_token,
        "gateway_id": gateway.id,
        "name": gateway.name,
    }


# ── Serieller Alarm-Ingest ─────────────────────────────────────────────────────

@router.post("/gateway/alarms")
async def ingest_alarm(request: Request, db: Session = Depends(get_db)):
    """Nimmt einen seriell empfangenen Alarm entgegen (idempotent via Rohtext-Hash)."""
    set_tenant_context(db, None)
    gateway = _resolve_gateway_from_bearer(request, db)
    if gateway is None:
        raise HTTPException(status_code=401, detail="Ungültiges Device-Token")

    data = await request.json()
    raw_text = data.get("raw_text") or ""
    if not raw_text:
        raise HTTPException(status_code=400, detail="raw_text fehlt")
    parse_status = data.get("parse_status") or ("parsed" if data.get("parsed") else "parse_failed")

    from app.services.serial_alarm_service import ingest_alarm as _ingest

    ingest, created = _ingest(
        db,
        org_id=gateway.org_id,
        gateway_id=gateway.id,
        raw_text=raw_text,
        charset=data.get("charset"),
        parsed=data.get("parsed"),
        parse_status=parse_status,
    )
    db.commit()

    # Board/Infoscreen live informieren
    if created and ingest.einsatz_id:
        try:
            from app.services.broadcast import broadcast_org
            await broadcast_org(gateway.org_id, {"type": "incident_created", "incident_id": ingest.einsatz_id})
        except Exception:
            pass

    return {
        "ingest_id": ingest.id,
        "duplicate": not created,
        "einsatz_id": ingest.einsatz_id,
        "parse_status": ingest.parse_status,
    }


# ── Signierte PDF-Auslieferung ─────────────────────────────────────────────────

@router.get("/print/artifacts/{job_id}")
def get_artifact(job_id: int, sig: str = "", db: Session = Depends(get_db)):
    """Liefert das gerenderte PDF eines Druckauftrags – nur mit gültiger Signatur."""
    set_tenant_context(db, None)
    from app.models.gateway import PrintJob
    from app.services.print_artifact_service import ArtifactError, render_job_pdf, verify_artifact_token

    org_id = verify_artifact_token(job_id, sig)
    if org_id is None:
        raise HTTPException(status_code=403, detail="Ungültige oder abgelaufene Signatur")
    job = db.get(PrintJob, job_id)
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=404, detail="Auftrag nicht gefunden")
    try:
        pdf = render_job_pdf(db, job)
    except ArtifactError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return Response(content=pdf, media_type="application/pdf")


@router.get("/print/render/{job_id}", response_class=HTMLResponse)
def get_render_page(job_id: int, sig: str = "", db: Session = Depends(get_db)):
    """Liefert die HTML-Seite eines Leaflet-Karten-Druckauftrags – nur mit gültiger
    Signatur. Das Gateway lädt diese Seite per Headless-Chromium (JS/Tiles) und druckt
    sie. Rendert im Modus render_mode (kein window.print, setzt window.__ecpgReady)."""
    set_tenant_context(db, None)
    from app.models.gateway import HTML_RENDER_DOC_TYPES, PrintJob
    from app.services.print_artifact_service import (
        ArtifactError,
        render_map_html,
        verify_artifact_token,
    )

    org_id = verify_artifact_token(job_id, sig)
    if org_id is None:
        raise HTTPException(status_code=403, detail="Ungültige oder abgelaufene Signatur")
    job = db.get(PrintJob, job_id)
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=404, detail="Auftrag nicht gefunden")
    if job.document_type not in HTML_RENDER_DOC_TYPES:
        raise HTTPException(status_code=400, detail="Kein HTML-Render-Dokumenttyp")
    try:
        html = render_map_html(db, job)
    except ArtifactError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return HTMLResponse(content=html)
