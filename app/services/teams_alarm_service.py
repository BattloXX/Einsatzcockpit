"""Teams-Alarmierung: Dispatch-Entscheidung (Bot vs. Webhook-Fallback) + Versand.

Zweistufiges Modell (siehe Wiki "Administration-Teams-Alarmierung" und Plan):
- Basis-Modus: einfacher Teams-Kanal-Webhook, keine Interaktion, kein Azure nötig.
- Bot-Erweiterung (separater Schalter `bot_enabled`): sobald für ein Ziel (Alarm/Übung)
  eine Kanalbindung existiert, wird dieses Ziel automatisch über den Bot versendet
  (Zusagen/Absagen möglich) statt über den Webhook — fehlt die Bindung, greift
  automatisch der Webhook (kein Hard-Fail).

Die eigentliche Bot-Versand-Implementierung (teams_bot_service.py) folgt mit der
Bot-Framework-Anbindung; bis dahin läuft jedes Ziel ohne Kanalbindung transparent über
den Webhook-Pfad.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.master import FireDept
from app.models.teams_bot import TeamsAlarmConfig, TeamsChannelBinding
from app.services.teams_card import build_incident_message_card

logger = logging.getLogger("einsatzleiter.teams_alarm")


async def _post_via_webhook(webhook_url: str, incident: Incident, cfg: TeamsAlarmConfig,
                             *, base_url: str, org: FireDept | None) -> bool:
    import httpx

    if not webhook_url or not webhook_url.startswith("https://"):
        logger.warning("Teams-Alarmierung: Webhook-URL ungültig oder leer (Einsatz %s)", incident.id)
        return False

    payload = build_incident_message_card(incident, cfg, base_url=base_url, org=org)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Teams-Alarmierung: Webhook-Fehler (Einsatz %s): %s", incident.id, exc)
        return False


async def post_incident_card(db: Session, incident: Incident, *, base_url: str) -> None:
    """Postet die Alarmkarte für einen (neu angelegten) Einsatz — Bot bevorzugt, sonst
    Webhook-Fallback. No-op, wenn die Teams-Alarmierung für die Org deaktiviert ist oder
    kein Ziel konfiguriert ist. Fehler werden nur geloggt (best effort)."""
    if not incident.primary_org_id:
        return

    cfg = (
        db.query(TeamsAlarmConfig)
        .filter(TeamsAlarmConfig.org_id == incident.primary_org_id)
        .first()
    )
    if not cfg or not cfg.enabled:
        return
    if incident.is_exercise and not cfg.send_exercise:
        return

    from app.services.alarm_service import get_alarm_type_by_code
    alarm_type = get_alarm_type_by_code(db, incident.primary_org_id, incident.alarm_type_code)
    if alarm_type and not alarm_type.teams_alarm_enabled:
        return

    # Koordinaten werden ggf. von einem parallel laufenden Background-Task (Geocoding,
    # eigene DB-Session) NACH dem Laden dieses `incident`-Objekts gesetzt — ohne Refresh
    # sieht build_incident_message_card() hier noch lat=lng=None (stale Identity-Map) und
    # Kartenbild/Google-Maps-Button fehlen in der Karte, obwohl die Koordinaten längst in
    # der DB stehen (beobachtet 2026-07-05, Testeinsatz F3 "Flotzbachstraße 18").
    db.refresh(incident, attribute_names=["lat", "lng"])

    target = "uebung" if incident.is_exercise else "alarm"
    org = db.get(FireDept, incident.primary_org_id)

    binding = None
    if cfg.bot_enabled:
        binding = (
            db.query(TeamsChannelBinding)
            .filter(TeamsChannelBinding.org_id == incident.primary_org_id, TeamsChannelBinding.target == target)
            .first()
        )

    if binding:
        from app.services.teams_bot_service import post_incident_card_via_bot
        try:
            await post_incident_card_via_bot(db, incident, cfg, binding, base_url=base_url, org=org)
        except Exception:
            logger.exception(
                "Teams-Alarmierung: Bot-Versand fehlgeschlagen (Einsatz %s, Org %s)",
                incident.id, incident.primary_org_id,
            )
        return

    webhook_url = cfg.webhook_url_uebung if target == "uebung" else cfg.webhook_url_alarm
    if not webhook_url:
        logger.debug(
            "Teams-Alarmierung: kein Webhook für Ziel '%s' konfiguriert (Org %s) — übersprungen",
            target, incident.primary_org_id,
        )
        return
    await _post_via_webhook(webhook_url, incident, cfg, base_url=base_url, org=org)
