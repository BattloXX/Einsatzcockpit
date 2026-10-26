"""Nachlauf nach erfolgreicher Anlage eines seriellen Alarms."""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.models.gateway import PARSE_FAILED, AlarmIngest
from app.models.incident import Incident

logger = logging.getLogger("einsatzleiter.alarm")


def wende_an(db: Session, ingest: AlarmIngest, org_id: int) -> tuple[Incident | None, str]:
    """Ordnet einen Cloud-Alarm zu; Feld-Merge folgt separat in P4b."""
    from app.services.incident_service import create_incident
    from app.services.lis.lis_matching import find_matching_incident
    from app.services.serial_alarm_service import FALLBACK_ALARM_CODE, _parse_dt, _parse_ingest

    p = _parse_ingest(db, ingest, org_id)
    nummer = ingest.leitstellennummer
    alarm_code = p.get("alarm_type_code") or FALLBACK_ALARM_CODE
    started_at = _parse_dt(p.get("started_at"))
    street, city = p.get("street"), p.get("city")
    reason = p.get("reason") or (ingest.raw_text[:200] if ingest.parse_status == PARSE_FAILED else None)

    def nummer_treffer() -> Incident | None:
        # Locking Read sieht unter MariaDB REPEATABLE READ auch den Gewinner
        # eines parallelen Inserts, ohne den aeusseren Claim zurueckzurollen.
        return db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.lis_operation_number == nummer,
        ).populate_existing().with_for_update().first()

    match = None
    try:
        match = find_matching_incident(
            db, org_id, alarm_type_code=alarm_code, street=street, city=city,
            started_at=started_at, report_text=ingest.raw_text,
            lis_operation_number=nummer,
        )
    except Exception as exc:  # Matching darf den Ingest nie blockieren
        logger.warning("Alarm-Matching fehlgeschlagen (org %s): %s", org_id, exc)

    action = "merged"
    if match is None:
        try:
            # Auch Tokens, Spalten und Audit der unterlegenen Anlage zurueckrollen.
            with db.begin_nested():
                match, _ = create_incident(
                    db, alarm_code, started_at=started_at,
                    is_exercise=bool(p.get("is_exercise")), address_street=street,
                    address_no=p.get("house_no"), address_city=city,
                    report_text=ingest.raw_text, reason=reason, primary_org_id=org_id,
                )
                match.lis_operation_number = nummer
                if hasattr(match, "source"):
                    match.source = "serial"
                db.flush()
            action = "created"
        except IntegrityError:
            if not nummer:
                raise
            match = nummer_treffer()
            if match is None:
                raise
            logger.info("Alarm-Anlage-Race zur Nummer %s (org %s): Einsatz %s", nummer, org_id, match.id)

    # Inhaltsdedup ohne Zeitfenster, nur innerhalb desselben Einsatzes und Tenants.
    original = None
    if nummer and ingest.content_hash:
        original = db.query(AlarmIngest).filter(
            AlarmIngest.org_id == org_id, AlarmIngest.einsatz_id == match.id,
            AlarmIngest.leitstellennummer == nummer,
            AlarmIngest.content_hash == ingest.content_hash,
            AlarmIngest.id != ingest.id, AlarmIngest.process_status == "applied",
        ).order_by(AlarmIngest.id).first()
    if original is not None:
        ingest.einsatz_id = match.id
        ingest.version_no = original.version_no
        logger.info("Identischer Alarm-Inhalt (ingest %s, Einsatz %s, org %s)", ingest.id, match.id, org_id)
        return match, "duplicate"

    if nummer and not match.lis_operation_number:
        try:
            with db.begin_nested():
                match.lis_operation_number = nummer
                db.flush()
        except IntegrityError:
            logger.info("Leitstellennummer %s bereits belegt (org %s), Zuordnung bleibt", nummer, org_id)

    version = db.query(func.max(AlarmIngest.version_no)).filter(
        AlarmIngest.org_id == org_id, AlarmIngest.einsatz_id == match.id,
        AlarmIngest.id != ingest.id,
    ).scalar()
    # Absichtlich kein UNIQUE: parallele Folgenachrichten duerfen dieselbe Version erhalten.
    ingest.einsatz_id = match.id
    ingest.version_no = (version or 0) + 1
    logger.info("Serieller Alarm %s: Einsatz %s, Version %s (org %s)", action, match.id, ingest.version_no, org_id)
    return match, action


def nachlauf(
    db: Session, ingest: AlarmIngest, *, org_id: int,
    background_tasks: BackgroundTasks, base_url: str,
) -> None:
    """Outbox, Benachrichtigungen und Hintergrundtasks in bisheriger Reihenfolge."""
    if ingest.einsatz_id:
        from app.models.incident import Incident
        from app.services.alarm_outbox import enqueue_incident_alarm

        incident = db.query(Incident).filter(
            Incident.id == ingest.einsatz_id, Incident.primary_org_id == org_id,
        ).first()
        if incident is not None:
            enqueue_incident_alarm(
                db, incident, org_id=org_id, source="gateway",
                push_url=f"/einsatz/{incident.id}", base_url=base_url,
            )
    db.commit()

    from app.services.print_dispatcher import autoprint_alarm_background
    background_tasks.add_task(autoprint_alarm_background, ingest.id)

    # Board/Infoscreen live informieren + Objekt-Verknüpfung nachziehen.
    # Wie bei UI-/API-/LIS-Anlage: Geocoding (setzt Koordinaten) und Objekt-Matching
    # (BMA-Nr./Adresse; Geo-Stufe folgt nach dem Geocoding) als Background-Task.
    # Ohne das wurden bei Gateway-Alarmen nie Objekte automatisch verknüpft.
    if ingest.einsatz_id:
        from app.models.incident import Incident
        inc = db.query(Incident).filter(
            Incident.id == ingest.einsatz_id, Incident.primary_org_id == org_id,
        ).first()
        if inc is not None:
            from app.services.incident_notify import incident_alarm_started, notify_incident_created
            if not incident_alarm_started(db, inc.id):
                run_async(
                    notify_incident_created, db, inc,
                    org_id=org_id, triggered_by_user_id=None,
                    push_url=f"/einsatz/{inc.id}", base_url=base_url,
                    background_tasks=background_tasks, source="gateway",
                )
            try:
                from app.services.broadcast import broadcast_org
                from app.services.exercise_guard import darf_extern
                run_async(broadcast_org, org_id, {
                        "type": "incident_created",
                        "incident_id": ingest.einsatz_id,
                        "alarm": inc.alarm_type_code,
                        "alarm_erlaubt": darf_extern(
                            "ws_alarm",
                            is_exercise=inc.is_exercise,
                            org_id=org_id,
                            db=db,
                            planspiel_run_id=inc.planspiel_run_id,
                        ),
                        "alarm_type_code": inc.alarm_type_code,
                        "is_exercise": inc.is_exercise,
                    })
            except Exception:
                logger.warning("Alarm-Broadcast fehlgeschlagen (ingest %s, org %s)",
                               ingest.id, org_id, exc_info=True)
            if inc.lat is None and (inc.address_street or inc.address_city):
                from app.routers.api_v1 import _geocode_incident
                background_tasks.add_task(
                    _geocode_incident, inc.id,
                    inc.address_street, inc.address_no, inc.address_city,
                )
            from app.services.objekt_matching_service import match_incident_background
            background_tasks.add_task(match_incident_background, inc.id)
            if ingest.dedup_action == "created":
                from app.services.print_dispatcher import autoprint_incident_background
                background_tasks.add_task(autoprint_incident_background, inc.id)

