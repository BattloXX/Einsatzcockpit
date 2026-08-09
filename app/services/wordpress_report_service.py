from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.core.timezones import to_org_tz
from app.models.incident import Incident
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

    org = db.query(FireDept).filter(FireDept.id == incident.primary_org_id).first()
    alarm_type = db.query(AlarmType).filter(
        AlarmType.org_id == incident.primary_org_id,
        AlarmType.code == incident.alarm_type_code,
    ).first()

    reason = (incident.reason or "").strip()
    alarm_label = (alarm_type.label or "").strip() if alarm_type else ""
    if reason:
        title = reason
    elif alarm_label:
        title = alarm_label
    elif incident.nummer is not None:
        title = f"Einsatz Nr. {incident.nummer}"
    else:
        title = f"Einsatz {incident.id}"

    payload: dict = {
        "incident_id": incident.id,
        "title": title,
        "fahrzeuge": [
            vehicle.vehicle_master.lis_reference_id
            for vehicle in incident.vehicles
            if vehicle.removed_at is None
            and vehicle.vehicle_master
            and vehicle.vehicle_master.lis_reference_id
        ],
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
        if edit_url is not None and not isinstance(edit_url, str):
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
