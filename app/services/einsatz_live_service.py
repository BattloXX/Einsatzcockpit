"""Live-Status fuer laufende Einsaetze in der nativen App."""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.queries import visible_incidents_q
from app.models.incident import Incident, IncidentOrg, IncidentVehicle
from app.services.incident_notify import _combined_address

PHASES = (
    {"phase": "alarmiert", "phase_label": "Alarmiert"},
    {"phase": "anfahrt", "phase_label": "Anfahrt"},
    {"phase": "einsatzstelle", "phase_label": "An der Einsatzstelle"},
    {"phase": "abschluss", "phase_label": "Einsatzbereit"},
)


def _derive_phase(db: Session, incident: Incident, device_token) -> tuple[int, str]:
    """Leitet die Phase aus dem eigenen Fahrzeug oder dem Org-Aggregat ab."""
    rows = db.query(IncidentVehicle).filter(
        IncidentVehicle.incident_id == incident.id,
        IncidentVehicle.removed_at.is_(None),
    )

    if device_token and device_token.vehicle_master_id:
        own_vehicle = rows.filter(
            IncidentVehicle.vehicle_master_id == device_token.vehicle_master_id
        ).first()
        if own_vehicle:
            phase_index = {
                "Einsatz übernommen": 1,
                "Am Einsatzort": 2,
                "Einsatzbereit": 3,
            }.get(own_vehicle.unit_status, 0)
            return phase_index, "vehicle"

    statuses = [row.unit_status for row in rows.all()]
    if not statuses:
        return 0, "org"
    if all(status == "Einsatzbereit" for status in statuses):
        return 3, "org"
    if any(status == "Am Einsatzort" for status in statuses):
        return 2, "org"
    return 1, "org"


def _active_incidents_q(db: Session, user):
    """Kanonische Sichtbarkeit plus expliziter, system-admin-sicherer Org-Scope."""
    collab_subq = db.query(IncidentOrg.incident_id).filter(
        IncidentOrg.org_id == user.org_id
    )
    return visible_incidents_q(db, user).filter(
        Incident.status == "active",
        or_(
            Incident.primary_org_id == user.org_id,
            Incident.id.in_(collab_subq),
        ),
    )


def _select_incident(db: Session, user, device_token) -> Incident | None:
    """Waehlt den fahrzeugrelevanten, sonst den neuesten aktiven Einsatz."""
    active = _active_incidents_q(db, user)
    if device_token and device_token.vehicle_master_id:
        assigned = (
            active.join(IncidentVehicle, Incident.id == IncidentVehicle.incident_id)
            .filter(
                IncidentVehicle.vehicle_master_id == device_token.vehicle_master_id,
                IncidentVehicle.removed_at.is_(None),
            )
            .order_by(Incident.started_at.desc(), Incident.id.desc())
            .first()
        )
        if assigned:
            return assigned
    return active.order_by(Incident.started_at.desc(), Incident.id.desc()).first()


def build_incident_live_payload(
    db: Session, incident: Incident, device_token=None
) -> dict:
    """Baut den Live-Payload fuer einen ausgewaehlten Einsatz."""
    phase_index, phase_source = _derive_phase(db, incident, device_token)
    phase = PHASES[phase_index]
    unit_count = db.query(IncidentVehicle).filter(
        IncidentVehicle.incident_id == incident.id,
        IncidentVehicle.removed_at.is_(None),
    ).count()
    return {
        "id": incident.id,
        "url": f"/einsatz/{incident.id}",
        "alarm_type_code": incident.alarm_type_code,
        "address": _combined_address(incident),
        "started_at": incident.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "is_exercise": incident.is_exercise,
        "phase": phase["phase"],
        "phase_index": phase_index,
        "phase_count": len(PHASES),
        "phase_label": phase["phase_label"],
        "phase_source": phase_source,
        "unit_count": unit_count,
    }


def build_live_state(db: Session, user, device_token) -> tuple[dict | None, int]:
    """Baut den Live-Status und die Zahl aller sichtbaren aktiven Einsaetze."""
    if not user or not user.org_id:
        return None, 0

    active = _active_incidents_q(db, user)
    incident_count = active.count()
    if not incident_count:
        return None, 0

    incident = _select_incident(db, user, device_token)
    if not incident:
        return None, incident_count

    return build_incident_live_payload(db, incident, device_token), incident_count
