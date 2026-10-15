"""Uhr, Regie und sichere Ereignisausfuehrung fuer Einzel-Planspiele."""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentLog, IncidentVehicle, Message, Task
from app.models.planspiel import PlanspielRun, PlanspielRunEreignis
from app.services.broadcast import broadcast_org
from app.services.incident_service import add_message, broadcast_message_created
from app.services.planspiel_katalog_service import org_stichwort_code
from app.services.planspiel_run_service import ZustandsFehler

logger = logging.getLogger(__name__)
_RUNNING = {"laeuft", "generierung_gestoppt"}
_STEUERBAR = _RUNNING | {"pausiert"}
_LEASE = timedelta(seconds=60)
_TYP_LABELS = {
    "aufklaerung": "Aufklärung", "eskalation": "Eskalation", "komplikation": "Komplikation",
    "entspannung": "Entspannung", "lageweit": "Lagemeldung", "funk": "Funkmeldung",
    "duplikat": "Weitere Meldung",
}


@dataclass(frozen=True)
class AusfuehrungsErgebnis:
    incident_id: int
    message_id: int | None


@dataclass(frozen=True)
class TickErgebnis:
    ausgefuehrt: list[AusfuehrungsErgebnis]
    verpasst: int = 0
    reaktiv_faellig: int = 0


def sim_sekunden(run: PlanspielRun, jetzt: datetime) -> float:
    if run.gestartet_at is None:
        return 0.0
    ende = run.pausiert_seit or jetzt
    sekunden = (ende - run.gestartet_at).total_seconds() - run.pausiert_summe_s
    return max(0.0, sekunden * run.tempo)


def _sofort_faellig(run: PlanspielRun, jetzt: datetime) -> float:
    """Faelligkeit fuer Regie-Ereignisse: abgerundete ganze Uebungssekunde.

    faellig_sim_s ist unter MariaDB ein FLOAT mit einfacher Genauigkeit; ein
    gebrochener Wert wird beim Speichern minimal aufgerundet und waere fuer den
    sofortigen Tick dann noch nicht faellig. Ganze Zahlen sind exakt darstellbar.
    """
    return float(math.floor(sim_sekunden(run, jetzt)))


def _audit(db: Session, run: PlanspielRun, user: Any, aktion: str, **payload: Any) -> None:
    write_audit(db, f"planspiel.run.{aktion}", org_id=run.org_id, user_id=user.id,
                entity_type="planspiel_run", entity_id=run.id, payload=payload or None)


def _steuerbar(run: PlanspielRun) -> None:
    if run.status not in _STEUERBAR:
        raise ZustandsFehler("Planspiel kann in diesem Status nicht gesteuert werden")


def pausieren(db: Session, run: PlanspielRun, user: Any, jetzt: datetime) -> PlanspielRun:
    if run.status not in _RUNNING:
        raise ZustandsFehler("Planspiel kann nicht pausiert werden")
    konfig = dict(run.konfig or {})
    konfig["status_vor_pause"] = run.status
    run.konfig, run.status, run.pausiert_seit = konfig, "pausiert", jetzt
    run.steuer_version += 1
    _audit(db, run, user, "pausiert")
    return run


def fortsetzen(db: Session, run: PlanspielRun, user: Any, jetzt: datetime) -> PlanspielRun:
    if run.status != "pausiert" or run.pausiert_seit is None:
        raise ZustandsFehler("Planspiel kann nicht fortgesetzt werden")
    run.pausiert_summe_s += max(0, int((jetzt - run.pausiert_seit).total_seconds()))
    status = (run.konfig or {}).get("status_vor_pause", "laeuft")
    run.status, run.pausiert_seit = status, None
    run.steuer_version += 1
    _audit(db, run, user, "fortgesetzt")
    return run


def folgemeldungen_setzen(db: Session, run: PlanspielRun, user: Any, aktiv: bool) -> PlanspielRun:
    _steuerbar(run)
    run.folge_aktiv = aktiv
    run.steuer_version += 1
    _audit(db, run, user, "folgemeldungen_gesetzt", aktiv=aktiv)
    return run


def beenden(db: Session, run: PlanspielRun, user: Any, jetzt: datetime) -> PlanspielRun:
    _steuerbar(run)
    if run.pausiert_seit is not None:
        run.pausiert_summe_s += max(0, int((jetzt - run.pausiert_seit).total_seconds()))
        run.pausiert_seit = None
    offene = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.run_id == run.id, PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.status.in_(("geplant", "faellig", "laeuft")),
    ).all()
    for event in offene:
        event.status, event.lease_until = "abgebrochen", None
    run.status, run.beendet_at = "beendet", jetzt
    run.worker_lease_until, run.worker_id = None, None
    run.steuer_version += 1
    incident = _incident(db, run.incident_id, run.org_id)
    if incident is not None:
        db.add(IncidentLog(incident_id=incident.id, author_name="Planspiel", level="info",
                           text="[Planspiel] Übung durch die Übungsleitung beendet."))
    _audit(db, run, user, "beendet")
    return run


def _existing_regie(db: Session, run: PlanspielRun, client_uuid: str | None) -> PlanspielRunEreignis | None:
    if not client_uuid:
        return None
    return db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.idempotenz_key == f"run{run.id}:regie:{client_uuid}",
    ).one_or_none()


def _event(db: Session, run: PlanspielRun, ereignis_id: int) -> PlanspielRunEreignis:
    event = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.id == ereignis_id, PlanspielRunEreignis.run_id == run.id,
        PlanspielRunEreignis.org_id == run.org_id,
    ).one_or_none()
    if event is None:
        raise ValueError("Ereignis gehört nicht zu diesem Planspiel")
    return event


def ereignis_jetzt(db: Session, run: PlanspielRun, ereignis_id: int, user: Any, jetzt: datetime,
                   client_uuid: str | None = None) -> PlanspielRunEreignis:
    _steuerbar(run)
    existing = _existing_regie(db, run, client_uuid)
    if existing is not None:
        return existing
    event = _event(db, run, ereignis_id)
    if event.status not in {"geplant", "uebersprungen"}:
        raise ZustandsFehler("Ereignis kann nicht sofort ausgelöst werden")
    event.faellig_sim_s, event.status = _sofort_faellig(run, jetzt), "geplant"
    if client_uuid:
        event.idempotenz_key = f"run{run.id}:regie:{client_uuid}"
    _audit(db, run, user, "ereignis_jetzt", ereignis_id=event.id)
    return event


def ereignis_ueberspringen(db: Session, run: PlanspielRun, ereignis_id: int, user: Any) -> PlanspielRunEreignis:
    _steuerbar(run)
    event = _event(db, run, ereignis_id)
    if event.status != "geplant":
        raise ZustandsFehler("Ereignis kann nicht übersprungen werden")
    event.status, event.fehler = "uebersprungen", "durch Regie übersprungen"
    _audit(db, run, user, "ereignis_uebersprungen", ereignis_id=event.id)
    return event


def _naechste_seq(db: Session, run: PlanspielRun) -> int:
    value = db.query(func.max(PlanspielRunEreignis.seq)).filter(
        PlanspielRunEreignis.run_id == run.id, PlanspielRunEreignis.org_id == run.org_id,
    ).scalar()
    return int(value or 0) + 1


def ereignis_wiederholen(db: Session, run: PlanspielRun, ereignis_id: int, user: Any, jetzt: datetime,
                         client_uuid: str | None = None) -> PlanspielRunEreignis:
    _steuerbar(run)
    existing = _existing_regie(db, run, client_uuid)
    if existing is not None:
        return existing
    source = _event(db, run, ereignis_id)
    key = f"run{run.id}:regie:{client_uuid}" if client_uuid else f"run{run.id}:regie:repeat:{_naechste_seq(db, run)}"
    event = PlanspielRunEreignis(org_id=run.org_id, run_id=run.id, seq=_naechste_seq(db, run), quelle="regie",
        baustein_code=source.baustein_code, ziel_typ=source.ziel_typ, ziel_id=source.ziel_id, trigger_type="manual",
        trigger_param=dict(source.trigger_param or {}), faellig_sim_s=_sofort_faellig(run, jetzt), status="geplant",
        idempotenz_key=key, sichtbarkeit=source.sichtbarkeit, text=source.text)
    db.add(event)
    db.flush()
    _audit(db, run, user, "ereignis_wiederholt", ereignis_id=source.id)
    return event


def freie_meldung(db: Session, run: PlanspielRun, user: Any, jetzt: datetime, *, text: str,
                  sichtbarkeit: str = "teilnehmer", ereignis_typ: str = "lageweit",
                  client_uuid: str | None = None) -> PlanspielRunEreignis:
    _steuerbar(run)
    if not 1 <= len(text) <= 1000:
        raise ValueError("Text muss zwischen 1 und 1000 Zeichen lang sein")
    existing = _existing_regie(db, run, client_uuid)
    if existing is not None:
        return existing
    key = f"run{run.id}:regie:{client_uuid}" if client_uuid else f"run{run.id}:regie:free:{_naechste_seq(db, run)}"
    event = PlanspielRunEreignis(org_id=run.org_id, run_id=run.id, seq=_naechste_seq(db, run), quelle="regie",
        ziel_typ="incident", ziel_id=run.incident_id, trigger_type="manual",
        trigger_param={"ereignis_typ": ereignis_typ}, faellig_sim_s=_sofort_faellig(run, jetzt), status="geplant",
        idempotenz_key=key, sichtbarkeit=sichtbarkeit, text=text)
    db.add(event)
    db.flush()
    _audit(db, run, user, "freie_meldung", ereignis_id=None)
    return event


def stichwort_aendern(db: Session, run: PlanspielRun, user: Any, code: str) -> Incident:
    _steuerbar(run)
    if run.org_id is None or org_stichwort_code(db, run.org_id, code) is None:
        raise ValueError("Stichwort ist in der Organisation nicht vorhanden")
    incident = _incident(db, run.incident_id, run.org_id)
    if incident is None:
        raise ValueError("Übungseinsatz wurde nicht gefunden")
    old = incident.alarm_type_code
    incident.alarm_type_code = code
    db.add(IncidentLog(incident_id=incident.id, author_name="Planspiel", level="info",
                       text=f"[Planspiel] Stichwort durch Übungsleitung geändert: {old} → {code}"))
    _audit(db, run, user, "stichwort_geaendert", alt=old, neu=code)
    return incident


def _incident(db: Session, incident_id: int | None, org_id: int | None) -> Incident | None:
    if incident_id is None or org_id is None:
        return None
    return db.query(Incident).filter(Incident.id == incident_id, Incident.primary_org_id == org_id).one_or_none()


def _trigger_erfuellt(db: Session, run: PlanspielRun, event: PlanspielRunEreignis, sim: float) -> bool:
    incident = _incident(db, run.incident_id, run.org_id)
    if incident is None:
        return False
    param = event.trigger_param or {}
    if event.trigger_type == "phase_reached":
        angekommen = db.query(IncidentVehicle.id).filter(
            IncidentVehicle.incident_id == incident.id, IncidentVehicle.removed_at.is_(None),
            IncidentVehicle.unit_status == "Am Einsatzort",
        ).first() is not None
        phase = param.get("phase")
        if phase != "in_arbeit" or not angekommen:
            return angekommen
        ref = dict(event.ergebnis_ref or {})
        first = ref.setdefault("bedingung_erfuellt_sim_s", sim)
        event.ergebnis_ref = ref
        return sim - float(first) >= 120
    if event.trigger_type == "assignment_created":
        task = db.query(Task.id).filter(
            Task.incident_id == incident.id, Task.is_cancelled.is_(False),
        ).first()
        return task is not None
    if event.trigger_type == "assignment_completed":
        return db.query(Task.id).filter(Task.incident_id == incident.id, Task.is_done.is_(True)).first() is not None
    return False


def reaktive_trigger_auswerten(db: Session, run: PlanspielRun, jetzt: datetime) -> int:
    sim, count = sim_sekunden(run, jetzt), 0
    events = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.run_id == run.id, PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.status == "geplant", PlanspielRunEreignis.faellig_sim_s.is_(None),
        PlanspielRunEreignis.trigger_type.in_(("phase_reached", "assignment_created", "assignment_completed")),
    ).all()
    fallback = float((run.konfig or {}).get("reaktiv_fallback_s", 480))
    for event in events:
        if _trigger_erfuellt(db, run, event, sim):
            event.faellig_sim_s = float(math.floor(sim))
            count += 1
        elif sim >= fallback:
            event.faellig_sim_s = float(math.floor(sim))
            event.ergebnis_ref = {**(event.ergebnis_ref or {}), "ausloesung": "fallback"}
            count += 1
    return count


def faellige_ereignisse(db: Session, run: PlanspielRun, jetzt: datetime, limit: int = 5) -> list[int]:
    sim = sim_sekunden(run, jetzt)
    return [row[0] for row in db.query(PlanspielRunEreignis.id).filter(
        PlanspielRunEreignis.run_id == run.id, PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.status == "geplant", PlanspielRunEreignis.faellig_sim_s.is_not(None),
        PlanspielRunEreignis.faellig_sim_s <= sim,
    ).order_by(PlanspielRunEreignis.faellig_sim_s, PlanspielRunEreignis.seq).limit(limit).all()]


def ereignis_beanspruchen(db: Session, ereignis_id: int, org_id: int, jetzt: datetime) -> bool:
    stale = and_(PlanspielRunEreignis.status == "laeuft", PlanspielRunEreignis.lease_until < jetzt)
    result = db.execute(update(PlanspielRunEreignis).where(
        PlanspielRunEreignis.id == ereignis_id, PlanspielRunEreignis.org_id == org_id,
        or_(PlanspielRunEreignis.status == "geplant", stale),
    ).values(status="laeuft", lease_until=jetzt + _LEASE,
             versuche=PlanspielRunEreignis.versuche + 1))
    db.commit()
    return bool(getattr(result, "rowcount", 0) == 1)


def _typ_label(event: PlanspielRunEreignis) -> str:
    typ = (event.trigger_param or {}).get("ereignis_typ")
    return _TYP_LABELS.get(typ if isinstance(typ, str) else "", "Lagemeldung")


def _execute_incident(db: Session, run: PlanspielRun, event: PlanspielRunEreignis) -> AusfuehrungsErgebnis:
    incident = _incident(db, event.ziel_id or run.incident_id, run.org_id)
    if incident is None:
        raise ValueError("Ziel-Einsatz wurde nicht gefunden")
    if event.sichtbarkeit == "regie":
        return AusfuehrungsErgebnis(incident.id, None)
    label = _typ_label(event)
    msg = add_message(db, incident, title=f"[Übung] {label}", detail=event.text,
                      author_name="Übungsleitung (Planspiel)")
    db.add(IncidentLog(incident_id=incident.id, author_name="Planspiel", level="info",
                       text=f"[Planspiel] {label}: {event.text or ''}"))
    return AusfuehrungsErgebnis(incident.id, msg.id)


_AUSFUEHRER: dict[str, Callable[[Session, PlanspielRun, PlanspielRunEreignis], AusfuehrungsErgebnis]] = {
    "incident": _execute_incident,
}


def ereignis_ausfuehren(db: Session, ereignis_id: int, org_id: int, steuer_version_erwartet: int,
                        jetzt: datetime) -> AusfuehrungsErgebnis | None:
    try:
        event = db.query(PlanspielRunEreignis).filter(
            PlanspielRunEreignis.id == ereignis_id, PlanspielRunEreignis.org_id == org_id,
        ).with_for_update().one_or_none()
        if event is None:
            db.rollback()
            return None
        run = db.query(PlanspielRun).filter(
            PlanspielRun.id == event.run_id, PlanspielRun.org_id == org_id,
        ).with_for_update().one_or_none()
        if run is None:
            db.rollback()
            return None
        if run.status == "beendet":
            event.status, event.lease_until = "abgebrochen", None
            db.commit()
            return None
        if run.status not in _RUNNING or run.steuer_version != steuer_version_erwartet or (
            not run.folge_aktiv and event.quelle != "regie"
        ):
            event.status, event.lease_until = "geplant", None
            db.commit()
            return None
        executor = _AUSFUEHRER.get(event.ziel_typ or "")
        if executor is None:
            raise ValueError(f"Unbekannter Zieltyp: {event.ziel_typ}")
        result = executor(db, run, event)
        event.status, event.ausgefuehrt_at = "ausgefuehrt", jetzt
        event.ausgefuehrt_sim_s, event.lease_until = sim_sekunden(run, jetzt), None
        event.ergebnis_ref = {**(event.ergebnis_ref or {}), "message_id": result.message_id}
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        event = db.query(PlanspielRunEreignis).filter(
            PlanspielRunEreignis.id == ereignis_id, PlanspielRunEreignis.org_id == org_id,
        ).one_or_none()
        if event is not None:
            event.fehler, event.lease_until = str(exc)[:2000], None
            event.status = "fehlgeschlagen" if event.versuche >= 3 else "geplant"
            run = db.query(PlanspielRun).filter(
                PlanspielRun.id == event.run_id, PlanspielRun.org_id == org_id,
            ).one_or_none()
            if run is not None:
                run.letzter_fehler = event.fehler
            db.commit()
        logger.exception("Planspiel-Ereignis fehlgeschlagen (id=%s)", ereignis_id)
        return None


async def nach_ausfuehrung_benachrichtigen(org_id: int, run_id: int, ergebnis: AusfuehrungsErgebnis) -> None:
    try:
        if ergebnis.message_id is not None:
            db = SessionLocal()
            try:
                set_tenant_context(db, org_id)
                message = db.get(Message, ergebnis.message_id)
                if message is not None:
                    await broadcast_message_created(ergebnis.incident_id, message)
            finally:
                db.close()
        await broadcast_org(org_id, {"type": "planspiel_changed", "run_id": run_id})
    except Exception:
        logger.exception("Planspiel-Benachrichtigung fehlgeschlagen (run=%s)", run_id)


def verpasste_markieren(db: Session, run: PlanspielRun, jetzt: datetime, fenster_s: int = 120) -> int:
    grenze = sim_sekunden(run, jetzt) - fenster_s
    events = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.run_id == run.id, PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.quelle != "regie", PlanspielRunEreignis.status == "geplant",
        PlanspielRunEreignis.trigger_type == "elapsed_time", PlanspielRunEreignis.faellig_sim_s < grenze,
    ).all()
    for event in events:
        event.status, event.fehler = "uebersprungen", "verpasst (Ausfall)"
    return len(events)


def tick(db_factory: Callable[[], Session], run_id: int, org_id: int, jetzt: datetime) -> TickErgebnis:
    db = db_factory()
    try:
        set_tenant_context(db, org_id)
        run = db.query(PlanspielRun).filter(PlanspielRun.id == run_id, PlanspielRun.org_id == org_id).one_or_none()
        if run is None or run.status not in _RUNNING:
            return TickErgebnis([])
        verpasst = verpasste_markieren(db, run, jetzt)
        reaktiv = reaktive_trigger_auswerten(db, run, jetzt)
        db.commit()
        version = run.steuer_version
        ids = faellige_ereignisse(db, run, jetzt)
    finally:
        db.close()
    results = []
    for event_id in ids:
        claim_db = db_factory()
        try:
            set_tenant_context(claim_db, org_id)
            claimed = ereignis_beanspruchen(claim_db, event_id, org_id, jetzt)
        finally:
            claim_db.close()
        if not claimed:
            continue
        execute_db = db_factory()
        try:
            set_tenant_context(execute_db, org_id)
            result = ereignis_ausfuehren(execute_db, event_id, org_id, version, jetzt)
            if result is not None:
                results.append(result)
        finally:
            execute_db.close()
    return TickErgebnis(results, verpasst, reaktiv)
