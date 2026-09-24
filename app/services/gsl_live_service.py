"""Kanonischer Live-Status einer laufenden Grossschadenslage."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.major_incident import (
    SITE_PHASE_GROUP,
    IncidentSite,
    MajorIncident,
    MajorIncidentStatus,
)

GSL_QUEUE_MAX_UPCOMING = 2


def _combined_site_address(site: IncidentSite) -> str:
    return f"{site.strasse or ''} {site.hausnr or ''}, {site.ort or ''}".strip(" ,")


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


def build_my_lage_queue(db: Session, device_token) -> dict | None:
    """Fahrzeugbezogene GSL-Warteschlange für das Einsatz-Widget.

    None, wenn kein Fahrzeug zugeordnet oder das Fahrzeug keiner aktiven
    Großschadenslage als LageEinheit zugeordnet ist.
    """
    if not device_token or not device_token.vehicle_master_id:
        return None

    from app.models.major_incident import EinheitSiteDispatch, LageEinheit
    from app.services.resource_service import STATUS_IM_EINSATZ

    einheit = (
        db.query(LageEinheit)
        .join(MajorIncident, MajorIncident.id == LageEinheit.lage_id)
        .filter(
            LageEinheit.vehicle_id == device_token.vehicle_master_id,
            LageEinheit.status == STATUS_IM_EINSATZ,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .order_by(LageEinheit.added_at.desc())
        .first()
    )
    if not einheit:
        return None
    lage = db.get(MajorIncident, einheit.lage_id)
    if not lage:
        return None

    dispatches = (
        db.query(EinheitSiteDispatch)
        .join(IncidentSite, IncidentSite.id == EinheitSiteDispatch.site_id)
        .filter(
            EinheitSiteDispatch.einheit_id == einheit.id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
        )
        .order_by(EinheitSiteDispatch.dispatched_at)
        .all()
    )
    if not dispatches:
        return None

    def _site_payload(site: IncidentSite) -> dict:
        return {
            "id": site.id,
            "bezeichnung": site.bezeichnung,
            "meldung": site.einsatzgrund,
            "address": _combined_site_address(site),
            "lat": site.lat,
            "lng": site.lng,
            "gmaps_url": (
                f"https://maps.google.com/?q={site.lat},{site.lng}"
                if site.lat is not None and site.lng is not None
                else None
            ),
            "priority": site.priority.name if site.priority else None,
            "phase": site.phase.value,
        }

    sites = [dispatch.site for dispatch in dispatches]
    current, upcoming = sites[0], sites[1 : 1 + GSL_QUEUE_MAX_UPCOMING]
    return {
        "lage_id": lage.id,
        "lage_name": lage.name,
        "lage_url": f"/lage/{lage.id}",
        "current": _site_payload(current),
        "upcoming": [_site_payload(site) for site in upcoming],
        "remaining_count": max(0, len(sites) - 1 - len(upcoming)),
    }


def format_counts(counts: dict) -> str:
    return f"{counts['neu']} neu · {counts['in_arbeit']} in Arbeit · {counts['erledigt']} erledigt"
