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

    # Aktuelle Org-Lokalzeit für das optionale Zeitfenster-Filter (rule.filters.zeitfenster).
    if "now_hhmm" not in context:
        try:
            from datetime import UTC, datetime

            from app.core.timezones import org_tz
            from app.models.master import FireDept
            org = db.get(FireDept, org_id)
            context["now_hhmm"] = datetime.now(UTC).astimezone(org_tz(org)).strftime("%H:%M")
        except Exception:
            context["now_hhmm"] = None

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
    fenster = f.get("zeitfenster") or {}
    von, bis = fenster.get("von"), fenster.get("bis")
    now = context.get("now_hhmm")
    if von and bis and now:
        # Fenster innerhalb eines Tages (von<=bis) oder über Mitternacht (von>bis).
        if von <= bis:
            if not (von <= now <= bis):
                return False
        elif not (now >= von or now <= bis):
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


def _resolve_autoprint_printer(db: Session, org_id: int):
    """Zieldrucker für Auto-Druck: erstes gekoppeltes Gateway der Org + aktiver Drucker,
    bevorzugt Rolle 'standard'. Gibt (gateway, printer) oder (None, None)."""
    from app.models.gateway import Gateway, Printer

    gateway = (
        db.query(Gateway)
        .filter(Gateway.org_id == org_id, Gateway.device_token_hash.isnot(None))
        .first()
    )
    if gateway is None:
        return None, None
    printers = (
        db.query(Printer)
        .filter(Printer.gateway_id == gateway.id, Printer.aktiv == True)  # noqa: E712
        .order_by(Printer.name)
        .execution_options(include_all_tenants=True)
        .all()
    )
    if not printers:
        return gateway, None
    standard = next((p for p in printers if (p.defaults or {}).get("role") == "standard"), None)
    return gateway, (standard or printers[0])


async def autoprint_verleih_background(ausleihe_id: int) -> None:
    """Background-Hook nach Verleihschein-Anlage: druckt automatisch am Stationsdrucker,
    wenn OrgSettings.verleih_autodruck aktiv ist UND Gateway-Modul + ein aktiver Drucker
    verfügbar sind. Best-effort – Fehler dürfen den Request nie beeinflussen."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import DOC_VERLEIH_SCHEIN
    from app.models.master import OrgSettings
    from app.models.verleih import VerleihAusleihe
    from app.services.gateway_service import gateway_effective_enabled

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        a = db.get(VerleihAusleihe, ausleihe_id)
        if a is None:
            return
        org_id = a.org_id
        set_tenant_context(db, org_id)
        row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not row or not row.verleih_autodruck:
            return
        if not gateway_effective_enabled(org_id, db):
            return
        gateway, printer = _resolve_autoprint_printer(db, org_id)
        if gateway is None or printer is None:
            return
        defaults = printer.defaults or {}
        job, _created = create_print_job(
            db, org_id=org_id, gateway_id=gateway.id, printer_id=printer.id,
            document_type=DOC_VERLEIH_SCHEIN, source=JOB_SOURCE_RULE,
            gsl_id=a.lage_id, artifact_ref=str(a.id),
            options={"copies": 1, "duplex": defaults.get("duplex") or "off"},
        )
        db.commit()
        try:
            await dispatch_job(db, job)
        except Exception as exc:  # pragma: no cover
            logger.warning("Verleih-Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Verleih-Auto-Druck fehlgeschlagen (Ausleihe %s): %s", ausleihe_id, exc)
    finally:
        db.close()


def _resolve_objekt_ids(db: Session, context: dict) -> list[int]:
    """Objekt(e), auf die sich die Regel bezieht: explizit im Kontext oder – bei einem
    Einsatz – die dort bestätigt verknüpften Objekte (ObjektEinsatz)."""
    if context.get("objekt_id"):
        return [int(context["objekt_id"])]
    incident_id = context.get("incident_id")
    if not incident_id:
        return []
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    rows = (
        db.query(ObjektEinsatz.objekt_id)
        .filter(
            ObjektEinsatz.incident_id == incident_id,
            ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
        )
        .all()
    )
    return [r[0] for r in rows]


def _seiten_for_elements(db: Session, objekt_id: int, elements: list[str]):
    """Konkrete druckbare Objekt-Dokumentseiten für die gewählten Objekt-Elemente.

    "bei_einsatz_drucken" → alle so markierten Seiten; jeder andere Schlüssel →
    Seiten mit passender dokumentart. Nur Seiten mit vorhandenem Einzel-PDF
    (einzel_pdf_pfad), da der Renderer (print_artifact_service) dieses lädt.
    """
    from sqlalchemy import or_

    from app.models.objekt import ObjektDokumentSeite

    dokumentarten = [e for e in elements if e != "bei_einsatz_drucken"]
    bedingungen = []
    if "bei_einsatz_drucken" in elements:
        bedingungen.append(ObjektDokumentSeite.bei_einsatz_drucken.is_(True))
    if dokumentarten:
        bedingungen.append(ObjektDokumentSeite.dokumentart.in_(dokumentarten))
    if not bedingungen:
        return []
    return (
        db.query(ObjektDokumentSeite)
        .filter(
            ObjektDokumentSeite.objekt_id == objekt_id,
            ObjektDokumentSeite.einzel_pdf_pfad.isnot(None),
            or_(*bedingungen),
        )
        .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
        .all()
    )


def _jobs_for_rule(
    db: Session, gateway, rule, context: dict, *, source: str = JOB_SOURCE_RULE,
) -> list[PrintJob]:
    """Erzeugt Jobs einer Regel: je Dokument × Zieldrucker sowie – bei zugeordnetem
    Objekt – je Objekt-Element-Seite × Zieldrucker (idempotent, außer bei source=manual)."""
    from app.models.gateway import DOC_OBJEKT_DOKUMENT

    jobs: list[PrintJob] = []
    printer_ids = rule.printer_ids or []
    if not printer_ids:
        return jobs

    def _add(**kw):
        job, created = create_print_job(
            db, org_id=rule.org_id, gateway_id=gateway.id, source=source,
            rule_id=rule.id, incident_id=context.get("incident_id"),
            gsl_id=context.get("gsl_id"), options=rule.options or {}, **kw,
        )
        if created:
            jobs.append(job)

    # 1) Dokumenttypen (Einsatzinfo, GSL-Lageblatt, Objektblatt …)
    for document_type in (rule.documents or []):
        for printer_id in printer_ids:
            _add(printer_id=printer_id, document_type=document_type, objekt_id=context.get("objekt_id"))

    # 2) Objekt-Elemente → konkrete Objekt-Dokumentseiten des zugeordneten Objekts
    objekt_elements = rule.objekt_elements or []
    if objekt_elements:
        for objekt_id in _resolve_objekt_ids(db, context):
            for seite in _seiten_for_elements(db, objekt_id, objekt_elements):
                for printer_id in printer_ids:
                    _add(printer_id=printer_id, document_type=DOC_OBJEKT_DOKUMENT,
                         objekt_id=objekt_id, artifact_ref=str(seite.id))
    return jobs


def build_test_jobs(db: Session, rule, incident) -> list[PrintJob]:
    """„Testdruck dieser Regel": erzeugt die Jobs der Regel gegen einen echten Einsatz,
    unabhängig von Trigger/aktiv/Filter, mit source=manual (immer neu, nie dedupliziert).
    Der Aufrufer committet und dispatcht. Gibt [] zurück, wenn kein Gateway/keine Drucker."""
    from app.models.gateway import Gateway

    gateway = (
        db.query(Gateway)
        .filter(Gateway.org_id == rule.org_id, Gateway.device_token_hash.isnot(None))
        .first()
    )
    if gateway is None:
        return []
    context = {
        "incident_id": incident.id,
        "stichwort": getattr(incident, "reason", None) or getattr(incident, "report_text", None),
    }
    jobs = _jobs_for_rule(db, gateway, rule, context, source=JOB_SOURCE_MANUAL)
    if jobs:
        db.flush()
    return jobs
