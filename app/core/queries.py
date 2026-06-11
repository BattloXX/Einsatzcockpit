"""Shared query helpers used across multiple routers."""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.permissions import has_role
from app.models.incident import Incident, IncidentOrg


def visible_incidents_q(db: Session, user):
    """Incident-Query gefiltert auf Einsätze der eigenen Org + kollaborative Einsätze.
    system_admin erhält ungefilterte Abfrage.
    """
    q = db.query(Incident)
    if has_role(user, "system_admin"):
        return q
    if not user or not user.org_id:
        return q.filter(False)  # type: ignore[arg-type]
    collab_subq = db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == user.org_id)
    return q.filter(
        or_(Incident.primary_org_id == user.org_id, Incident.id.in_(collab_subq))
    )
