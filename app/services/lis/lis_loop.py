"""Background-Loop für die LIS/IPR-Anbindung (Poll-Intervall konfigurierbar).

Muster: weather_alert_loop.py — globaler Kill-Switch + pro-Org-Filter über
OrgLisConfig.enabled. Ein Org-Fehler blockiert nie den Zyklus für andere Orgs.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.lis.loop")

_DEFAULT_INTERVAL_S = 30


async def lis_poll_loop() -> None:
    from app.config import settings
    if not getattr(settings, "LIS_ENABLED", True):
        logger.info("lis_poll_loop: deaktiviert (LIS_ENABLED=False)")
        return

    interval = getattr(settings, "LIS_POLL_INTERVAL_S", _DEFAULT_INTERVAL_S)
    logger.info("lis_poll_loop gestartet (Intervall %ds)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            with iteration_watch(logger, "lis_poll_loop", interval):
                await _run_all_orgs()
        except asyncio.CancelledError:
            logger.info("lis_poll_loop beendet")
            break
        except Exception:
            logger.exception("lis_poll_loop: Iteration fehlgeschlagen")


async def _run_all_orgs() -> None:
    def _lade_paare() -> list[tuple[int, int]]:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.lis import OrgLisConfig
        from app.models.master import FireDept
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            configs = (
                db.query(OrgLisConfig)
                .filter(OrgLisConfig.enabled == True)  # noqa: E712
                .all()
            )
            # (org, config) Paare vorab auflösen, bevor die Session je Org neu geöffnet wird
            pairs = []
            for cfg in configs:
                org = db.get(FireDept, cfg.org_id)
                if org:
                    pairs.append((org.id, cfg.id))
            return pairs
        finally:
            db.close()

    pairs = await asyncio.to_thread(_lade_paare)

    for org_id, config_id in pairs:
        try:
            await _sync_one_org(org_id, config_id)
        except Exception:
            logger.exception("lis_poll_loop: Org %s fehlgeschlagen", org_id)


async def _sync_one_org(org_id: int, config_id: int) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.lis import OrgLisConfig
    from app.models.master import FireDept
    from app.services.lis.lis_sync import sync_organization

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, org_id)
        config = db.get(OrgLisConfig, config_id)
        if not org or not config or not config.enabled:
            return
        await sync_organization(db, org, config)
    finally:
        db.close()
