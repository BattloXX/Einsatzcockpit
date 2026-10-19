"""Gemeinsame Leitstellennummer-Regel fuer GSL-Projektionen."""

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.major_incident import IncidentSite


def incident_leitstellennummer(incident: Incident | None, org_id: int) -> str:
    """Nur echte Leitstellennummern aus Einsätzen der führenden Organisation."""
    if incident is None or incident.primary_org_id != org_id:
        return ""
    return (incident.lis_operation_number or "").strip()


def site_leitstellennummer(db: Session, lage, site: IncidentSite) -> str:
    if site.incident_id is None:
        return ""
    # IncidentSite hat keine Incident-Beziehung; bereits geladene Einsätze nutzen.
    incident = db.identity_map.get(db.identity_key(Incident, site.incident_id))
    if incident is None:
        incident = db.query(Incident).filter(
            Incident.id == site.incident_id,
            Incident.primary_org_id == lage.org_id,
        ).first()
    return incident_leitstellennummer(incident, lage.org_id)


def leitstellen_nummern_by_site(
    db: Session, lage, sites: list[IncidentSite]
) -> dict[int, str]:
    """Ermittelt echte Leitstellennummern verknüpfter Einsätze per Batch-Abfrage."""
    incident_ids = {site.incident_id for site in sites if site.incident_id is not None}
    if not incident_ids:
        return {}

    incident_nummern: dict[int, str] = {}
    for incident in (
        db.query(Incident)
        .filter(
            Incident.id.in_(incident_ids),
            Incident.primary_org_id == lage.org_id,
        )
        .all()
    ):
        nummer = incident_leitstellennummer(incident, lage.org_id)
        if nummer:
            incident_nummern[incident.id] = nummer
    return {
        site.id: incident_nummern[site.incident_id]
        for site in sites
        if site.incident_id in incident_nummern
    }

