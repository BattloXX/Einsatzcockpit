"""Zentrale Benachrichtigung bei Einsatzanlage: SMS-Einsatzinfo + Web-Push (+ Teams,
sobald die Teams-Alarmierung umgesetzt ist).

Grund: bisher rief jeder der drei Erzeugungspfade (REST-API `api_v1.py`, manuelle UI
`ui_incident.py`, LIS/IPR-Hintergrund-Sync `lis_sync.py`) SMS/Push unabhaengig und
unterschiedlich vollstaendig auf — nur die REST-API loeste beides aus, die manuelle UI nur
SMS (kein Push), der LIS-Sync gar nichts. `notify_incident_created()` buendelt beides an
einer Stelle, damit alle drei Pfade konsistent alarmieren.

`create_incident()` selbst ist kein geeigneter Ort fuer diese Seiteneffekte: es laeuft vor
dem Commit (ein Rollback wuerde sonst bereits verschickte Benachrichtigungen ueber einen nie
existierenden Einsatz erzeugen), hat kein `BackgroundTasks`-Objekt zur Verfuegung, und der
LIS-Sync-Pfad laeuft ausserhalb eines Requests ganz ohne `BackgroundTasks`. Diese Funktion
wird daher explizit von den Routern/dem LIS-Loop **nach** dem Commit aufgerufen.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.incident import Incident

logger = logging.getLogger("einsatzleiter.incident_notify")


def _combined_address(incident: Incident) -> str:
    """Baut den `{adresse}`-Platzhalter-String — gleiches Format wie bisher in api_v1.py."""
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


async def notify_incident_created(
    db: Session,
    incident: Incident,
    *,
    org_id: int | None,
    triggered_by_user_id: int | None = None,
    push_url: str | None = None,
    base_url: str | None = None,
    background_tasks=None,
) -> None:
    """Loest SMS-Einsatzinfo + Web-Push + Teams-Alarmierung fuer einen neu angelegten
    Einsatz aus.

    MUSS erst nach dem Commit des Einsatzes aufgerufen werden.

    - `background_tasks` gesetzt (API-/UI-Request): Versand laeuft wie bisher als
      FastAPI-`BackgroundTask` nach der Response.
    - `background_tasks=None` (LIS-Hintergrund-Loop, kein Request-Kontext vorhanden):
      Versand laeuft sofort — SMS/Teams sind bereits async und werden direkt ge-awaitet;
      Push ist synchron und laeuft ueber `asyncio.to_thread`, damit der Poll-Loop nicht
      blockiert. Fehler werden in diesem Zweig nur geloggt, nie weitergeworfen (best
      effort, wie die bisherigen BackgroundTasks es implizit auch schon waren).
    - `base_url` wird nur für die Teams-Alarmierung gebraucht (absolute Links/Kartenbild-URL
      in der Karte). Ohne `base_url` wird der Teams-Versand übersprungen.
    """
    from app.services.push_service import notify_all, notify_org
    from app.services.sms_dispatch_service import dispatch_einsatzinfo
    from app.services.teams_alarm_service import post_incident_card

    address = _combined_address(incident)
    exercise_prefix = "[ÜBUNG] " if incident.is_exercise else ""
    push_title = f"{exercise_prefix}🚒 Einsatz: {incident.alarm_type_code}"
    push_body = address or incident.report_text or "Kein Ort angegeben"
    resolved_push_url = push_url or f"/einsatz/{incident.id}"

    # Explizit lose typisiert (Any/Callable[..., int]) statt aus der ersten Zuweisung
    # inferieren zu lassen -- notify_org (5 Args, org_id) und notify_all (4 Args, ohne
    # org_id) haben unterschiedliche Signaturen, push_func/push_args werden aber je nach
    # Zweig konsistent im jeweils passenden Paar gesetzt und weiter unten aufgerufen.
    push_func: Callable[..., int]
    push_args: tuple[Any, ...]
    if org_id:
        # Öffentlicher Einsatzinfo-Link (No-Login) für die SMS; request-loser Kontext →
        # settings.effective_public_base_url statt request.base_url.
        from app.config import settings
        info_link = (
            f"{settings.effective_public_base_url.rstrip('/')}/alarm/{incident.alarm_token}"
            if incident.alarm_token else ""
        )
        sms_args = (
            org_id, incident.alarm_type_code, address, incident.address_city,
            incident.report_text, incident.reason, incident.is_exercise,
            triggered_by_user_id, info_link,
        )
        push_args = (db, org_id, push_title, push_body, resolved_push_url)
        push_func = notify_org
    else:
        sms_args = None  # Einsatzinfo-SMS ist org-gebunden — ohne Org kein Versand
        push_args = (db, push_title, push_body, resolved_push_url)
        push_func = notify_all

    teams_args = (db, incident) if base_url else None

    if background_tasks is not None:
        if sms_args is not None:
            background_tasks.add_task(dispatch_einsatzinfo, *sms_args)
        background_tasks.add_task(push_func, *push_args)
        if teams_args is not None:
            background_tasks.add_task(post_incident_card, *teams_args, base_url=base_url)
        return

    # Kein Request-Kontext (LIS-Poll-Loop) — direkt ausfuehren statt background_tasks
    if sms_args is not None:
        try:
            await dispatch_einsatzinfo(*sms_args)
        except Exception:
            logger.exception("Einsatzinfo-SMS fehlgeschlagen (Einsatz %s)", incident.id)
    try:
        await asyncio.to_thread(push_func, *push_args)
    except Exception:
        logger.exception("Push-Benachrichtigung fehlgeschlagen (Einsatz %s)", incident.id)
    if teams_args is not None:
        assert base_url is not None  # teams_args ist nur gesetzt, wenn base_url vorhanden ist
        try:
            await post_incident_card(*teams_args, base_url=base_url)
        except Exception:
            logger.exception("Teams-Alarmierung fehlgeschlagen (Einsatz %s)", incident.id)
