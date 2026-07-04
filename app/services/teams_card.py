"""Aufbau des Karteninhalts für die Teams-Alarmierung.

Zwei unterschiedliche Formate, je nach Versandweg (siehe teams_alarm_service.py):
- Webhook-Basis-Modus: legacy "MessageCard" (wie teams_service.py::post_teams_karte,
  aber mit mehreren Buttons + optionalem Bild) — keine Interaktion möglich.
- Bot-Erweiterung: Adaptive Card mit Action.Execute-Buttons (Zusagen/Absagen) — folgt mit
  der Bot-Framework-Anbindung (siehe Plan, Schritt 5/6); hier nur der Webhook-Baustein.
"""
from __future__ import annotations

from app.core.timezones import format_local_datetime
from app.models.incident import Incident
from app.models.teams_bot import TeamsAlarmConfig


def _combined_address(incident: Incident) -> str:
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


def build_incident_message_card(
    incident: Incident, cfg: TeamsAlarmConfig, *, base_url: str, org=None,
) -> dict:
    """Baut die MessageCard für den Webhook-Basis-Modus — respektiert die
    include_map/include_gmaps_link/include_qr_link-Schalter aus TeamsAlarmConfig."""
    base_url = base_url.rstrip("/")
    exercise_prefix = "[ÜBUNG] " if incident.is_exercise else ""
    title = f"{exercise_prefix}🚒 Einsatz {incident.alarm_type_code}"
    address = _combined_address(incident)

    lines = [f"**Adresse:** {address}" if address else "**Adresse:** –"]
    if incident.report_text:
        lines.append(f"**Meldung:** {incident.report_text}")
    if incident.reason:
        lines.append(f"**Einsatzgrund:** {incident.reason}")
    if incident.started_at:
        lines.append(f"**Zeit:** {format_local_datetime(incident.started_at, org)} Uhr")

    section: dict = {
        "activityTitle": title,
        "activityText": "\n\n".join(lines),
    }

    has_coords = incident.lat is not None and incident.lng is not None
    if cfg.include_map and has_coords and incident.alarm_token:
        section["activityImage"] = f"{base_url}/api/v1/teams/map/{incident.alarm_token}.png"

    actions = []
    if cfg.include_gmaps_link and has_coords:
        actions.append({
            "@type": "OpenUri",
            "name": "🗺 Google Maps",
            "targets": [{"os": "default", "uri": f"https://maps.google.com/?q={incident.lat},{incident.lng}"}],
        })
    if cfg.include_qr_link and incident.alarm_token:
        actions.append({
            "@type": "OpenUri",
            "name": "📋 Alarmübersicht",
            "targets": [{"os": "default", "uri": f"{base_url}/alarm/{incident.alarm_token}"}],
        })

    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": title,
        "themeColor": "d42225",
        "sections": [section],
    }
    if actions:
        payload["potentialAction"] = actions
    return payload
