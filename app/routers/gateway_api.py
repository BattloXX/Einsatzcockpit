"""ECPG Gateway-API (Container-zugewandt, ohne User-Session).

- POST /api/v1/gateway/pair       – Einmal-Code → langlebiges Device-Token
- POST /api/v1/gateway/alarms     – seriellen Alarm ingesten (idempotent, Device-Token)
- GET  /api/v1/print/artifacts/id – kurzlebige signierte PDF-URL (Gateway lädt PDF)

Sicherheit: Diese Endpunkte laufen ohne Tenant-Kontext (set_tenant_context(db, None))
und scopen selbst – Pairing über den Code, Alarme über das Device-Token, Artefakte
über die HMAC-Signatur. Siehe SEC-11-Hinweis in app/core/dependencies.py.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.gateway import AlarmIngest, Gateway

logger = logging.getLogger("einsatzleiter.gateway")
alarm_logger = logging.getLogger("einsatzleiter.alarm")
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
def pair(request: Request, db: Session = Depends(get_db)):
    """Löst einen Pairing-Code ein und gibt ein langlebiges Device-Token zurück."""
    set_tenant_context(db, None)
    data = run_async(request.json)
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
def ingest_alarm(
    request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    """Nimmt einen seriell empfangenen Alarm entgegen (idempotent via Sequenz oder Rohtext-Hash)."""
    set_tenant_context(db, None)
    gateway = _resolve_gateway_from_bearer(request, db)
    if gateway is None:
        raise HTTPException(status_code=401, detail="Ungültiges Device-Token")
    assert gateway.org_id is not None  # Gateway wird immer mit org_id angelegt

    data = run_async(request.json)
    raw_bytes = None
    if data.get("raw_bytes_b64") is not None:
        try:
            raw_bytes = base64.b64decode(data["raw_bytes_b64"], validate=True)
        except (ValueError, TypeError):
            alarm_logger.warning("Ungueltige Alarm-Bytes ignoriert (gateway %s)", gateway.id)
        if raw_bytes is not None and len(raw_bytes) > 65536:
            alarm_logger.warning("Alarm-Bytes auf 64 KiB gekuerzt (gateway %s)", gateway.id)
            raw_bytes = raw_bytes[:65536]
    raw_text = data.get("raw_text") or ""
    if not raw_text and raw_bytes is not None:
        try:
            raw_text = raw_bytes.decode(data.get("charset") or "utf-8", errors="replace")
        except LookupError:
            alarm_logger.warning("Unbekannter Alarm-Zeichensatz (gateway %s)", gateway.id)
            raw_text = raw_bytes.decode("utf-8", errors="replace")
    if not raw_text:
        raise HTTPException(status_code=400, detail="raw_text fehlt")
    parse_status = data.get("parse_status") or ("parsed" if data.get("parsed") else "parse_failed")
    sequence = data.get("sequence")
    if sequence is not None and type(sequence) is not int:
        alarm_logger.warning("Ungueltige Alarm-Sequenz ignoriert (gateway %s)", gateway.id)
        sequence = None
    received_at = None
    if data.get("received_at"):
        try:
            received_at = datetime.fromisoformat(str(data["received_at"]).replace("Z", "+00:00"))
        except ValueError:
            alarm_logger.warning("Ungueltige Alarm-Empfangszeit ignoriert (gateway %s)", gateway.id)
    # source_addr und incomplete werden akzeptiert; P1a hat dafuer keine DB-Spalten.
    from app.services.serial_alarm_service import process_ingest, store_raw

    ingest, created = store_raw(
        db,
        org_id=gateway.org_id,
        gateway_id=gateway.id,
        raw_text=raw_text,
        raw_bytes=raw_bytes,
        charset=data.get("charset"),
        parsed=data.get("parsed"),
        parse_status=parse_status,
        sequence=sequence,
        received_at_gateway=received_at,
    )
    if created:
        ingest_id = ingest.id
        applied_here: list[AlarmIngest] = []
        try:
            ingest = process_ingest(db, ingest_id, on_applied=applied_here.append)
        except Exception:
            db.rollback()
            alarm_logger.exception("Alarm-Verarbeitung fehlgeschlagen (ingest %s)", ingest_id)
        if applied_here and ingest.einsatz_id and ingest.dedup_action != "duplicate":
            from app.services.alarm_apply_service import nachlauf

            nachlauf(
                db, ingest, org_id=gateway.org_id,
                background_tasks=background_tasks, base_url=str(request.base_url),
            )

    return {
        "ingest_id": ingest.id,
        "duplicate": not created,
        "einsatz_id": ingest.einsatz_id,
        "parse_status": ingest.parse_status,
        "process_status": ingest.process_status,
    }


# ── Signierte PDF-Auslieferung ─────────────────────────────────────────────────

@router.get("/print/artifacts/{job_id}")
def get_artifact(job_id: int, sig: str = "", db: Session = Depends(get_db)):
    """Liefert das gerenderte PDF eines Druckauftrags – nur mit gültiger Signatur."""
    set_tenant_context(db, None)
    from app.models.gateway import PrintJob
    from app.services.print_artifact_service import ArtifactError, render_job_pdf, verify_artifact_token
    from app.services.print_dispatcher import mark_job_failed

    org_id = verify_artifact_token(job_id, sig)
    if org_id is None:
        raise HTTPException(status_code=403, detail="Ungültige oder abgelaufene Signatur")
    job = db.get(PrintJob, job_id)
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=404, detail="Auftrag nicht gefunden")
    try:
        for attempt in range(3):
            try:
                pdf = render_job_pdf(db, job)
                break
            except OperationalError as error:
                args = getattr(error.orig, "args", ())
                code = args[0] if args else None
                if code not in (1020, 1213) or attempt == 2:
                    raise
                # Dieser GET hat keine physische Aussenwirkung. MariaDB verwirft
                # bei Snapshot-Konflikt/Deadlock die Transaktion; komplett neu lesen.
                db.rollback()
                logger.warning(
                    "Artefakt-Transaktion wiederholen (job=%s, code=%s, attempt=%s)", job_id, code, attempt + 1
                )
                time.sleep(0.02 * (attempt + 1))
                refreshed = db.get(PrintJob, job_id)
                if refreshed is None or refreshed.org_id != org_id:
                    raise HTTPException(status_code=404, detail="Auftrag nicht gefunden")
                job = refreshed
    except ArtifactError as exc:
        # Kein Broadcast: Die Historie hat keinen Live-Listener und wird beim Reload geladen.
        mark_job_failed(db, job, f"Rendern fehlgeschlagen: {exc}")
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
    from app.services.print_dispatcher import mark_job_failed

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
        # Kein Broadcast: Die Historie hat keinen Live-Listener und wird beim Reload geladen.
        mark_job_failed(db, job, f"Rendern fehlgeschlagen: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    return HTMLResponse(content=html)
