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
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from app.models.gateway import (
    JOB_DONE,
    JOB_FAILED,
    JOB_PRINTING,
    JOB_SENT,
    JOB_SOURCE_MANUAL,
    JOB_SOURCE_RULE,
    PrintJob,
)

if TYPE_CHECKING:
    from app.models.gateway import Gateway

logger = logging.getLogger("einsatzleiter.print")


class TestBezug(NamedTuple):
    context: dict
    art: str
    ref_id: int


TRIGGER_TEST_BEZUG = {
    "einsatz_created": "einsatz",
    "einsatz_created_gsl": "gsl_einsatz",
    "einsatz_updated": "einsatz",
    "manual_only": "einsatz",
    "gsl_created": "gsl",
    "gsl_lage_updated": "gsl",
    "verleih_created": "verleih",
    "gsl_einheit_angelegt": "gsl_einheit",
    "gsl_stelle_disponiert": "gsl_stelle",
    "alarm_serial_received": "alarm",
}


def objekt_element_optionen(db: Session, org_id: int) -> dict[str, str]:
    """Aktive Objekt-Druckelemente der Organisation mit dem Systemelement."""
    from app.models.gateway import OBJEKT_ELEMENT_LABELS
    from app.models.objekt import AUSWAHL_DOKUMENTART
    from app.services.objekt_service import lade_auswahl

    return {
        "bei_einsatz_drucken": OBJEKT_ELEMENT_LABELS["bei_einsatz_drucken"],
        **lade_auswahl(db, org_id, AUSWAHL_DOKUMENTART),
    }


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
    fallback_of: int | None = None,
    idempotenz_zusatz: str | None = None,
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
    if fallback_of is not None:
        parts.append(f"fb{fallback_of}")
    if idempotenz_zusatz is not None:
        parts.append(idempotenz_zusatz)
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
    fallback_of_job_id: int | None = None,
    idempotenz_zusatz: str | None = None,
    request_key: str | None = None,
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
        fallback_of=fallback_of_job_id,
        idempotenz_zusatz=idempotenz_zusatz,
    )
    if request_key is not None:
        key = "manual-request:" + hashlib.sha256(f"{org_id}:{created_by_id}:{request_key}".encode()).hexdigest()
    if source != JOB_SOURCE_MANUAL or request_key is not None:
        existing = (
            db.query(PrintJob)
            .filter(PrintJob.idempotency_key == key)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if existing is not None:
            return existing, False

    from datetime import UTC, datetime

    from app.services.print_agent_service import hat_agent_routen
    agent_delivery = hat_agent_routen(db, printer_id)
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
        delivery="lease" if agent_delivery else "push",
        queued_at=datetime.now(UTC).replace(tzinfo=None) if agent_delivery else None,
        created_by_id=created_by_id,
        fallback_of_job_id=fallback_of_job_id,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(PrintJob)
            .filter(PrintJob.idempotency_key == key)
            .with_for_update()
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
    from app.services import print_agent_service
    from app.services.print_artifact_service import artifact_url, is_html_render

    # Druckziele mit Windows-Druckagent-Routen: Lease-Zustellung statt Push. Der Job wartet
    # als 'queued', bis ein bereiter Agent ihn atomar übernimmt (print_agent_service).
    if print_agent_service.hat_agent_routen(db, job.printer_id):
        return await _dispatch_job_to_agents(db, job)

    payload = {
        "job_id": job.id,
        "document_type": job.document_type,
        "printer_id": job.printer_id,
        "artifact_url": artifact_url(job),
        "options": ({k: v for k, v in (job.options or {}).items() if k in ("copies", "duplex", "color", "media")}
                    if job.document_type == "dokumentpaket" else job.options or {}),
    }
    # Leaflet-Karten: artifact_url zeigt auf eine HTML-Seite; das Gateway rendert sie
    # per Headless-Chromium (JS/Tiles) statt eine PDF herunterzuladen. Die Seitengröße
    # (A4/A3, Hoch/Quer) bestimmt das @page-CSS der Druckseite (preferCSSPageSize).
    if is_html_render(job):
        payload["render_kind"] = "html"
    # Der atomare Push-Claim und der Transport werden gemeinsam gespeichert.
    # Wiederholte Dispatcher-Aufrufe erzeugen keinen zweiten physischen Auftrag.
    db.commit()
    assert job.org_id is not None
    result = await dispatch_print_job(job.org_id, job.id, payload)
    db.refresh(job)
    return result


async def _dispatch_job_to_agents(db: Session, job: PrintJob) -> dict:
    """Reiht einen Job für Windows-Druckagents ein und weckt die zuständigen Agents."""
    from app.routers.ws import notify_agents_job_available
    from app.services import print_agent_service

    assert job.org_id is not None  # create_print_job() setzt org_id immer
    gateway_ids = print_agent_service.enqueue_for_agents(db, job)
    await notify_agents_job_available(job.org_id, gateway_ids, [job.id])
    logger.info("Druckauftrag %s für Druckagents %s eingereiht", job.id, gateway_ids)
    return {"job_id": job.id, "status": job.status, "error": None}


def mark_job_failed(db: Session, job: PrintJob, grund: str) -> bool:
    """Setzt einen Job auf 'failed' mit Begruendung. Idempotent und terminal-sicher.

    True, wenn der Status gewechselt hat.
    """
    from app.models.gateway import JOB_TERMINAL, JOB_UNCERTAIN

    job = db.query(PrintJob).filter(PrintJob.id == job.id, PrintJob.org_id == job.org_id).with_for_update(
    ).populate_existing().one()
    if job.status in JOB_TERMINAL | {JOB_UNCERTAIN}:
        return False
    job.status = JOB_UNCERTAIN if job.phase in ("submitting", "spooled") or (
        job.delivery == "push" and job.status in ("sent", "printing") and not job.lease_token
    ) else JOB_FAILED
    job.error = grund[:500]
    db.commit()
    return True


def escalate_stale_job(db: Session, job_id: int, grund: str) -> bool:
    """Uebernimmt einen haengenden Job atomar auf 'failed'. True nur fuer den Gewinner.

    Bedingtes UPDATE statt Python-Pruefung: der Watchdog laeuft ohne Leader-Guard in
    jedem Worker, ein Check-then-commit wuerde doppelt zuschlagen. Das Bulk-UPDATE
    ist trotz Tenant-Tabelle zulaessig, weil der Filter ueber den eindeutigen
    Primaerschluessel genau eine Zeile adressiert.
    """
    from app.models.gateway import JOB_UNCERTAIN
    job = db.get(PrintJob, job_id)
    if job is None:
        return False
    treffer = db.query(PrintJob).filter(PrintJob.id == job_id, PrintJob.org_id == job.org_id,
        PrintJob.status.in_((JOB_SENT, JOB_PRINTING)), PrintJob.aktualisiert_am == job.aktualisiert_am).update(
            {"status": JOB_UNCERTAIN, "error": grund[:500]}, synchronize_session=False)

    db.commit()
    return treffer == 1


async def dispatch_fallback_for_failed_job(job_id: int) -> PrintJob | None:
    """Legt nach einem gemeldeten Fehlschlag genau einen Ersatz-Job an und stellt ihn zu.

    Eigene Session, best-effort.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import Printer, PrintRule

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = db.get(PrintJob, job_id)
        if job is None or job.status != JOB_FAILED or job.source != JOB_SOURCE_RULE or job.rule_id is None:
            return None
        if job.fallback_of_job_id is not None:
            return None
        rule = db.get(PrintRule, job.rule_id)
        if rule is None or rule.org_id != job.org_id or not rule.fallback_printer_id:
            return None
        if rule.fallback_printer_id == job.printer_id:
            return None
        printer = db.get(Printer, rule.fallback_printer_id)
        if printer is None or printer.org_id != job.org_id or not printer.aktiv:
            logger.warning(
                "Fallback-Drucker %s fuer Druckauftrag %s nicht verfuegbar",
                rule.fallback_printer_id, job.id,
            )
            return None
        assert job.org_id is not None

        options = dict(job.options or {})
        media = options.get("media")
        supported_media = (printer.capabilities or {}).get("media") or ["A4"]
        if media and media not in supported_media:
            options.pop("media", None)
        ersatz, created = create_print_job(
            db,
            org_id=job.org_id,
            gateway_id=printer.gateway_id,
            printer_id=printer.id,
            document_type=job.document_type,
            source=job.source,
            rule_id=job.rule_id,
            incident_id=job.incident_id,
            gsl_id=job.gsl_id,
            objekt_id=job.objekt_id,
            artifact_ref=job.artifact_ref,
            options=options,
            created_by_id=job.created_by_id,
            fallback_of_job_id=job.id,
        )
        if not created:
            return None
        db.commit()
        logger.info(
            "Ersatz-Druckauftrag %s fuer fehlgeschlagenen Auftrag %s angelegt",
            ersatz.id, job.id,
        )
        await dispatch_job(db, ersatz)
        return ersatz
    except Exception as exc:  # pragma: no cover - best-effort Hintergrundpfad
        logger.warning("Fallback fuer Druckauftrag %s fehlgeschlagen: %s", job_id, exc)
        return None
    finally:
        db.close()


# ── Domain-Events → Druckregeln (Phase 4) ──────────────────────────────────────

def on_event(db: Session, org_id: int, trigger: str, context: dict) -> list[PrintJob]:
    """Wertet alle aktiven PrintRules für (org, trigger) aus und legt Jobs an.

    context: {incident_id?, gsl_id?, objekt_id?, alarmstufe?, stichwort?, nur_bma?}
    Gibt die neu angelegten Jobs zurück (Dispatch erfolgt separat/asynchron).
    Verbindet sich kein Gateway, bleiben die Jobs 'queued'.
    """
    from app.models.gateway import PrintRule
    from app.services.exercise_guard import darf_extern
    from app.services.gateway_service import gateway_effective_enabled

    if not darf_extern(
        "autoprint",
        is_exercise=bool(context.get("is_exercise")),
        org_id=org_id,
        db=db,
        planspiel_run_id=context.get("planspiel_run_id"),
    ):
        return []

    if not gateway_effective_enabled(org_id, db):
        return []

    gateway = paired_gateway(db, org_id)
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
    from app.models.gateway import (
        TRIGGER_EINSATZ_CREATED,
        TRIGGER_EINSATZ_CREATED_GSL,
        TRIGGER_EINSATZ_UPDATED,
        TRIGGER_GSL_STELLE_DISPONIERT,
        TRIGGER_MAIL_EINGANG,
    )

    f = rule.filters or {}
    if rule.trigger == TRIGGER_MAIL_EINGANG:
        if f.get("mail_nur_zugeordnet") and not context.get("mail_assigned"):
            return False
        if f.get("mail_nur_freigegeben") and not context.get("mail_sender_approved"):
            return False
    if rule.trigger == TRIGGER_GSL_STELLE_DISPONIERT:
        if f.get("stelle_nur_erste_disposition") and not context.get("erste_disposition"):
            return False
        auftragsart = f.get("stelle_auftragsart", "alle")
        if auftragsart in ("erkundung", "abarbeitung") and context.get("auftragsart") != auftragsart:
            return False
    hat_einsatzbezug = rule.trigger in (
        TRIGGER_EINSATZ_CREATED, TRIGGER_EINSATZ_CREATED_GSL, TRIGGER_EINSATZ_UPDATED,
    )
    alarmstufen = f.get("alarmstufen")
    if hat_einsatzbezug and isinstance(alarmstufen, list):
        alarmstufen = {str(code).strip().upper() for code in alarmstufen if str(code).strip()}
        alarm_code = context.get("alarm_code")
        alarm_code = alarm_code.strip().upper() if isinstance(alarm_code, str) else None
        if alarmstufen and alarm_code not in alarmstufen:
            return False
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


def effektive_regel_einstellungen(rule, context: dict) -> tuple[list[str], list[str], dict]:
    """Grundwerte einer Druckregel samt optionaler Abweichung je Alarmstufe."""
    documents = list(rule.documents or [])
    objekt_elements = list(rule.objekt_elements or [])
    optionen_roh = rule.options or {}
    options = {key: value for key, value in optionen_roh.items() if key != "je_alarmstufe"}
    alarm_code = context.get("alarm_code")
    if not isinstance(alarm_code, str) or not alarm_code.strip():
        return documents, objekt_elements, options
    abweichungen = optionen_roh.get("je_alarmstufe", {})
    if not isinstance(abweichungen, dict):
        return documents, objekt_elements, options
    abweichung = next(
        (wert for code, wert in abweichungen.items()
         if str(code).strip().upper() == alarm_code.strip().upper() and isinstance(wert, dict)),
        None,
    )
    if abweichung is None:
        return documents, objekt_elements, options
    copies = abweichung.get("copies")
    if isinstance(copies, int) and copies >= 1:
        options["copies"] = copies
    if abweichung.get("media") in ("A3", "A4"):
        options["media"] = abweichung["media"]
    if isinstance(abweichung.get("documents"), list):
        documents = abweichung["documents"]
    if isinstance(abweichung.get("objekt_elements"), list):
        objekt_elements = abweichung["objekt_elements"]
    return documents, objekt_elements, options


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
        "planspiel_run_id": inc.planspiel_run_id,
        "alarmstufe": int(treffer.group()) if treffer else None,
        "alarm_code": alarm_code.strip().upper() or None,
        "nur_bma": nur_bma,
    }


def _gsl_context(lage) -> dict:
    """Vollständiger Filterkontext einer Großschadenslage."""
    return {
        "gsl_id": lage.id,
        "stichwort": lage.name,
        "is_exercise": getattr(lage, "is_exercise", None),
        "planspiel_run_id": lage.planspiel_run_id,
    }


def paired_gateway(db: Session, org_id: int) -> Gateway | None:
    """Erstes gekoppeltes Gateway einer Organisation."""
    from app.models.gateway import Gateway

    return (
        db.query(Gateway)
        .filter(Gateway.org_id == org_id, Gateway.device_token_hash.isnot(None))
        .first()
    )


def resolve_test_context(db: Session, rule) -> TestBezug | None:
    """Loest den juengsten zur Regel passenden Testbezug derselben Organisation auf."""
    from app.models.gateway import AlarmIngest
    from app.models.incident import Incident
    from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, MajorIncident
    from app.models.verleih import VerleihAusleihe

    art = TRIGGER_TEST_BEZUG.get(rule.trigger)
    if art == "einsatz":
        inc = (
            db.query(Incident)
            .filter(Incident.primary_org_id == rule.org_id)
            .order_by(Incident.started_at.desc())
            .first()
        )
        return TestBezug(_incident_context(inc), art, inc.id) if inc else None
    if art == "gsl_einsatz":
        pair = (
            db.query(Incident, IncidentSite, MajorIncident)
            .join(IncidentSite, IncidentSite.incident_id == Incident.id)
            .join(MajorIncident, MajorIncident.id == IncidentSite.major_incident_id)
            .filter(
                Incident.primary_org_id == rule.org_id,
                IncidentSite.org_id == rule.org_id,
                MajorIncident.org_id == rule.org_id,
                MajorIncident.status == "active",
            )
            .order_by(Incident.started_at.desc(), Incident.id.desc())
            .first()
        )
        if pair is None:
            return None
        inc, gsl_site, gsl_lage = pair
        context = _incident_context(inc) | {"gsl_id": gsl_lage.id, "site_id": gsl_site.id}
        return TestBezug(context, art, inc.id)
    if art == "gsl":
        lage = (
            db.query(MajorIncident)
            .filter(MajorIncident.org_id == rule.org_id)
            .order_by(MajorIncident.started_at.desc())
            .first()
        )
        return TestBezug(_gsl_context(lage), art, lage.id) if lage else None
    if art == "gsl_einheit":
        einheit = (db.query(LageEinheit).join(MajorIncident, LageEinheit.lage_id == MajorIncident.id)
                   .filter(MajorIncident.org_id == rule.org_id).order_by(LageEinheit.id.desc()).first())
        if einheit is None:
            return None
        lage = db.get(MajorIncident, einheit.lage_id)
        if lage is None:
            return None
        return TestBezug({"gsl_id": einheit.lage_id, "einheit_id": einheit.id,
                          "is_exercise": getattr(lage, "is_exercise", None),
                          "planspiel_run_id": lage.planspiel_run_id}, art, einheit.id)
    if art == "gsl_stelle":
        dispatch = (db.query(EinheitSiteDispatch)
                    .join(IncidentSite, EinheitSiteDispatch.site_id == IncidentSite.id)
                    .join(MajorIncident, IncidentSite.major_incident_id == MajorIncident.id)
                    .filter(MajorIncident.org_id == rule.org_id)
                    .order_by(EinheitSiteDispatch.id.desc()).first())
        if dispatch is None:
            return None
        site = db.get(IncidentSite, dispatch.site_id)
        lage = db.get(MajorIncident, site.major_incident_id) if site else None
        if site is None or lage is None:
            return None
        erste = not db.query(EinheitSiteDispatch.id).filter(
            EinheitSiteDispatch.site_id == site.id, EinheitSiteDispatch.id < dispatch.id,
        ).first()
        return TestBezug({"gsl_id": lage.id, "site_id": site.id, "dispatch_id": dispatch.id,
                          "einheit_id": dispatch.einheit_id, "auftragsart": dispatch.auftragsart,
                          "is_exercise": lage.is_exercise, "planspiel_run_id": lage.planspiel_run_id,
                          "erste_disposition": erste}, art, dispatch.id)
    if art == "verleih":
        ausleihe = (
            db.query(VerleihAusleihe)
            .filter(VerleihAusleihe.org_id == rule.org_id)
            .order_by(VerleihAusleihe.created_at.desc())
            .first()
        )
        if ausleihe is None:
            return None
        lage = (
            db.query(MajorIncident)
            .filter(MajorIncident.id == ausleihe.lage_id, MajorIncident.org_id == rule.org_id)
            .first()
            if ausleihe.lage_id else None
        )
        context = {
            "gsl_id": ausleihe.lage_id,
            "ausleihe_id": ausleihe.id,
            "is_exercise": getattr(lage, "is_exercise", None),
            "planspiel_run_id": lage.planspiel_run_id if lage else None,
        }
        return TestBezug(context, art, ausleihe.id)
    if art == "alarm":
        ingest = (
            db.query(AlarmIngest)
            .filter(AlarmIngest.org_id == rule.org_id)
            .order_by(AlarmIngest.received_at.desc())
            .first()
        )
        if ingest is None:
            return None
        return TestBezug(
            {"alarm_ingest_id": ingest.id, "incident_id": ingest.einsatz_id}, art, ingest.id,
        )
    return None


async def autoprint_incident_background(incident_id: int, trigger: str | None = None) -> None:
    """Background-Hook fuer Einsatz-Aenderungen: wertet passende Druckregeln aus
    und stellt die Jobs zu. Best-effort – Fehler dürfen den Request nie beeinflussen."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_EINSATZ_CREATED, TRIGGER_EINSATZ_CREATED_GSL
    from app.models.incident import Incident
    from app.models.major_incident import IncidentSite, MajorIncident

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        inc = db.get(Incident, incident_id)
        if inc is None or inc.primary_org_id is None:
            return
        org_id = inc.primary_org_id
        set_tenant_context(db, org_id)
        effektiver_trigger = trigger or TRIGGER_EINSATZ_CREATED
        context = _incident_context(inc)
        if effektiver_trigger == TRIGGER_EINSATZ_CREATED:
            site_lage = (
                db.query(IncidentSite, MajorIncident)
                .join(MajorIncident, MajorIncident.id == IncidentSite.major_incident_id)
                .filter(
                    IncidentSite.incident_id == inc.id,
                    IncidentSite.org_id == org_id,
                    MajorIncident.org_id == org_id,
                    MajorIncident.status == "active",
                )
                .first()
            )
            if site_lage is not None:
                site, lage = site_lage
                effektiver_trigger = TRIGGER_EINSATZ_CREATED_GSL
                context |= {"gsl_id": lage.id, "site_id": site.id}
        jobs = on_event(db, org_id, effektiver_trigger, context)
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


async def autoprint_incident_updated_background(incident_id: int) -> None:
    """Duennner BackgroundTasks-Wrapper fuer den Nachzuegler-Ausloeser."""
    from app.models.gateway import TRIGGER_EINSATZ_UPDATED
    await autoprint_incident_background(incident_id, TRIGGER_EINSATZ_UPDATED)


async def autoprint_gsl_background(
    lage_id: int, org_id: int, trigger: str | None = None,
) -> None:
    """Background-Hook fuer Anlage oder Aenderung einer Großschadenslage."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_GSL_CREATED
    from app.models.major_incident import MajorIncident

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = (
            db.query(MajorIncident)
            .filter(MajorIncident.id == lage_id, MajorIncident.org_id == org_id)
            .first()
        )
        if lage is None or lage.org_id is None:
            return
        set_tenant_context(db, lage.org_id)
        jobs = on_event(db, lage.org_id, trigger or TRIGGER_GSL_CREATED, _gsl_context(lage))
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


async def autoprint_gsl_updated_background(lage_id: int, org_id: int) -> None:
    """Duennner BackgroundTasks-Wrapper fuer den GSL-Nachzuegler-Ausloeser."""
    from app.models.gateway import TRIGGER_GSL_LAGE_UPDATED
    await autoprint_gsl_background(lage_id, org_id, TRIGGER_GSL_LAGE_UPDATED)


async def autoprint_alarm_background(alarm_ingest_id: int) -> None:
    """Wertet den Regel-Ausloeser fuer genau einen neuen seriellen Alarm aus."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_ALARM_SERIAL, AlarmIngest

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        ingest = db.get(AlarmIngest, alarm_ingest_id)
        if ingest is None or ingest.org_id is None:
            return
        set_tenant_context(db, ingest.org_id)
        jobs = on_event(db, ingest.org_id, TRIGGER_ALARM_SERIAL, {
            "alarm_ingest_id": ingest.id,
            "incident_id": ingest.einsatz_id,
        })
        db.commit()
        for job in jobs:
            try:
                await dispatch_job(db, job)
            except Exception as exc:  # pragma: no cover
                logger.warning("Alarm-Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Auto-Druck fehlgeschlagen (Alarm %s): %s", alarm_ingest_id, exc)
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
            "planspiel_run_id": lage.planspiel_run_id if lage else None,
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


async def autoprint_gsl_einheit_background(einheit_id: int) -> None:
    """Best-effort-Regeldruck nach Anlage einer GSL-Einheit."""

    from datetime import UTC, datetime

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_GSL_EINHEIT_ANGELEGT
    from app.models.major_incident import LageEinheit, LageEinheitZugang, MajorIncident
    from app.services import gk_zugang_service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = db.get(LageEinheit, einheit_id)
        if einheit is None:
            return
        lage = db.get(MajorIncident, einheit.lage_id)
        if lage is None or lage.org_id is None:
            return
        set_tenant_context(db, lage.org_id)
        try:
            qr = gk_zugang_service.stelle_qr_zugang_aus(db, lage, einheit, user_id=None,
                                                         grund="autodruck", neu=False)
        except ValueError:
            logger.info("QR-Autodruck für Einheit %s übersprungen: QR-Zugang nicht aktiviert", einheit.id)
            return
        context = {"gsl_id": lage.id, "einheit_id": einheit.id, "qr_generation": qr.generation,
                   "is_exercise": lage.is_exercise, "planspiel_run_id": lage.planspiel_run_id}
        jobs = on_event(db, lage.org_id, TRIGGER_GSL_EINHEIT_ANGELEGT, context)
        if jobs:
            zugang = db.query(LageEinheitZugang).filter(LageEinheitZugang.id == qr.zugang_id).one()
            if zugang.qr_druck_job_id is None:
                zugang.qr_druck_job_id = jobs[0].id
                zugang.qr_druck_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
        for job in jobs:
            try:
                await dispatch_job(db, job)
            except Exception as exc:  # pragma: no cover
                logger.warning("QR-Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("QR-Auto-Druck fehlgeschlagen (Einheit %s): %s", einheit_id, exc)
    finally:
        db.close()


async def autoprint_gsl_stelle_disponiert_background(dispatch_id: int) -> None:
    """Best-effort-Regeldruck nach einer neuen Einsatzstellen-Disposition."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_GSL_STELLE_DISPONIERT
    from app.models.major_incident import EinheitSiteDispatch, IncidentSite, MajorIncident

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dispatch = db.get(EinheitSiteDispatch, dispatch_id)
        if dispatch is None:
            return
        site = db.get(IncidentSite, dispatch.site_id)
        if site is None:
            return
        lage = db.get(MajorIncident, site.major_incident_id)
        if lage is None or lage.org_id is None:
            return
        set_tenant_context(db, lage.org_id)
        erste_disposition = not db.query(EinheitSiteDispatch.id).filter(
            EinheitSiteDispatch.site_id == site.id,
            EinheitSiteDispatch.id < dispatch.id,
        ).first()
        jobs = on_event(db, lage.org_id, TRIGGER_GSL_STELLE_DISPONIERT, {
            "gsl_id": lage.id,
            "site_id": site.id,
            "dispatch_id": dispatch.id,
            "einheit_id": dispatch.einheit_id,
            "auftragsart": dispatch.auftragsart,
            "is_exercise": lage.is_exercise,
            "planspiel_run_id": lage.planspiel_run_id,
            "erste_disposition": erste_disposition,
        })
        db.commit()
        for job in jobs:
            try:
                await dispatch_job(db, job)
            except Exception as exc:  # pragma: no cover
                logger.warning("Einsatzstellen-Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Einsatzstellen-Auto-Druck fehlgeschlagen (Disposition %s): %s", dispatch_id, exc)
    finally:
        db.close()


async def autoprint_mail_eingang_background(eingang_id: int) -> None:
    """Best-effort-Regeldruck einer angenommenen Eingangsmail, erst nach Commit."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import TRIGGER_MAIL_EINGANG
    from app.models.master import OrgSettings
    from app.models.org_mail import OrgMailEingang
    from app.services.mail_zuordnung_service import _allowed

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(OrgMailEingang, eingang_id)
        if row is None or row.org_id is None or row.zuordnung == "abgelehnt":
            return
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == row.org_id).first()
        if settings is None or settings.mail_eingang_modus == "aus":
            return
        incident = None
        if row.incident_id:
            from app.models.incident import Incident
            incident = db.get(Incident, row.incident_id)
            if incident is None or incident.primary_org_id != row.org_id or incident.is_exercise:
                return
        approved, _ = _allowed(row, settings)
        set_tenant_context(db, row.org_id)
        jobs = on_event(db, row.org_id, TRIGGER_MAIL_EINGANG, {
            "mail_eingang_id": row.id, "incident_id": row.incident_id,
            "is_exercise": bool(getattr(incident, "is_exercise", False)),
            "planspiel_run_id": incident.planspiel_run_id if incident else None,
            "mail_assigned": bool(row.incident_id), "mail_sender_approved": approved,
        })
        db.commit()
        if jobs:
            try:
                from app.services.mail_eingang_service import ereignis
                ereignis(db, row, "gedruckt")
                db.commit()
            except Exception:
                db.rollback()
        for job in jobs:
            try:
                await dispatch_job(db, job)
            except Exception as exc:  # pragma: no cover
                logger.warning("Mail-Auto-Druck Job %s nicht zustellbar: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Mail-Auto-Druck fehlgeschlagen (Mail %s): %s", eingang_id, exc)
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

    from app.models.objekt import ObjektDokument, ObjektDokumentSeite

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
        .options(defer(ObjektDokumentSeite.volltext))
        .join(ObjektDokument, ObjektDokumentSeite.dokument_id == ObjektDokument.id)
        .filter(
            ObjektDokumentSeite.objekt_id == objekt_id,
            ObjektDokumentSeite.einzel_pdf_pfad.isnot(None),
            ObjektDokument.ist_aktuelle_version.is_(True),
            ObjektDokument.freigabe_status == "freigegeben",
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
        DOC_ALARM_ROHTEXT,
        DOC_GSL_EINHEIT_QR,
        DOC_MAIL_EINGANG,
        DOC_OBJEKT_DOKUMENT,
        DOC_OBJEKTBLATT,
        DOC_SITE_KARTE,
        DOC_VERLEIH_SCHEIN,
        TRIGGER_DOCUMENT_TYPES,
        Printer,
    )

    jobs: list[PrintJob] = []
    documents, objekt_elements, regel_optionen = effektive_regel_einstellungen(rule, context)
    printer_ids = rule.printer_ids or []
    if not printer_ids:
        return jobs

    def _add(**kw):
        printer = db.get(Printer, kw["printer_id"])
        defaults = {
            key: value for key, value in (printer.defaults or {}).items()
            if key in ("duplex", "color", "media")
        } if printer is not None else {}
        options = defaults | regel_optionen
        job, created = create_print_job(
            db, org_id=rule.org_id, gateway_id=gateway.id, source=source,
            rule_id=rule.id, incident_id=context.get("incident_id"),
            gsl_id=context.get("gsl_id"), options=options,
            idempotenz_zusatz=(f"d{context['dispatch_id']}" if context.get("dispatch_id") else None),
            **kw,
        )
        if created:
            jobs.append(job)

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
        if document_type == DOC_GSL_EINHEIT_QR:
            if not context.get("einheit_id") or context.get("qr_generation") is None:
                continue
            for printer_id in printer_ids:
                _add(printer_id=printer_id, document_type=document_type,
                     artifact_ref=f"{context['einheit_id']}:{context['qr_generation']}")
            continue
        if document_type == DOC_MAIL_EINGANG:
            mail_id = context.get("mail_eingang_id")
            if not mail_id:
                continue
            for printer_id in printer_ids:
                _add(printer_id=printer_id, document_type=document_type, artifact_ref=str(mail_id))
            continue
        if document_type == DOC_SITE_KARTE:
            site_id = context.get("site_id")
            if not site_id or not context.get("gsl_id"):
                continue
            for printer_id in printer_ids:
                _add(printer_id=printer_id, document_type=document_type, artifact_ref=str(site_id))
            continue
        if document_type == DOC_ALARM_ROHTEXT:
            # Ohne AlarmIngest-Bezug wuerde _render_alarm_rohtext ein leeres Blatt
            # erzeugen. Der Schluessel fehlt bei jedem nicht-Alarm-Kontext (z.B.
            # Testdruck einer Altbestand-Regel, deren Dokumente nicht zum Ausloeser
            # passen) - dann den Dokumenttyp ueberspringen statt leer zu drucken.
            alarm_ingest_id = context.get("alarm_ingest_id")
            if not alarm_ingest_id:
                logger.warning(
                    "Alarm-Rohtext für Druckregel %s ohne Alarm-Bezug übersprungen", rule.id,
                )
                continue
            for printer_id in printer_ids:
                _add(
                    printer_id=printer_id,
                    document_type=document_type,
                    artifact_ref=str(alarm_ingest_id),
                )
            continue
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
    if objekt_elements and DOC_OBJEKT_DOKUMENT in erlaubte_typen:
        for objekt_id in _resolve_objekt_ids(db, context):
            for seite in _seiten_for_elements(db, objekt_id, objekt_elements):
                for printer_id in printer_ids:
                    _add(printer_id=printer_id, document_type=DOC_OBJEKT_DOKUMENT,
                         objekt_id=objekt_id, artifact_ref=str(seite.id))
    return jobs


def build_test_jobs(db: Session, rule, context: dict) -> list[PrintJob]:
    """Erzeugt Test-Jobs der Regel mit fertigem Bezugskontext."""
    gateway = paired_gateway(db, rule.org_id)
    if gateway is None:
        return []
    jobs = _jobs_for_rule(db, gateway, rule, context, source=JOB_SOURCE_MANUAL)
    if jobs:
        db.flush()
    return jobs
