"""ECPG PrintDispatcher – Kernstück der Automatik.

- create_print_job: idempotente Job-Anlage (Dedup je Quelle/Regel/Dokument/Drucker)
- dispatch_job: PDF-URL signieren, ans Gateway senden, Status setzen
- on_event: Domain-Events (Einsatz/GSL/Alarm) → PrintRules auswerten (Phase 4)
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.gateway import (
    JOB_DONE,
    JOB_FAILED,
    JOB_SENT,
    JOB_SOURCE_MANUAL,
    JOB_SOURCE_RULE,
    PrintJob,
)

logger = logging.getLogger("einsatzleiter.print")


def unfulfilled_print_jobs(db: Session, incident_id: int) -> list[PrintJob]:
    """Bereits angelegte Regel-Jobs eines Einsatzes, die noch nicht fertig sind."""
    return (
        db.query(PrintJob)
        .filter(
            PrintJob.incident_id == incident_id,
            PrintJob.source == JOB_SOURCE_RULE,
            PrintJob.status != JOB_DONE,
        )
        .all()
    )


# ── Idempotenz ─────────────────────────────────────────────────────────────────

def build_idempotency_key(
    *,
    org_id: int,
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
        str(org_id),
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
        org_id=org_id, source=source, rule_id=rule_id, incident_id=incident_id, gsl_id=gsl_id,
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
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(PrintJob)
            .filter(PrintJob.idempotency_key == key)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if existing is not None:
            return existing, False
        raise
    return job, True


# ── Zustellung ─────────────────────────────────────────────────────────────────

async def dispatch_job(db: Session, job: PrintJob) -> dict:
    """Sendet einen Job ans Gateway (signierte PDF-URL + Druckoptionen).

    Setzt job.status auf 'sent' bei erfolgreicher Übergabe. Der Endstatus
    (printing/done/failed) kommt asynchron per job_status vom Gateway zurück.
    """
    from app.routers.ws import dispatch_print_job
    from app.services.print_artifact_service import artifact_url, is_html_render

    payload = {
        "job_id": job.id,
        "document_type": job.document_type,
        "printer_id": job.printer_id,
        "artifact_url": artifact_url(job),
        "options": job.options or {},
    }
    # Leaflet-Karten: artifact_url zeigt auf eine HTML-Seite; das Gateway rendert sie
    # per Headless-Chromium (JS/Tiles) statt eine PDF herunterzuladen. Die Seitengröße
    # (A4/A3, Hoch/Quer) bestimmt das @page-CSS der Druckseite (preferCSSPageSize).
    if is_html_render(job):
        payload["render_kind"] = "html"
    # attempts + 'sent' VOR dem Await committen und danach den Status NICHT erneut
    # schreiben: Den Laufzeit-Status (printing/done/failed) besitzt der Gateway-Callback
    # `_apply_job_status` (eigene Session). Ein zweiter Commit hier nach dem Await
    # kollidierte mit diesem Callback auf derselben print_job-Zeile → MariaDB-Fehler
    # 1020 „Record has changed since last read" (Prod-Log 2026-07-08).
    job.attempts = (job.attempts or 0) + 1
    job.status = JOB_SENT
    db.commit()
    assert job.org_id is not None  # create_print_job() setzt org_id immer
    try:
        result = await dispatch_print_job(job.org_id, job.id, payload)
    except RuntimeError as exc:
        db.refresh(job)
        job.status = JOB_FAILED
        job.error = str(exc)[:500]
        db.commit()
        logger.warning("Druckauftrag %s nicht zustellbar: %s", job.id, exc)
        return {"job_id": job.id, "status": JOB_FAILED, "error": str(exc)}

    # Erfolgreich übergeben. Endstatus kommt asynchron via job_status → _apply_job_status.
    return {"job_id": job.id, "status": result.get("status") or JOB_SENT,
            "error": result.get("error")}


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
    from app.models.gateway import TRIGGER_EINSATZ_CREATED, TRIGGER_EINSATZ_UPDATED

    f = rule.filters or {}
    hat_einsatzbezug = rule.trigger in (TRIGGER_EINSATZ_CREATED, TRIGGER_EINSATZ_UPDATED)
    uebung = f.get("uebung", "alle")
    if uebung in ("nur_echt", "nur_uebung"):
        is_exercise = context.get("is_exercise")
        if is_exercise is None:
            logger.warning("Druckregel %s übersprungen: Übungsstatus im Kontext fehlt", rule.id)
            return False
        if uebung == "nur_echt" and is_exercise:
            return False
        if uebung == "nur_uebung" and not is_exercise:
            return False
    min_stufe = f.get("min_alarmstufe")
    if min_stufe is not None and hat_einsatzbezug:
        if context.get("alarmstufe") is None:
            return False
        try:
            if int(context["alarmstufe"]) < int(min_stufe):
                return False
        except (ValueError, TypeError):
            return False
    stichworte = f.get("stichwort") or []
    if stichworte and context.get("stichwort"):
        if not any(s.lower() in str(context["stichwort"]).lower() for s in stichworte):
            return False
    if f.get("nur_bma") and hat_einsatzbezug and not context.get("nur_bma"):
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


def _incident_context(inc) -> dict:
    """Vollständiger Filterkontext eines Einsatzes."""
    alarm_code = getattr(inc, "alarm_type_code", None)
    if not isinstance(alarm_code, str):
        alarm_code = ""
    treffer = re.search(r"\d+", alarm_code)
    from sqlalchemy.orm.exc import UnmappedInstanceError

    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    try:
        session = Session.object_session(inc)
    except UnmappedInstanceError:
        session = None
    objekt_links = [] if session is None else (
        session.query(ObjektEinsatz)
        .filter(
            ObjektEinsatz.incident_id == inc.id,
            ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
        )
        .all()
    )
    nur_bma = any(
        link.objekt and link.objekt.bma and link.objekt.bma.bma_nummer
        for link in objekt_links
    )
    return {
        "incident_id": inc.id,
        "stichwort": getattr(inc, "reason", None) or getattr(inc, "report_text", None),
        "is_exercise": getattr(inc, "is_exercise", None),
        "alarmstufe": int(treffer.group()) if treffer else None,
        "nur_bma": nur_bma,
    }


def _gsl_context(lage) -> dict:
    """Vollständiger Filterkontext einer Großschadenslage."""
    return {
        "gsl_id": lage.id,
        "stichwort": lage.name,
        "is_exercise": getattr(lage, "is_exercise", None),
    }


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
        context = _incident_context(inc)
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


async def autoprint_gsl_background(lage_id: int) -> None:
    """Background-Hook nach Anlage einer Großschadenslage."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_GSL_CREATED
    from app.models.major_incident import MajorIncident

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        if lage is None or lage.org_id is None:
            return
        set_tenant_context(db, lage.org_id)
        jobs = on_event(db, lage.org_id, TRIGGER_GSL_CREATED, _gsl_context(lage))
        db.commit()
        for job in jobs:
            try:
                await dispatch_job(db, job)
            except Exception as exc:  # pragma: no cover
                logger.warning("GSL-Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("GSL-Auto-Druck fehlgeschlagen (Lage %s): %s", lage_id, exc)
    finally:
        db.close()


async def autoprint_verleih_background(ausleihe_id: int) -> None:
    """Background-Hook nach Verleihschein-Anlage; ausschliesslich regelbasiert.

    Best-effort: Fehler duerfen den Request nie beeinflussen.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_VERLEIH_CREATED
    from app.models.major_incident import MajorIncident
    from app.models.verleih import VerleihAusleihe
    from app.services.gateway_service import gateway_effective_enabled

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        a = db.get(VerleihAusleihe, ausleihe_id)
        if a is None:
            return
        org_id = a.org_id
        assert org_id is not None  # jede Ausleihe gehoert immer einer Org
        set_tenant_context(db, org_id)
        if not gateway_effective_enabled(org_id, db):
            return

        lage = db.get(MajorIncident, a.lage_id) if a.lage_id else None
        context = {
            "gsl_id": a.lage_id,
            "ausleihe_id": a.id,
            "is_exercise": getattr(lage, "is_exercise", None),
        }
        jobs = on_event(db, org_id, TRIGGER_VERLEIH_CREATED, context)

        db.commit()
        for job in jobs:
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
    from app.models.gateway import (
        DOC_OBJEKT_DOKUMENT,
        DOC_OBJEKTBLATT,
        DOC_VERLEIH_SCHEIN,
        TRIGGER_DOCUMENT_TYPES,
    )

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

    documents = rule.documents or []
    erlaubte_typen = (
        frozenset(documents) | {DOC_OBJEKT_DOKUMENT}
        if source == JOB_SOURCE_MANUAL
        else TRIGGER_DOCUMENT_TYPES.get(rule.trigger, frozenset())
    )

    # 1) Dokumenttypen (Einsatzinfo, GSL-Lageblatt, Objektblatt …)
    for document_type in documents:
        if document_type not in erlaubte_typen:
            logger.warning(
                "Dokumenttyp %s für Druckregel %s mit Auslöser %s übersprungen",
                document_type, rule.id, rule.trigger,
            )
            continue
        if document_type == DOC_VERLEIH_SCHEIN:
            continue  # braucht Vorgangs-Kontext → unten mit artifact_ref (ausleihe_id)
        if document_type == DOC_OBJEKTBLATT:
            # Objektblatt braucht immer ein konkretes Objekt: entweder explizit im
            # Kontext (z.B. manueller Testdruck fuer ein Objekt) oder - beim
            # Einsatz-Trigger - die dort bestaetigt verknuepften Objekte. Ohne diese
            # Aufloesung blieb objekt_id fuer jede Auto-Druckregel mit Objektblatt am
            # Trigger "Einsatz angelegt" leer, render_job_pdf scheiterte dann immer
            # mit "Objektblatt ohne objekt_id" (Vorfall 2026-07-13).
            objekt_ids = (
                [int(context["objekt_id"])] if context.get("objekt_id")
                else _resolve_objekt_ids(db, context)
            )
            for objekt_id in objekt_ids:
                for printer_id in printer_ids:
                    _add(printer_id=printer_id, document_type=document_type, objekt_id=objekt_id)
            continue
        for printer_id in printer_ids:
            _add(printer_id=printer_id, document_type=document_type, objekt_id=context.get("objekt_id"))

    # 1b) Verleihschein: nur sinnvoll mit ausleihe_id im Kontext (Trigger verleih_created).
    if (DOC_VERLEIH_SCHEIN in documents and DOC_VERLEIH_SCHEIN in erlaubte_typen
            and context.get("ausleihe_id")):
        for printer_id in printer_ids:
            _add(printer_id=printer_id, document_type=DOC_VERLEIH_SCHEIN,
                 artifact_ref=str(context["ausleihe_id"]))

    # 2) Objekt-Elemente → konkrete Objekt-Dokumentseiten des zugeordneten Objekts
    objekt_elements = rule.objekt_elements or []
    if objekt_elements and DOC_OBJEKT_DOKUMENT in erlaubte_typen:
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
    context = _incident_context(incident)
    jobs = _jobs_for_rule(db, gateway, rule, context, source=JOB_SOURCE_MANUAL)
    if jobs:
        db.flush()
    return jobs
