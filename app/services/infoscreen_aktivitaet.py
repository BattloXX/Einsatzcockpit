"""Gemeinsame, explizit organisationsgebundene Alarm-Aktivitaetsabfragen."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.major_incident import MajorIncident, MajorIncidentStatus


def aktive_gsl(db: Session, org_id: int) -> MajorIncident | None:
    return (db.query(MajorIncident)
            .filter(MajorIncident.org_id == org_id, MajorIncident.status == MajorIncidentStatus.active)
            .order_by(MajorIncident.started_at.desc()).first())


def aktiver_incident(db: Session, org_id: int) -> Incident | None:
    return (db.query(Incident)
            .filter(Incident.primary_org_id == org_id, Incident.status == "active")
            .order_by(Incident.started_at.desc()).first())


@dataclass(frozen=True)
class Aktivitaet:
    gsl: bool = False
    incident: bool = False
    letztes_ende: datetime | None = None


def lade_aktivitaet(db: Session, org_id: int) -> Aktivitaet:
    incident_ende = db.query(func.max(Incident.closed_at)).filter(
        Incident.primary_org_id == org_id, Incident.status == "closed").scalar()
    gsl_ende = db.query(func.max(MajorIncident.ended_at)).filter(
        MajorIncident.org_id == org_id, MajorIncident.ended_at.is_not(None)).scalar()
    enden = [ende for ende in (incident_ende, gsl_ende) if ende is not None]
    return Aktivitaet(aktive_gsl(db, org_id) is not None, aktiver_incident(db, org_id) is not None,
                      max(enden) if enden else None)
