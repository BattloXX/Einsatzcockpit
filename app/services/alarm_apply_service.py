"""Nachlauf nach erfolgreicher Anlage eines seriellen Alarms."""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.models.gateway import AlarmIngest

logger = logging.getLogger("einsatzleiter.alarm")


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

