from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.core.timezones import to_org_tz
from app.models.incident import Incident
from app.models.major_incident import IncidentSite, MajorIncident
from app.models.master import AlarmType, FireDept
from app.models.wordpress_report import WordPressReportConfig

logger = logging.getLogger("einsatzleiter.wordpress_report")


@dataclass(frozen=True)
class WordPressReportResult:
    success: bool
    post_id: int | None
    edit_url: str | None
    error: str | None
    already_existed: bool


async def post_incident_report(db: Session, incident: Incident) -> WordPressReportResult:
    if incident.wp_report_post_id is not None:
        return WordPressReportResult(
            success=True,
            post_id=incident.wp_report_post_id,
            edit_url=incident.wp_report_edit_url,
            error=None,
            already_existed=True,
        )

    cfg = db.query(WordPressReportConfig).filter(
        WordPressReportConfig.org_id == incident.primary_org_id
    ).first()
    if not cfg or not cfg.enabled or not cfg.webhook_url or not cfg.webhook_token:
        return WordPressReportResult(
            success=False,
            post_id=None,
            edit_url=None,
            error="Website-Anbindung ist für diese Organisation nicht konfiguriert.",
            already_existed=False,
        )

    # Großschadenslage: nur EIN Bericht für die ganze Lage, nicht einer je Einsatzstelle.
    # Ist dieser Einsatz einer Lage als Einsatzstelle zugeordnet und hat eine ANDERE
    # Einsatzstelle derselben Lage bereits einen Bericht, wird dessen Post übernommen
    # statt ein zweiter angelegt — kein Webhook-Aufruf in diesem Fall. Analoges Muster
    # zu teams_alarm_service.py::post_incident_card() (TeamsAlarmConfig.
    # suppress_card_in_major_incident), hier aber ohne Org-Opt-in: für Website-Entwürfe
    # gibt es keinen plausiblen Grund, mehrere Beiträge für dieselbe Lage zu wollen.
    major_incident_id = (
        db.query(IncidentSite.major_incident_id)
        .filter(IncidentSite.incident_id == incident.id)
        .execution_options(include_all_tenants=True)
        .scalar()
    )
    major_incident = None
    if major_incident_id is not None:
        sibling = (
            db.query(Incident)
            .join(IncidentSite, IncidentSite.incident_id == Incident.id)
            .filter(
                IncidentSite.major_incident_id == major_incident_id,
                Incident.wp_report_post_id.isnot(None),
            )
            .execution_options(include_all_tenants=True)
            .first()
        )
        if sibling is not None:
            incident.wp_report_post_id = sibling.wp_report_post_id
            incident.wp_report_edit_url = sibling.wp_report_edit_url
            db.commit()
            return WordPressReportResult(
                success=True,
                post_id=sibling.wp_report_post_id,
                edit_url=sibling.wp_report_edit_url,
                error=None,
                already_existed=True,
            )
        major_incident = db.get(MajorIncident, major_incident_id)

    org = db.query(FireDept).filter(FireDept.id == incident.primary_org_id).first()
    alarm_type = db.query(AlarmType).filter(
        AlarmType.org_id == incident.primary_org_id,
        AlarmType.code == incident.alarm_type_code,
    ).first()

    lage_name = (major_incident.name or "").strip() if major_incident else ""
    reason = (incident.reason or "").strip()
    alarm_label = (alarm_type.label or "").strip() if alarm_type else ""
    if lage_name:
        title = lage_name
    elif reason:
        title = reason
    elif alarm_label:
        title = alarm_label
    elif incident.lis_operation_number is not None:
        title = f"Einsatz Nr. {incident.lis_operation_number}"
    else:
        title = f"Einsatz {incident.id}"

    assigned_vehicle_ids = {
        vehicle.vehicle_master_id
        for vehicle in incident.vehicles
        if vehicle.removed_at is None and vehicle.vehicle_master
    }
    from app.services.pdf_service import load_fahrtenbuch_report
    _details, extra = load_fahrtenbuch_report(
        incident.id, assigned_vehicle_ids, db=db
    )
    fahrzeuge = [
        vehicle.vehicle_master.lis_reference_id
        for vehicle in incident.vehicles
        if vehicle.removed_at is None
        and vehicle.vehicle_master
        and vehicle.vehicle_master.lis_reference_id
    ]
    fahrzeuge += [
        item["vehicle"].lis_reference_id
        for item in extra
        if item["vehicle"].lis_reference_id
    ]
    payload: dict = {
        "incident_id": incident.id,
        "title": title,
        "fahrzeuge": list(dict.fromkeys(fahrzeuge)),
    }

    alarmzeit = to_org_tz(incident.started_at, org)
    if alarmzeit is not None:
        payload["alarmzeit"] = f"{alarmzeit:%Y-%m-%dT%H:%M}"
    einsatzende = to_org_tz(incident.closed_at, org)
    if einsatzende is not None:
        payload["einsatzende"] = f"{einsatzende:%Y-%m-%dT%H:%M}"
    if incident.started_at is not None and incident.closed_at is not None:
        payload["dauer_min"] = int((incident.closed_at - incident.started_at).total_seconds() / 60)

    street = (incident.address_street or "").strip()
    if street:
        incident_city = (incident.address_city or "").strip()
        org_city = (org.city or "").strip() if org else ""
        if incident_city.casefold() == org_city.casefold() and incident_city:
            payload["einsatzort"] = street
        else:
            payload["einsatzort"] = f"{incident_city} {street}".strip()

    if alarm_type and alarm_type.wp_einsatzart:
        payload["einsatzart"] = alarm_type.wp_einsatzart

    webhook_url = cfg.webhook_url.strip()
    webhook_token = cfg.webhook_token.strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{webhook_url}?token={webhook_token}", json=payload)
        response.raise_for_status()
        data = response.json()
        post_id = data.get("post_id") if isinstance(data, dict) else None
        if not isinstance(post_id, int):
            raise ValueError("Antwort enthält keine gültige post_id")
        edit_url = data.get("edit_url")
        if not isinstance(edit_url, str) or not edit_url.strip():
            raise ValueError("Antwort enthält keine gültige edit_url")
    except Exception as exc:
        logger.error(
            "WordPress-Bericht: Webhook-Fehler (Einsatz %s, Org %s): %s",
            incident.id, incident.primary_org_id, exc,
        )
        return WordPressReportResult(
            success=False,
            post_id=None,
            edit_url=None,
            error="Website-Entwurf konnte nicht erstellt werden. Bitte später erneut versuchen.",
            already_existed=False,
        )

    incident.wp_report_post_id = post_id
    incident.wp_report_edit_url = edit_url
    db.commit()
    return WordPressReportResult(
        success=True,
        post_id=post_id,
        edit_url=edit_url,
        error=None,
        already_existed=False,
    )
