"""Background-Loop für kontinuierliches Pegel-Polling (alle 10 Minuten je Org).

Ohne diesen Loop wurden Pegelmessungen nur abgerufen und in die Wetter-DB
geschrieben, wenn zufällig jemand eine Seite mit dem Wetter-Panel geöffnet hat
(refresh_all_for_org() wurde bislang ausschließlich aus ui_weather.py heraus
aufgerufen) — bei Inaktivität (z.B. nachts) entstanden dadurch Lücken im
24-h-Verlauf. Muster: weather_alert_loop.py.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.abfluss_poll_loop")


async def abfluss_poll_loop() -> None:
    from app.config import settings
    if not settings.ABFLUSS_POLL_ENABLED:
        logger.info("abfluss_poll_loop: deaktiviert (ABFLUSS_POLL_ENABLED=False)")
        return

    logger.info("abfluss_poll_loop gestartet (Intervall %ds)", settings.ABFLUSS_POLL_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(settings.ABFLUSS_POLL_INTERVAL_S)
            with iteration_watch(logger, "abfluss_poll_loop", settings.ABFLUSS_POLL_INTERVAL_S):
                await _poll_all_orgs()
        except asyncio.CancelledError:
            logger.info("abfluss_poll_loop beendet")
            break
        except Exception:
            logger.exception("abfluss_poll_loop: Iteration fehlgeschlagen")


async def _poll_all_orgs() -> None:
    from app.services import abfluss_service

    def _lade_orgs() -> list[tuple[int, list]]:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.master import OrgSettings
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            return [
                (o.org_id, o.abfluss_stationen_list)
                for o in db.query(OrgSettings).filter(OrgSettings.abfluss_stationen.isnot(None)).all()
            ]
        finally:
            db.close()

    orgs_stationen = await asyncio.to_thread(_lade_orgs)

    for org_id, stationen in orgs_stationen:
        if not stationen:
            continue
        try:
            await abfluss_service.refresh_all_for_org(org_id, stationen)
        except Exception:
            logger.exception("abfluss_poll_loop: Org %s fehlgeschlagen", org_id)
