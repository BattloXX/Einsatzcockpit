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

from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.teams_bot import TeamsAlarmConfig, TeamsChannelBinding
from app.services.sms_dispatch_service import _DEFAULT_GSL_ALARM_TEXT
from app.services.teams_card import build_gsl_alarm_card, build_incident_message_card

logger = logging.getLogger("einsatzleiter.teams_alarm")


async def _post_payload(webhook_url: str, payload: dict, *, log_label: str) -> bool:
    import httpx

    if not webhook_url or not webhook_url.startswith("https://"):
        logger.warning("Teams-Alarmierung: Webhook-URL ungültig oder leer (%s)", log_label)
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Teams-Alarmierung: Webhook-Fehler (%s): %s", log_label, exc)
        return False


async def _post_via_webhook(webhook_url: str, incident: Incident, cfg: TeamsAlarmConfig,
                             *, base_url: str, org: FireDept | None) -> bool:
    payload = build_incident_message_card(incident, cfg, base_url=base_url, org=org)
    return await _post_payload(webhook_url, payload, log_label=f"Einsatz {incident.id}")


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


async def post_gsl_alarm_card(
    org_id: int, lage_id: int, lage_name: str, is_exercise: bool, *, base_url: str,
) -> None:
    """Postet den Großschadenslage-Sonderalarm bei Ausrufung einer neuen Lage — unabhängig
    von der stichwortbezogenen Einsatzkarte (post_incident_card()): eigener, schlanker
    Kartentyp (build_gsl_alarm_card()), nicht durch den Stichwort-Filter
    (AlarmType.teams_alarm_enabled) einschränkbar, da eine Großschadenslage per Definition
    immer relevant ist. Läuft, wie dispatch_gsl_alarm() für die SMS-Seite, mit einer eigenen
    DB-Session, um unabhängig vom aufrufenden Request-Lifecycle zu funktionieren.

    Immer über den einfachen Webhook (kein Bot-Versand) — der Sonderalarm braucht keine
    Zusage/Absage-Buttons.
    """
    db = SessionLocal()
    try:
        cfg = db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == org_id).first()
        if not cfg or not cfg.enabled:
            return
        if is_exercise and not cfg.send_exercise:
            return

        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if org_settings and not org_settings.gsl_alarm_enabled:
            return

        webhook_url = cfg.webhook_url_uebung if is_exercise else cfg.webhook_url_alarm
        if not webhook_url:
            logger.debug(
                "GSL-Alarm: kein Webhook konfiguriert (Org %s) — übersprungen", org_id,
            )
            return

        exercise_prefix = "[UEBUNG] " if is_exercise else ""
        text = exercise_prefix + (
            (org_settings.gsl_alarm_text if org_settings else None) or _DEFAULT_GSL_ALARM_TEXT
        ).replace("{lage}", lage_name)

        payload = build_gsl_alarm_card(
            lage_id, lage_name, text, is_exercise=is_exercise, base_url=base_url,
        )
        await _post_payload(webhook_url, payload, log_label=f"Lage {lage_id}")
    finally:
        db.close()
