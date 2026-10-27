from datetime import UTC, datetime

from sqlalchemy.orm import Session


def write_audit(
    db: Session,
    action: str,
    *,
    org_id: int | None = None,
    user_id: int | None = None,
    api_key_id: int | None = None,
    incident_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    payload: dict | None = None,
    ip: str | None = None,
) -> None:
    """Write a system-level audit log entry (auth, admin actions, API-key usage)."""
    import json

    from app.models.user import AuditLog

    entry = AuditLog(
        action=action,
        org_id=org_id,
        user_id=user_id,
        api_key_id=api_key_id,
        incident_id=incident_id,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
        ip=ip,
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    # caller is responsible for commit


def write_incident_change(
    db: Session,
    incident_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    before: dict | None,
    after: dict | None,
    *,
    user_id: int | None = None,
    api_key_id: int | None = None,
    ip: str | None = None,
    ts: datetime | None = None,
) -> None:
    """Write a granular incident change record (every field mutation)."""
    import json

    from app.models.incident import IncidentChange

    entry = IncidentChange(
        incident_id=incident_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=json.dumps(before, ensure_ascii=False, default=str) if before else None,
        after_json=json.dumps(after, ensure_ascii=False, default=str) if after else None,
        user_id=user_id,
        api_key_id=api_key_id,
        ip=ip,
        ts=ts if ts is not None else datetime.now(UTC),
    )
    db.add(entry)

    # Nur echte Benutzer-/API-Key-Mutationen markieren, keine Alarmpipeline.
    if entity_type == "incident" and action != "alarm.update" and (user_id is not None or api_key_id is not None):
        from app.models.incident import Incident
        from app.services.alarm_apply_service import ALARM_FELDER, markiere_manuell

        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if incident is not None and incident.primary_org_id is not None:
            for field in ALARM_FELDER:
                if after is not None and field in after and (before or {}).get(field) != after[field]:
                    markiere_manuell(db, incident_id, incident.primary_org_id, field)
