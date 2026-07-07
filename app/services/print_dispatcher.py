"""ECPG PrintDispatcher – Kernstück der Automatik.

- create_print_job: idempotente Job-Anlage (Dedup je Quelle/Regel/Dokument/Drucker)
- dispatch_job: PDF-URL signieren, ans Gateway senden, Status setzen
- on_event: Domain-Events (Einsatz/GSL/Alarm) → PrintRules auswerten (Phase 4)
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from sqlalchemy.orm import Session

from app.models.gateway import (
    JOB_FAILED,
    JOB_SENT,
    JOB_SOURCE_MANUAL,
    JOB_SOURCE_RULE,
    PrintJob,
)

logger = logging.getLogger("einsatzleiter.print")


# ── Idempotenz ─────────────────────────────────────────────────────────────────

def build_idempotency_key(
    *,
    source: str,
    rule_id: int | None,
    incident_id: int | None,
    gsl_id: int | None,
    objekt_id: int | None,
    document_type: str,
    artifact_ref: str | None,
    printer_id: int | None,
) -> str:
    """Deterministischer Schlüssel für Automatik-Jobs (max. einmal je Kombination).

    Manuelle Jobs bekommen einen zufälligen Schlüssel (immer eindeutig → nie dedupliziert).
    """
    if source == JOB_SOURCE_MANUAL:
        return f"manual:{uuid.uuid4()}"
    parts = [
        source,
        str(rule_id or ""),
        f"i{incident_id or ''}",
        f"g{gsl_id or ''}",
        f"o{objekt_id or ''}",
        document_type,
        str(artifact_ref or ""),
        str(printer_id or ""),
    ]
    raw = ":".join(parts)
    return f"{source}:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def create_print_job(
    db: Session,
    *,
    org_id: int,
    gateway_id: int,
    printer_id: int | None,
    document_type: str,
    source: str = JOB_SOURCE_MANUAL,
    rule_id: int | None = None,
    incident_id: int | None = None,
    gsl_id: int | None = None,
    objekt_id: int | None = None,
    artifact_ref: str | None = None,
    options: dict | None = None,
    created_by_id: int | None = None,
) -> tuple[PrintJob, bool]:
    """Legt einen Druckauftrag an. Gibt (job, created) zurück.

    Bei Automatik-Quellen (source != manual) verhindert der idempotency_key ein
    doppeltes Anlegen: existiert bereits ein Job mit gleichem Schlüssel, wird der
    vorhandene zurückgegeben (created=False).
    """
    key = build_idempotency_key(
        source=source, rule_id=rule_id, incident_id=incident_id, gsl_id=gsl_id,
        objekt_id=objekt_id, document_type=document_type, artifact_ref=artifact_ref,
        printer_id=printer_id,
    )
    if source != JOB_SOURCE_MANUAL:
        existing = (
            db.query(PrintJob)
            .filter(PrintJob.idempotency_key == key)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if existing is not None:
            return existing, False

    job = PrintJob(
        org_id=org_id,
        gateway_id=gateway_id,
        printer_id=printer_id,
        source=source,
        rule_id=rule_id,
        incident_id=incident_id,
        gsl_id=gsl_id,
        objekt_id=objekt_id,
        document_type=document_type,
        artifact_ref=artifact_ref,
        options=options or {},
        idempotency_key=key,
        created_by_id=created_by_id,
    )
    db.add(job)
    db.flush()
    return job, True


# ── Zustellung ─────────────────────────────────────────────────────────────────

async def dispatch_job(db: Session, job: PrintJob) -> dict:
    """Sendet einen Job ans Gateway (signierte PDF-URL + Druckoptionen).

    Setzt job.status auf 'sent' bei erfolgreicher Übergabe. Der Endstatus
    (printing/done/failed) kommt asynchron per job_status vom Gateway zurück.
    """
    from app.routers.ws import dispatch_print_job
    from app.services.print_artifact_service import artifact_url

    payload = {
        "job_id": job.id,
        "document_type": job.document_type,
        "printer_id": job.printer_id,
        "artifact_url": artifact_url(job),
        "options": job.options or {},
    }
    job.attempts = (job.attempts or 0) + 1
    try:
        result = await dispatch_print_job(job.org_id, job.id, payload)
    except RuntimeError as exc:
        job.status = JOB_FAILED
        job.error = str(exc)[:500]
        db.commit()
        logger.warning("Druckauftrag %s nicht zustellbar: %s", job.id, exc)
        return {"job_id": job.id, "status": JOB_FAILED, "error": str(exc)}

    # Gateway hat den Job entgegengenommen. Endstatus folgt via job_status.
    status = result.get("status") or JOB_SENT
    if status not in ("done", "printing", "failed"):
        status = JOB_SENT
    job.status = status
    if result.get("error"):
        job.error = str(result["error"])[:500]
    db.commit()
    return {"job_id": job.id, "status": job.status, "error": job.error}


# ── Domain-Events → Druckregeln (Phase 4) ──────────────────────────────────────

def on_event(db: Session, org_id: int, trigger: str, context: dict) -> list[PrintJob]:
    """Wertet alle aktiven PrintRules für (org, trigger) aus und legt Jobs an.

    context: {incident_id?, gsl_id?, objekt_id?, alarmstufe?, stichwort?, nur_bma?}
    Gibt die neu angelegten Jobs zurück (Dispatch erfolgt separat/asynchron).
    Verbindet sich kein Gateway, bleiben die Jobs 'queued'.
    """
    from app.models.gateway import Gateway, PrintRule
    from app.services.gateway_service import gateway_effective_enabled

    if not gateway_effective_enabled(org_id, db):
        return []

    gateway = (
        db.query(Gateway)
        .filter(Gateway.org_id == org_id, Gateway.device_token_hash.isnot(None))
        .first()
    )
    if gateway is None:
        return []

    rules = (
        db.query(PrintRule)
        .filter(PrintRule.org_id == org_id, PrintRule.trigger == trigger, PrintRule.aktiv == True)  # noqa: E712
        .order_by(PrintRule.sort_order)
        .all()
    )
    created: list[PrintJob] = []
    for rule in rules:
        if not _filter_matches(rule, context):
            continue
        created.extend(_jobs_for_rule(db, gateway, rule, context))
    if created:
        db.flush()
    return created


def _filter_matches(rule, context: dict) -> bool:
    f = rule.filters or {}
    min_stufe = f.get("min_alarmstufe")
    if min_stufe is not None and context.get("alarmstufe") is not None:
        try:
            if int(context["alarmstufe"]) < int(min_stufe):
                return False
        except (ValueError, TypeError):
            pass
    stichworte = f.get("stichwort") or []
    if stichworte and context.get("stichwort"):
        if not any(s.lower() in str(context["stichwort"]).lower() for s in stichworte):
            return False
    if f.get("nur_bma") and not context.get("nur_bma"):
        return False
    return True


async def autoprint_incident_background(incident_id: int) -> None:
    """Background-Hook nach Einsatz-Anlage: wertet Druckregeln (einsatz_created) aus
    und stellt die Jobs zu. Best-effort – Fehler dürfen den Request nie beeinflussen."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_EINSATZ_CREATED
    from app.models.incident import Incident

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        inc = db.get(Incident, incident_id)
        if inc is None or inc.primary_org_id is None:
            return
        org_id = inc.primary_org_id
        set_tenant_context(db, org_id)
        context = {
            "incident_id": incident_id,
            "stichwort": getattr(inc, "reason", None) or getattr(inc, "report_text", None),
        }
        jobs = on_event(db, org_id, TRIGGER_EINSATZ_CREATED, context)
        db.commit()
        for job in jobs:
            try:
                await dispatch_job(db, job)
            except Exception as exc:  # pragma: no cover
                logger.warning("Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Auto-Druck fehlgeschlagen (Einsatz %s): %s", incident_id, exc)
    finally:
        db.close()


def _jobs_for_rule(db: Session, gateway, rule, context: dict) -> list[PrintJob]:
    """Erzeugt Jobs für alle Dokumente × Zieldrucker einer Regel (idempotent)."""
    jobs: list[PrintJob] = []
    printer_ids = rule.printer_ids or []
    documents = rule.documents or []
    for document_type in documents:
        for printer_id in printer_ids:
            job, created = create_print_job(
                db,
                org_id=rule.org_id,
                gateway_id=gateway.id,
                printer_id=printer_id,
                document_type=document_type,
                source=JOB_SOURCE_RULE,
                rule_id=rule.id,
                incident_id=context.get("incident_id"),
                gsl_id=context.get("gsl_id"),
                objekt_id=context.get("objekt_id"),
                options=rule.options or {},
            )
            if created:
                jobs.append(job)
    return jobs
