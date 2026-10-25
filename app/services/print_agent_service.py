"""Lease-basierte Zustellung von Druckaufträgen an Windows-Druckagents.

Spezifikation: docs/print-agent-architektur.md. Kernidee: Die DB ist die einzige
Wahrheit. Jede Zustandsänderung eines Agent-Jobs ist ein bedingtes Einzelzeilen-UPDATE
(Primärschlüssel + erwarteter Zustand + Lease-Token); rowcount == 1 entscheidet. Damit
ist der Ablauf sicher bei mehreren Cloud-Workern und mehreren Agents gleichzeitig.

Zweiphasige Freigabe: Ein Agent übergibt erst an den Spooler, wenn print_intent hier
bestätigt wurde (Phase submitting). Ab dann wird ein Job nie mehr automatisch umgeroutet;
reißt der Kontakt ab, wird er „uncertain“ statt erneut gedruckt.

Alle Funktionen laufen ohne User-Tenant-Kontext (Agent-WebSocket, Sweep-Loop) und filtern
deshalb explizit nach org_id (CLAUDE.md, Tenant-Scoping bei Mutationen).
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.gateway import (
    DELIVERY_LEASE,
    GATEWAY_KIND_WINDOWS_AGENT,
    GATEWAY_STATUS_ONLINE,
    JOB_CANCELED,
    JOB_DONE,
    JOB_FAILED,
    JOB_PRINTING,
    JOB_QUEUED,
    JOB_SENT,
    JOB_TERMINAL,
    JOB_UNCERTAIN,
    PHASE_FETCHING,
    PHASE_LEASED,
    PHASE_SPOOLED,
    PHASE_SUBMITTING,
    Gateway,
    Printer,
    PrinterAgentRoute,
    PrintJob,
)

logger = logging.getLogger("einsatzleiter.print_agent")

LEASE_TTL_S = 60
AGENT_ONLINE_S = 90
READINESS_FRESH_S = 180
FALLBACK_GRACE_S = 15
UNCERTAIN_AFTER_S = 600
QUEUE_MAX_WAIT_S = 180
MAX_AGENT_ATTEMPTS = 3
SWEEP_INTERVAL_S = 15

VOR_UEBERGABE = (PHASE_LEASED, PHASE_FETCHING)
NACH_UEBERGABE = (PHASE_SUBMITTING, PHASE_SPOOLED)
# Zustände, aus denen ein Agent-Status keinen Übergang mehr auslösen darf.
AGENT_ENDZUSTAENDE = tuple(JOB_TERMINAL | {JOB_UNCERTAIN})

FEHLER_UNKLAR = "Keine Rückmeldung des Druckagents nach Druckübergabe – bitte am Drucker prüfen"
FEHLER_KEIN_AGENT = "Kein Druckagent bereit"
FEHLER_MEHRFACH = "Druckagent hat den Auftrag mehrfach nicht übernommen"


def _jetzt(now: datetime | None) -> datetime:
    return now or datetime.now(UTC).replace(tzinfo=None)


def _zeit_aus_json(value: object) -> datetime | None:
    """ISO-Zeitstempel (z. B. readiness.checked_at) robust als naive UTC lesen."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _norm(value: object) -> str:
    return str(value).lower().replace(":", "").replace("-", "").replace(" ", "")


def identitaet_passt(soll: dict | None, ist: dict | None) -> bool:
    """Vergleicht Soll- und Ist-Identität eines Druckers.

    Erst der erste gemeinsame starke Schlüssel (serial, uuid, mac), sonst ip/host. Ist
    nichts vergleichbar (USB-, Datei- oder Befehlsziel), gilt die Identität als passend.
    """
    soll = soll or {}
    ist = ist or {}
    for key in ("serial", "uuid", "mac", "ip", "host"):
        if soll.get(key) and ist.get(key):
            return _norm(soll[key]) == _norm(ist[key])
    return True


def _aktive_routen(db: Session, org_id: int | None, *, printer_id: int | None = None,
                   gateway_id: int | None = None) -> list[PrinterAgentRoute]:
    query = (
        db.query(PrinterAgentRoute)
        .filter(PrinterAgentRoute.org_id == org_id, PrinterAgentRoute.aktiv == True)  # noqa: E712
    )
    if printer_id is not None:
        query = query.filter(PrinterAgentRoute.printer_id == printer_id)
    if gateway_id is not None:
        query = query.filter(PrinterAgentRoute.gateway_id == gateway_id)
    return query.execution_options(include_all_tenants=True).all()


def route_kandidaten(db: Session, printer: Printer, now: datetime | None = None) -> list[PrinterAgentRoute]:
    """Bereite Routen eines Druckziels, nach (priority, id) sortiert.

    Bereit heißt: Route aktiv, Agent gekoppelt und online (frischer Heartbeat), Route meldet
    frisch „ready“ und – bei aktivem Identitäts-Check – die Druckeridentität passt.
    """
    zeit = _jetzt(now)
    routen = (
        db.query(PrinterAgentRoute)
        .join(Gateway, Gateway.id == PrinterAgentRoute.gateway_id)
        .filter(
            PrinterAgentRoute.org_id == printer.org_id,
            PrinterAgentRoute.printer_id == printer.id,
            PrinterAgentRoute.aktiv == True,  # noqa: E712
            Gateway.org_id == printer.org_id,
            Gateway.kind == GATEWAY_KIND_WINDOWS_AGENT,
            Gateway.device_token_hash.isnot(None),
            Gateway.status == GATEWAY_STATUS_ONLINE,
            Gateway.agent_connection_id.isnot(None),
            Gateway.agent_connection_token_hash == Gateway.device_token_hash,
            Gateway.agent_presence_expires_at > zeit,
            Gateway.last_seen_at >= zeit - timedelta(seconds=AGENT_ONLINE_S),
        )
        .execution_options(include_all_tenants=True)
        .all()
    )
    kandidaten = []
    for route in routen:
        geprueft = _zeit_aus_json((route.readiness or {}).get("checked_at"))
        if not route.ready or geprueft is None:
            continue
        if geprueft < zeit - timedelta(seconds=READINESS_FRESH_S):
            continue
        if route.identity_check and not identitaet_passt(printer.identity, route.identity):
            continue
        kandidaten.append(route)
    return sorted(kandidaten, key=lambda r: (r.priority, r.id))


def darf_leasen(route: PrinterAgentRoute, kandidaten: list[PrinterAgentRoute], job: PrintJob,
                now: datetime | None = None) -> bool:
    """Darf diese Route den Job jetzt übernehmen?

    Die beste bereite Route sofort; die n-te Route erst, wenn der Job seit
    FALLBACK_GRACE_S * n Sekunden unbeansprucht in der Warteschlange liegt.
    """
    ids = [kandidat.id for kandidat in kandidaten]
    if route.id not in ids:
        return False
    rang = ids.index(route.id)
    if rang == 0:
        return True
    if job.queued_at is None:
        return False
    return _jetzt(now) - job.queued_at >= timedelta(seconds=FALLBACK_GRACE_S * rang)


def hat_agent_routen(db: Session, printer_id: int | None) -> bool:
    """True, wenn das Druckziel mindestens eine aktive Agent-Route hat (bereit oder nicht)."""
    if printer_id is None:
        return False
    treffer = (
        db.query(PrinterAgentRoute.id)
        .filter(PrinterAgentRoute.printer_id == printer_id, PrinterAgentRoute.aktiv == True)  # noqa: E712
        .execution_options(include_all_tenants=True)
        .first()
    )
    return treffer is not None


def route_gateway_ids(db: Session, org_id: int | None, printer_id: int | None) -> list[int]:
    """Agents, die ein Druckziel bedienen (Empfänger für job_available-Hinweise)."""
    if printer_id is None:
        return []
    return sorted({route.gateway_id for route in _aktive_routen(db, org_id, printer_id=printer_id)})


def enqueue_for_agents(db: Session, job: PrintJob, now: datetime | None = None) -> list[int]:
    """Stellt einen Job auf Lease-Zustellung um und gibt die zu benachrichtigenden Agents zurück."""
    job.delivery = DELIVERY_LEASE
    job.status = JOB_QUEUED
    job.phase = None
    job.queued_at = _jetzt(now)
    db.commit()
    return route_gateway_ids(db, job.org_id, job.printer_id)


def _grant(job: PrintJob, route: PrinterAgentRoute, token: str) -> dict:
    from app.services.print_artifact_service import artifact_url, is_html_render

    options = dict(route.options or {})
    if job.document_type == "dokumentpaket":
        # Bestehender Windows-Treiber liest diese Optionen auf Routenebene.
        # Nur für diesen Auftrag, niemals die gespeicherte Route verändern.
        options.update(orientation="auto", scaling="fit", collate=True)
    return {
        "job_id": job.id,
        "lease_token": token,
        "lease_generation": job.lease_generation,
        "lease_expires_in": LEASE_TTL_S,
        "document_type": job.document_type,
        "artifact_url": artifact_url(job),
        "render_kind": "html" if is_html_render(job) else "pdf",
        "options": ({k: v for k, v in (job.options or {}).items() if k in ("copies", "duplex", "color", "media")}
                    if job.document_type == "dokumentpaket" else job.options or {}),
        "route": {
            "route_id": route.id,
            "printer_id": route.printer_id,
            "local_queue": route.local_queue,
            "render_mode": route.render_mode,
            "options": options,
        },
    }


def lease_jobs(db: Session, gateway: Gateway, max_n: int, now: datetime | None = None) -> list[dict]:
    """Übernimmt bis zu max_n wartende Jobs für diesen Agent (atomar, ein Gewinner je Job)."""
    zeit = _jetzt(now)
    routen = {route.printer_id: route for route in _aktive_routen(db, gateway.org_id, gateway_id=gateway.id)}
    if not routen or max_n <= 0:
        return []
    wartend = (
        db.query(PrintJob)
        .filter(
            PrintJob.org_id == gateway.org_id,
            PrintJob.delivery == DELIVERY_LEASE,
            PrintJob.status == JOB_QUEUED,
            PrintJob.printer_id.in_(list(routen)),
        )
        .order_by(PrintJob.queued_at, PrintJob.id)
        .limit(20)
        .execution_options(include_all_tenants=True)
        .all()
    )
    kandidaten_je_drucker: dict[int, list[PrinterAgentRoute]] = {}
    grants: list[dict] = []
    for job in wartend:
        if len(grants) >= max_n:
            break
        if job.printer_id is None:
            continue
        route = routen[job.printer_id]
        if job.printer_id not in kandidaten_je_drucker:
            printer = (
                db.query(Printer)
                .filter(Printer.id == job.printer_id, Printer.org_id == gateway.org_id)
                .execution_options(include_all_tenants=True)
                .first()
            )
            kandidaten_je_drucker[job.printer_id] = (
                route_kandidaten(db, printer, zeit) if printer is not None else []
            )
        if not darf_leasen(route, kandidaten_je_drucker[job.printer_id], job, zeit):
            continue
        token = secrets.token_hex(16)
        gewonnen = (
            db.query(PrintJob)
            .filter(
                PrintJob.id == job.id,
                PrintJob.org_id == gateway.org_id,
                PrintJob.status == JOB_QUEUED,
                PrintJob.delivery == DELIVERY_LEASE,
            )
            .update({
                "status": JOB_SENT,
                "phase": PHASE_LEASED,
                "lease_gateway_id": gateway.id,
                "lease_token": token,
                "lease_expires_at": zeit + timedelta(seconds=LEASE_TTL_S),
                "lease_generation": PrintJob.lease_generation + 1,
                "agent_contact_at": zeit,
                "gateway_id": gateway.id,
                "attempts": PrintJob.attempts + 1,
            }, synchronize_session=False)
        )
        db.commit()
        if gewonnen != 1:
            continue
        db.refresh(job)
        grants.append(_grant(job, route, token))
        logger.info("Druckauftrag %s an Agent %s verliehen (Generation %s)",
                    job.id, gateway.id, job.lease_generation)
    return grants


def renew(db: Session, gateway: Gateway, leases: list[dict], now: datetime | None = None) -> list[int]:
    """Verlängert eigene Leases. Gibt die Job-IDs zurück, deren Lease nicht mehr gilt."""
    zeit = _jetzt(now)
    ungueltig: list[int] = []
    for lease in leases:
        job_id = lease.get("job_id")
        if not isinstance(job_id, int):
            continue
        treffer = (
            db.query(PrintJob)
            .filter(
                PrintJob.id == job_id,
                PrintJob.org_id == gateway.org_id,
                PrintJob.lease_gateway_id == gateway.id,
                PrintJob.lease_token == lease.get("lease_token"),
                PrintJob.status.in_((JOB_SENT, JOB_PRINTING)),
            )
            .update({
                "lease_expires_at": zeit + timedelta(seconds=LEASE_TTL_S),
                "agent_contact_at": zeit,
            }, synchronize_session=False)
        )
        if treffer != 1:
            ungueltig.append(job_id)
    db.commit()
    return ungueltig


def _job(db: Session, org_id: int | None, job_id: object) -> PrintJob | None:
    if not isinstance(job_id, int):
        return None
    return (
        db.query(PrintJob)
        .filter(PrintJob.id == job_id, PrintJob.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )


def print_intent(db: Session, gateway: Gateway, job_id: int, token: str,
                 now: datetime | None = None) -> tuple[bool, str | None]:
    """Gibt die Übergabe an den Spooler frei (Phase submitting). Nur mit gültigem Lease."""
    zeit = _jetzt(now)
    treffer = (
        db.query(PrintJob)
        .filter(
            PrintJob.id == job_id,
            PrintJob.org_id == gateway.org_id,
            PrintJob.lease_gateway_id == gateway.id,
            PrintJob.lease_token == token,
            PrintJob.status == JOB_SENT,
            PrintJob.phase.in_(VOR_UEBERGABE),
            PrintJob.lease_expires_at >= zeit,
        )
        .update({
            "phase": PHASE_SUBMITTING,
            "agent_contact_at": zeit,
            "lease_expires_at": zeit + timedelta(seconds=LEASE_TTL_S),
        }, synchronize_session=False)
    )
    db.commit()
    if treffer == 1:
        return True, None
    job = _job(db, gateway.org_id, job_id)
    if job is None:
        return False, "unknown"
    if job.status == JOB_CANCELED:
        return False, "canceled"
    if job.lease_gateway_id != gateway.id or job.lease_token != token:
        return False, "not_owner"
    if job.status == JOB_SENT and job.phase == PHASE_SUBMITTING:
        return True, None  # Wiederholung desselben Intents (z. B. nach Reconnect)
    if job.phase in NACH_UEBERGABE or job.status in AGENT_ENDZUSTAENDE:
        return False, "already_submitted"
    return False, "lease_expired"


def apply_agent_status(db: Session, gateway: Gateway, job_id: int, token: str, status: str,
                       phase: str | None, spool_ref: str | None, error: str | None,
                       now: datetime | None = None) -> dict:
    """Wendet eine job_status-Meldung des Agents an (Fencing über den Lease-Token).

    Ergebnis: {"result": applied|stale|ignored, "status", "fallback", "requeued",
    "notify_gateway_ids"}. Den Ersatzdruck führt der Aufrufer aus.
    """
    zeit = _jetzt(now)
    ergebnis: dict[str, Any] = {
        "result": "ignored", "status": None, "fallback": False, "requeued": False,
        "notify_gateway_ids": [],
    }
    job = _job(db, gateway.org_id, job_id)
    if job is None or job.lease_gateway_id != gateway.id or job.lease_token != token:
        logger.info("Veraltete Statusmeldung %s für Druckauftrag %s von Agent %s ignoriert",
                    status, job_id, gateway.id)
        ergebnis["result"] = "stale"
        return ergebnis
    ergebnis["status"] = job.status
    if job.status in AGENT_ENDZUSTAENDE:
        return ergebnis

    fehler = (error or "")[:500] or None
    werte: dict[Any, Any] = {"agent_contact_at": zeit}
    erneut_einreihen = False
    ersatzdruck = False
    if status == JOB_PRINTING:
        werte.update(status=JOB_PRINTING, phase=PHASE_SPOOLED, error=fehler)
        if spool_ref:
            werte["spool_ref"] = spool_ref[:120]
    elif status == JOB_DONE:
        werte.update(status=JOB_DONE, error=None)
    elif status == JOB_CANCELED:
        werte.update(status=JOB_CANCELED)
    elif status == JOB_UNCERTAIN:
        werte.update(status=JOB_UNCERTAIN, error=fehler or FEHLER_UNKLAR)
    elif status == JOB_FAILED:
        werte["error"] = fehler or "Druck fehlgeschlagen"
        if job.phase in VOR_UEBERGABE and job.attempts < MAX_AGENT_ATTEMPTS:
            werte.update(status=JOB_QUEUED, phase=None, queued_at=zeit, lease_gateway_id=None,
                         lease_token=None, lease_expires_at=None)
            erneut_einreihen = True
        else:
            # Vor der Übergabe nach zu vielen Versuchen oder vom Spooler sicher abgelehnt:
            # es wurde nichts gedruckt, der Ersatzdrucker der Regel darf einspringen.
            werte["status"] = JOB_FAILED
            ersatzdruck = True
    else:
        return ergebnis

    treffer = (
        db.query(PrintJob)
        .filter(
            PrintJob.id == job.id,
            PrintJob.org_id == gateway.org_id,
            PrintJob.lease_gateway_id == gateway.id,
            PrintJob.lease_token == token,
            PrintJob.status.notin_(AGENT_ENDZUSTAENDE),
        )
        .update(werte, synchronize_session=False)
    )
    db.commit()
    if treffer != 1:
        ergebnis["result"] = "stale"
        return ergebnis
    ergebnis.update(result="applied", status=werte.get("status", job.status),
                    fallback=ersatzdruck, requeued=erneut_einreihen)
    if erneut_einreihen:
        ergebnis["notify_gateway_ids"] = route_gateway_ids(db, gateway.org_id, job.printer_id)
    return ergebnis


def resume(db: Session, gateway: Gateway, open_jobs: list[dict], now: datetime | None = None) -> list[dict]:
    """Abgleich beim hello: gültige eigene Leases verlängern, alles andere abandon."""
    zeit = _jetzt(now)
    abandon: list[dict] = []
    for eintrag in open_jobs:
        job_id = eintrag.get("job_id")
        job = _job(db, gateway.org_id, job_id)
        gueltig = (
            job is not None
            and job.lease_gateway_id == gateway.id
            and job.lease_token == eintrag.get("lease_token")
            and job.status in (JOB_SENT, JOB_PRINTING)
        )
        if not gueltig:
            abandon.append({"job_id": job_id, "reason": "invalid_lease"})
            continue
        (
            db.query(PrintJob)
            .filter(
                PrintJob.id == job_id,
                PrintJob.org_id == gateway.org_id,
                PrintJob.lease_token == eintrag.get("lease_token"),
            )
            .update({
                "lease_expires_at": zeit + timedelta(seconds=LEASE_TTL_S),
                "agent_contact_at": zeit,
            }, synchronize_session=False)
        )
    db.commit()
    return abandon


def sweep(db: Session, now: datetime | None = None) -> dict[str, list[tuple[int, int]]]:
    """Räumt Agent-Jobs auf (läuft in jedem Worker; bedingte UPDATEs, ein Gewinner).

    (a) abgelaufener Lease vor der Übergabe → wieder einreihen (nach MAX_AGENT_ATTEMPTS: failed)
    (b) nach der Übergabe ohne Agent-Kontakt → uncertain (nie neu drucken)
    (c) zu lange ohne bereiten Agent in der Warteschlange → failed (Ersatzdruck durch Aufrufer)
    """
    zeit = _jetzt(now)
    ergebnis: dict[str, list[tuple[int, int]]] = {"requeued": [], "uncertain": [], "failed": []}
    faelle: list[tuple[str, tuple]] = [
        ("abgelaufen", (
            PrintJob.delivery == DELIVERY_LEASE,
            PrintJob.status == JOB_SENT,
            PrintJob.phase.in_(VOR_UEBERGABE),
            PrintJob.lease_expires_at < zeit,
        )),
        ("uncertain", (
            PrintJob.delivery == DELIVERY_LEASE,
            PrintJob.status.in_((JOB_SENT, JOB_PRINTING)),
            PrintJob.phase.in_(NACH_UEBERGABE),
            PrintJob.agent_contact_at < zeit - timedelta(seconds=UNCERTAIN_AFTER_S),
        )),
        ("failed", (
            PrintJob.delivery == DELIVERY_LEASE,
            PrintJob.status == JOB_QUEUED,
            PrintJob.queued_at < zeit - timedelta(seconds=QUEUE_MAX_WAIT_S),
        )),
    ]
    for fall, bedingungen in faelle:
        jobs = db.query(PrintJob).filter(*bedingungen).execution_options(include_all_tenants=True).all()
        for job in jobs:
            if job.org_id is None:
                continue
            werte: dict[Any, Any]
            if fall == "abgelaufen" and job.attempts < MAX_AGENT_ATTEMPTS:
                art = "requeued"
                werte = {"status": JOB_QUEUED, "phase": None, "queued_at": zeit,
                         "lease_gateway_id": None, "lease_token": None, "lease_expires_at": None}
            elif fall == "abgelaufen":
                art = "failed"
                werte = {"status": JOB_FAILED, "error": FEHLER_MEHRFACH}
            elif fall == "uncertain":
                art = "uncertain"
                werte = {"status": JOB_UNCERTAIN, "error": FEHLER_UNKLAR}
            else:
                art = "failed"
                werte = {"status": JOB_FAILED, "error": FEHLER_KEIN_AGENT}
            treffer = (
                db.query(PrintJob)
                .filter(PrintJob.id == job.id, PrintJob.org_id == job.org_id, *bedingungen)
                .update(werte, synchronize_session=False)
            )
            db.commit()
            if treffer == 1:
                ergebnis[art].append((job.id, job.org_id))
                logger.info("Druckauftrag %s (org_id=%s): Sweep %s", job.id, job.org_id, art)
    return ergebnis


def route_readiness_aktualisieren(db: Session, gateway: Gateway, routes_payload: list[dict],
                                  now: datetime | None = None) -> None:
    """Übernimmt die readiness-Meldung des Agents (nur eigene Routen derselben Org)."""
    zeit = _jetzt(now)
    for eintrag in routes_payload:
        route_id = eintrag.get("route_id")
        if not isinstance(route_id, int):
            continue
        route = (
            db.query(PrinterAgentRoute)
            .filter(
                PrinterAgentRoute.id == route_id,
                PrinterAgentRoute.org_id == gateway.org_id,
                PrinterAgentRoute.gateway_id == gateway.id,
            )
            .execution_options(include_all_tenants=True)
            .first()
        )
        if route is None:
            continue
        printer = (
            db.query(Printer)
            .filter(Printer.id == route.printer_id, Printer.org_id == gateway.org_id)
            .execution_options(include_all_tenants=True)
            .first()
        )
        identitaet = eintrag.get("identity") if isinstance(eintrag.get("identity"), dict) else None
        bereit = bool(eintrag.get("ready"))
        zustand = eintrag.get("state")
        if printer is not None:
            if identitaet and not printer.identity:
                printer.identity = identitaet
            if (route.identity_check and identitaet
                    and not identitaet_passt(printer.identity, identitaet)):
                bereit = False
                zustand = "identity_mismatch"
            if isinstance(eintrag.get("capabilities"), dict):
                printer.capabilities = eintrag["capabilities"]
        route.ready = bereit
        route.identity = identitaet
        route.readiness = {"state": zustand, "detail": eintrag.get("detail"), "checked_at": zeit.isoformat()}
    db.commit()


def inventar_speichern(db: Session, gateway: Gateway, payload: dict, now: datetime | None = None) -> None:
    """Speichert das gemeldete Queue-Inventar des Agents in gateway.agent_info."""
    info = dict(gateway.agent_info or {})
    info["queues"] = payload.get("queues") or []
    info["reported_at"] = _jetzt(now).isoformat()
    gateway.agent_info = info
    db.commit()


def agent_config_sync(db: Session, gateway: Gateway) -> dict:
    """config_sync-Payload für einen Agent: seine aktiven Routen samt Druckziel-Vorgaben."""
    zeilen = (
        db.query(PrinterAgentRoute, Printer)
        .join(Printer, Printer.id == PrinterAgentRoute.printer_id)
        .filter(
            PrinterAgentRoute.org_id == gateway.org_id,
            PrinterAgentRoute.gateway_id == gateway.id,
            PrinterAgentRoute.aktiv == True,  # noqa: E712
            Printer.org_id == gateway.org_id,
        )
        .order_by(PrinterAgentRoute.priority, PrinterAgentRoute.id)
        .execution_options(include_all_tenants=True)
        .all()
    )
    return {
        "agent_id": gateway.id,
        "name": gateway.name,
        "routes": [
            {
                "route_id": route.id,
                "printer_id": printer.id,
                "printer_name": printer.name,
                "local_queue": route.local_queue,
                "priority": route.priority,
                "render_mode": route.render_mode,
                "options": route.options or {},
                "identity_check": route.identity_check,
                "defaults": printer.defaults or {},
            }
            for route, printer in zeilen
        ],
        "intervals": {"heartbeat_s": 30, "readiness_s": 30, "lease_poll_s": 15, "renew_s": 20},
    }
