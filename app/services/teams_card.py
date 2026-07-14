"""Aufbau des Karteninhalts für die Teams-Alarmierung.

Zwei unterschiedliche Formate, je nach Versandweg (siehe teams_alarm_service.py):
- Webhook-Basis-Modus: Adaptive Card (in "attachments" gewrappt) — keine Interaktion
  möglich, aber Bild + Buttons.
- Bot-Erweiterung: Adaptive Card mit Action.Execute-Buttons (Zusagen/Absagen) — folgt mit
  der Bot-Framework-Anbindung (siehe Plan, Schritt 5/6); hier nur der Webhook-Baustein.

Hinweis: Microsoft hat die klassischen Office-365-Connector-Webhooks durch die
"Workflows"-App (Power Automate) ersetzt. Ein per Workflows erzeugter Webhook
akzeptiert zwar noch das alte "MessageCard"-Schema aus Kompatibilitätsgründen,
rendert davon aber NUR Titel/Text — "activityImage" und "potentialAction" (Kartenbild,
Maps-/Alarmübersicht-Buttons) werden dabei stillschweigend verworfen (beobachtet
2026-07-04: Karte kam nur mit Text an, obwohl Kartenbild/Maps-Link in der Konfiguration
aktiv waren). Das Adaptive-Card-Format über "attachments" wird sowohl von Workflows als
auch von den (auslaufenden) klassischen Connectors korrekt inkl. Bild/Buttons dargestellt.
"""
from __future__ import annotations

from app.core.timezones import format_local_datetime
from app.models.fahrtenbuch import Fahrt
from app.models.incident import Incident
from app.models.master import VehicleMaster
from app.models.teams_bot import TeamsAlarmConfig


def _combined_address(incident: Incident) -> str:
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


def build_incident_message_card(
    incident: Incident, cfg: TeamsAlarmConfig, *, base_url: str, org=None,
) -> dict:
    """Baut die Adaptive Card (in "attachments" gewrappt) für den Webhook-Basis-Modus —
    respektiert die include_map/include_gmaps_link/include_qr_link-Schalter aus
    TeamsAlarmConfig. Siehe Modul-Docstring zum Hintergrund (Workflows-Webhook verwirft
    Bild/Buttons im alten MessageCard-Schema)."""
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

    body: list[dict] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Large", "wrap": True},
        {"type": "TextBlock", "text": "\n\n".join(lines), "wrap": True},
    ]

    has_coords = incident.lat is not None and incident.lng is not None
    if cfg.include_map and has_coords and incident.alarm_token:
        body.append({
            "type": "Image",
            "url": f"{base_url}/api/v1/teams/map/{incident.alarm_token}.png",
            "size": "Stretch",
        })

    actions = []
    # Primäraktion: öffentliche Einsatzinformation (No-Login, Karte + Hydranten).
    # Der extern weitergeleitete Link geht immer hierher.
    if cfg.include_qr_link and incident.alarm_token:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "ℹ️ Einsatzinformation",
            "url": f"{base_url}/alarm/{incident.alarm_token}",
        })
    if cfg.include_gmaps_link and has_coords:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "🗺 Google Maps",
            "url": f"https://maps.google.com/?q={incident.lat},{incident.lng}",
        })
    if cfg.include_board_link:
        # Login-pflichtig (kein QR-Auto-Login wie die Alarmübersicht) — bewusst dieselbe
        # URL wie der Web-Push (siehe incident_notify.py), damit kein zusätzliches
        # Auth-Token in der Teams-Kanalhistorie landet.
        actions.append({
            "type": "Action.OpenUrl",
            "title": "🖥 Einsatz-Board öffnen",
            "url": f"{base_url}/einsatz/{incident.id}",
        })

    adaptive_card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if actions:
        adaptive_card["actions"] = actions

    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card},
        ],
    }


def build_schaden_message_card(
    fahrt: Fahrt, fahrzeug: VehicleMaster, *, betreff: str, foto_urls: list[str], detail_url: str | None,
) -> dict:
    """Baut die Adaptive Card für eine Schadensmeldung (siehe schaden_service.py).

    Ersetzt die frühere post_teams_karte()-MessageCard für diesen Anwendungsfall, weil
    NUR das Adaptive-Card-Format Bilder in Workflows-Webhooks zuverlässig rendert
    (siehe Modul-Docstring). foto_urls müssen bereits öffentliche https-URLs sein
    (signiert via app/core/security.py::sign_fahrt_foto_token) — Teams' Cloud ruft sie
    server-seitig ab und kann interne/http-URLs nicht laden."""
    betriebsfaehig_text = "Ja" if fahrt.schaden_betriebsfaehig else "Nein"
    lines = [
        f"**Fahrzeug:** {fahrzeug.code} {fahrzeug.kennzeichen or ''}".strip(),
        f"**Maschinist:** {fahrt.maschinist_name}",
        f"**Zeitpunkt:** {fahrt.zeitpunkt.strftime('%d.%m.%Y %H:%M')}",
        f"**Betriebsfähig:** {betriebsfaehig_text}",
    ]
    if fahrt.schaden_beschreibung:
        lines.append(f"**Beschreibung:** {fahrt.schaden_beschreibung}")

    body: list[dict] = [
        {"type": "TextBlock", "text": f"⚠️ {betreff}", "weight": "Bolder", "size": "Large", "wrap": True},
        {"type": "TextBlock", "text": "\n\n".join(lines), "wrap": True},
    ]
    if len(foto_urls) == 1:
        body.append({"type": "Image", "url": foto_urls[0], "size": "Stretch"})
    elif len(foto_urls) > 1:
        body.append({
            "type": "ImageSet",
            "imageSize": "Medium",
            "images": [{"type": "Image", "url": u} for u in foto_urls],
        })

    adaptive_card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if detail_url:
        adaptive_card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Fahrt öffnen", "url": detail_url},
        ]

    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card},
        ],
    }


def build_gsl_alarm_card(
    lage_id: int, lage_name: str, text: str, *, is_exercise: bool, base_url: str,
) -> dict:
    """Baut die Adaptive Card für den Großschadenslage-Sonderalarm — bewusst schlank
    (kein Kartenbild/Google-Maps, keine Stichwort-Toggles): einmaliger Sonderhinweis bei
    Ausrufung einer neuen Lage, siehe gsl_notify.py."""
    base_url = base_url.rstrip("/")
    exercise_prefix = "[ÜBUNG] " if is_exercise else ""
    adaptive_card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": f"{exercise_prefix}🚨 Großschadenslage",
             "weight": "Bolder", "size": "Large", "wrap": True},
            {"type": "TextBlock", "text": text, "wrap": True},
        ],
        "actions": [
            {"type": "Action.OpenUrl", "title": "🖥 Lage öffnen", "url": f"{base_url}/lage/{lage_id}"},
        ],
    }
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card},
        ],
    }
