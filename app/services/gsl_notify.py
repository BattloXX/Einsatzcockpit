"""Benachrichtigung bei Ausrufung einer neuen Großschadenslage: einmaliger SMS- +
Teams-Sonderalarm ("Großschadenslage! Alle ins Gerätehaus einrücken") — unabhängig von der
stichwortbezogenen Einsatzinfo (siehe incident_notify.py für das analoge Muster je Einsatz).

MUSS erst NACH dem Commit der neu angelegten Lage aufgerufen werden (gleiche Begründung wie
bei notify_incident_created(): ein Rollback dürfte keinen bereits verschickten Sonderalarm
für eine nie existierende Lage erzeugen).
"""
from __future__ import annotations

import logging

from app.models.major_incident import MajorIncident

logger = logging.getLogger("einsatzleiter.gsl_notify")


async def notify_gsl_created(
    lage: MajorIncident,
    *,
    triggered_by_user_id: int | None = None,
    base_url: str | None = None,
    background_tasks=None,
) -> None:
    """Löst SMS- + Teams-Sonderalarm für eine neu ausgerufene Großschadenslage aus.

    - `background_tasks` gesetzt (Request-Kontext): Versand läuft wie bei
      notify_incident_created() als FastAPI-BackgroundTask nach der Response.
    - `background_tasks=None`: Versand läuft sofort, Fehler werden nur geloggt.
    - `base_url` wird nur für die Teams-Karte gebraucht (Link "Lage öffnen"). Ohne
      `base_url` wird der Teams-Versand übersprungen (SMS läuft trotzdem).
    """
    from app.services.sms_dispatch_service import dispatch_gsl_alarm
    from app.services.teams_alarm_service import post_gsl_alarm_card

    sms_args = (lage.org_id, lage.name, lage.is_exercise, triggered_by_user_id)
    teams_args = (lage.org_id, lage.id, lage.name, lage.is_exercise) if base_url else None

    if background_tasks is not None:
        background_tasks.add_task(dispatch_gsl_alarm, *sms_args)
        if teams_args is not None:
            background_tasks.add_task(post_gsl_alarm_card, *teams_args, base_url=base_url)
        return

    try:
        await dispatch_gsl_alarm(*sms_args)
    except Exception:
        logger.exception("GSL-Sonderalarm-SMS fehlgeschlagen (Lage %s)", lage.id)
    if teams_args is not None:
        try:
            await post_gsl_alarm_card(*teams_args, base_url=base_url)
        except Exception:
            logger.exception("GSL-Sonderalarm-Teams fehlgeschlagen (Lage %s)", lage.id)
