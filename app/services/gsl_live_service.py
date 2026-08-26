"""Kanonischer Live-Status einer laufenden Grossschadenslage."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.major_incident import (
    SITE_PHASE_GROUP,
    IncidentSite,
    MajorIncident,
    MajorIncidentStatus,
)


def build_gsl_live_payload(db: Session, lage: MajorIncident) -> dict:
    counts = {"neu": 0, "in_arbeit": 0, "erledigt": 0}
    rows = db.query(IncidentSite.phase, func.count(IncidentSite.id)).filter(
        IncidentSite.major_incident_id == lage.id,
        IncidentSite.org_id == lage.org_id,
        IncidentSite.phase.in_(tuple(SITE_PHASE_GROUP)),
    ).group_by(IncidentSite.phase).all()
    for phase, count in rows:
        counts[SITE_PHASE_GROUP[phase]] += int(count)
    counts["gesamt"] = sum(counts.values())
    return {
        "id": lage.id, "url": f"/lage/{lage.id}", "name": lage.name,
        "is_exercise": lage.is_exercise,
        "started_at": lage.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
    }


def build_gsl_live_state(db: Session, user) -> tuple[dict | None, int]:
    if not user or not user.org_id:
        return None, 0
    query = db.query(MajorIncident).filter(
        MajorIncident.org_id == user.org_id,
        MajorIncident.status == MajorIncidentStatus.active,
    )
    count = query.count()
    lage = query.order_by(MajorIncident.started_at.desc(), MajorIncident.id.desc()).first()
    return (build_gsl_live_payload(db, lage) if lage else None), count


def format_counts(counts: dict) -> str:
    return f"{counts['neu']} neu · {counts['in_arbeit']} in Arbeit · {counts['erledigt']} erledigt"
