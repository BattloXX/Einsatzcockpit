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
    db=None, alarm_type=None, org_settings=None,
) -> dict:
    """Baut eine Workflows-kompatible Adaptive Card aus denselben Daten wie die Mail.

    DB und Stammdaten sind optional, damit bestehende Aufrufer weiterhin nur mit
    Incident-Feldern arbeiten können. Inhalts-Schalter betreffen Bild und Links.
    """
    from app.services.einsatzinfo_mail_service import build_mail_view

    if alarm_type is None and db is not None and incident.primary_org_id:
        from app.services.alarm_service import get_alarm_type_by_code
        alarm_type = get_alarm_type_by_code(db, incident.primary_org_id, incident.alarm_type_code)
    v = build_mail_view(
        db, incident, org, alarm_type, org_settings, "", None, base_url=base_url,
    )
    badge_style = {"#c8102e": "attention", "#1d4ed8": "accent"}.get(v["kategorie_farbe"], "emphasis")

    def text_block(text: str, **options) -> dict:
        return {"type": "TextBlock", "text": text, "wrap": True, **options}

    body: list[dict] = []
    if v["is_exercise"]:
        body.append({
            "type": "Container", "style": "warning",
            "items": [text_block("[ÜBUNG] – kein Realeinsatz", weight="Bolder")],
        })
    heading = [text_block(f"Einsatz {v['stichwort']}", weight="Bolder", size="Large")]
    if v["stichwort_label"]:
        heading.append(text_block(v["stichwort_label"]))
    if v["alarmzeit"]:
        heading.append(text_block(f"{v['alarmzeit']} Uhr", isSubtle=True))
    body.append({
        "type": "ColumnSet", "columns": [
            {"type": "Column", "width": "auto", "items": [{
                "type": "Container", "style": badge_style,
                "items": [text_block(v["stichwort"], weight="Bolder", size="Large")],
            }]},
            {"type": "Column", "width": "stretch", "items": heading},
        ],
    })
    if v["adresse"]:
        body.append(text_block(v["adresse"], weight="Bolder", size="Large"))
    if v["meldung"]:
        body.append(text_block(v["meldung"], weight="Bolder", size="Medium"))
    if v["details"]:
        body.append({
            "type": "FactSet",
            "facts": [{"title": label, "value": value} for label, value in v["details"]],
        })
    if cfg.include_map and v["map_image_url"]:
        body.append({"type": "Image", "url": v["map_image_url"], "size": "Stretch", "altText": "Einsatzort"})

    actions = []
    if cfg.include_qr_link and v["info_link"]:
        actions.append({
            "type": "Action.OpenUrl", "title": "ℹ️ Einsatzinformation",
            "url": v["info_link"], "style": "positive",
        })
    if v["anmeldelink"]:
        actions.append({"type": "Action.OpenUrl", "title": "Mit PIN anmelden", "url": v["anmeldelink"]})
    if cfg.include_gmaps_link and v["navi_link"]:
        actions.append({"type": "Action.OpenUrl", "title": "🗺 Google Maps", "url": v["navi_link"]})
    if cfg.include_board_link and v["board_link"]:
        actions.append({"type": "Action.OpenUrl", "title": "🖥 Einsatz-Board öffnen", "url": v["board_link"]})

    adaptive_card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4", "msteams": {"width": "Full"},
        "body": body,
    }
    if actions:
        adaptive_card["actions"] = actions
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card}],
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
