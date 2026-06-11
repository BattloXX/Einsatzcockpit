"""Alarm-Type Service: org-scoped Lookups und Hilfsfunktionen."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.master import AlarmType


def get_alarm_type_by_code(db: Session, org_id: int, code: str) -> AlarmType | None:
    """Gibt den AlarmType für (org_id, code) zurück oder None.

    Ersatz für db.get(AlarmType, code) – benötigt org_id für Multi-Tenancy.
    """
    return (
        db.query(AlarmType)
        .filter(AlarmType.org_id == org_id, AlarmType.code == code)
        .first()
    )
