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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.teams_bot import TeamsAlarmConfig, TeamsCardPost, TeamsChannelBinding
from app.services.sms_dispatch_service import _DEFAULT_GSL_ALARM_TEXT
from app.services.teams_card import build_gsl_alarm_card, build_incident_message_card

logger = logging.getLogger("einsatzleiter.teams_alarm")


def _card_base_url(base_url: str) -> str:
    """Öffentlich erreichbare Base-URL für die Teams-Karte.

    Teams rendert das Kartenbild server-seitig aus der Microsoft-Cloud und ruft dazu die
    Bild-URL selbst ab. `request.base_url` ist bei API-/LIS-Erzeugung aber oft eine interne
    Adresse (LAN/localhost, http), die Teams nicht laden kann → das Kartenbild fehlte
    (beobachtet 2026-07-05). Ist PUBLIC_BASE_URL konfiguriert, wird sie bevorzugt; sonst
    bleibt es beim übergebenen `base_url` (Verhalten wie bisher)."""
    from app.config import settings
    public = (settings.PUBLIC_BASE_URL or "").strip()
    resolved = public or base_url
    if resolved and not resolved.startswith("https://"):
        logger.warning(
            "Teams-Alarmierung: Karten-Base-URL '%s' ist nicht https/öffentlich — Teams kann "
            "das Kartenbild evtl. nicht laden. PUBLIC_BASE_URL auf die öffentliche https-Domain setzen.",
            resolved,
        )
    return resolved


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
    from app.services.exercise_guard import darf_extern
    if not darf_extern(
        "teams",
        is_exercise=incident.is_exercise,
        org_id=incident.primary_org_id,
        db=db,
    ):
        return

    from app.services.alarm_service import get_alarm_type_by_code
    alarm_type = get_alarm_type_by_code(db, incident.primary_org_id, incident.alarm_type_code)
    if alarm_type and not alarm_type.teams_alarm_enabled:
        return

    # Großschadenslage: nur EINE Karte für die ganze Lage, nicht eine je zugeordnetem
    # Einsatz (Org-Opt-in, siehe TeamsAlarmConfig.suppress_card_in_major_incident).
    # major_incident_id bleibt None für Einsätze außerhalb einer Lage — dort ändert sich
    # nichts (unverändertes bisheriges Verhalten).
    major_incident_id = None
    if cfg.suppress_card_in_major_incident:
        from app.services.major_incident_service import incident_major_incident_id
        major_incident_id = incident_major_incident_id(db, incident.id)
        if major_incident_id is not None:
            bereits_gesendet = (
                db.query(TeamsCardPost)
                .filter(
                    TeamsCardPost.org_id == incident.primary_org_id,
                    TeamsCardPost.major_incident_id == major_incident_id,
                )
                .first()
            )
            if bereits_gesendet is not None:
                logger.info(
                    "Teams-Alarmierung: Karte für Lage %s bereits gesendet — Einsatz %s übersprungen",
                    major_incident_id, incident.id,
                )
                return

    # Koordinaten werden ggf. von einem parallel laufenden Background-Task (Geocoding,
    # eigene DB-Session) NACH dem Laden dieses `incident`-Objekts gesetzt — ohne Refresh
    # sieht build_incident_message_card() hier noch lat=lng=None (stale Identity-Map) und
    # Kartenbild/Google-Maps-Button fehlen in der Karte, obwohl die Koordinaten längst in
    # der DB stehen (beobachtet 2026-07-05, Testeinsatz F3 "Flotzbachstraße 18").
    db.refresh(incident, attribute_names=["lat", "lng", "lis_operation_number"])

    # Kartenbild/Links müssen für Teams öffentlich erreichbar sein → ggf. PUBLIC_BASE_URL.
    base_url = _card_base_url(base_url)

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
        # KEIN _record_card_post() hier: post_incident_card_via_bot() ist aktuell nur ein
        # Platzhalter (siehe teams_bot_service.py-Docstring), der nie wirklich versendet —
        # ein Protokolleintrag würde eine tatsächlich verschickte Karte vortäuschen. Sobald
        # der echte Bot-Versand implementiert ist, muss er hier ebenfalls protokollieren
        # (analog zum Webhook-Pfad unten), sonst greift die Lage-Sperre für den Bot-Pfad nie.
        return

    webhook_url = cfg.webhook_url_uebung if target == "uebung" else cfg.webhook_url_alarm
    if not webhook_url:
        logger.debug(
            "Teams-Alarmierung: kein Webhook für Ziel '%s' konfiguriert (Org %s) — übersprungen",
            target, incident.primary_org_id,
        )
        return
    sent = await _post_via_webhook(webhook_url, incident, cfg, base_url=base_url, org=org)
    if sent and major_incident_id is not None:
        _record_card_post(db, incident, target=target, conversation_id=webhook_url, major_incident_id=major_incident_id)


def _record_card_post(
    db: Session, incident: Incident, *, target: str, conversation_id: str, major_incident_id: int,
) -> None:
    """Protokolliert eine erfolgreich versendete Karte für die Lage-Sperre
    (suppress_card_in_major_incident) — Savepoint, damit ein Race zwischen zwei
    Einsätzen derselben Lage (beide finden noch keinen bereits_gesendet-Eintrag)
    nur diesen einen Log-Eintrag verwirft, statt die aufrufende Transaktion zu
    beschädigen (Muster: dibos_enrich._sync_dibos_comments())."""
    try:
        with db.begin_nested():
            db.add(TeamsCardPost(
                org_id=incident.primary_org_id, incident_id=incident.id, target=target,
                conversation_id=conversation_id[:300], major_incident_id=major_incident_id,
            ))
            db.flush()
    except IntegrityError:
        logger.info(
            "Teams-Alarmierung: Karte für Lage %s bereits von einem parallelen Versand "
            "protokolliert (Einsatz %s) — kein doppelter Log-Eintrag nötig",
            major_incident_id, incident.id,
        )


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
        from app.services.exercise_guard import darf_extern
        if not darf_extern("teams", is_exercise=is_exercise, org_id=org_id, db=db):
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
