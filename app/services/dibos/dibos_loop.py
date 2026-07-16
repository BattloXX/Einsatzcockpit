"""Background-Loop für die DIBOS-EventHub-Auto-Erkennung (Poll-Intervall konfigurierbar).

Anders als lis_loop.py schreibt dieser Loop selbst NICHTS in die DB und zeichnet
auch selbst nichts auf — er fragt nur leichtgewichtig `Main/GetCurrentEvents` ab
(die eigenen aktiven Einsätze der Org) und startet, sobald diese Liste nicht mehr
leer ist, einen vollständigen Trace (dibos_capture.py::start_trace_for_org). So
wird ein echter Einsatz ohne manuelles Zutun aufgezeichnet, ohne dass ständig alle
Endpunkte abgefragt werden müssen.

Muster: app/services/lis/lis_loop.py — globaler Kill-Switch + pro-Org-Filter,
ein Org-Fehler blockiert nie den Zyklus für andere Orgs.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("einsatzleiter.dibos.loop")

_DEFAULT_INTERVAL_S = 20


async def dibos_poll_loop() -> None:
    from app.config import settings
    if not getattr(settings, "DIBOS_TRACE_ENABLED", True):
        logger.info("dibos_poll_loop: deaktiviert (DIBOS_TRACE_ENABLED=False)")
        return

    interval = getattr(settings, "DIBOS_POLL_INTERVAL_S", _DEFAULT_INTERVAL_S)
    logger.info("dibos_poll_loop gestartet (Intervall %ds)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            await _run_all_orgs()
        except asyncio.CancelledError:
            logger.info("dibos_poll_loop beendet")
            break
        except Exception:
            logger.exception("dibos_poll_loop: Iteration fehlgeschlagen")


async def _run_all_orgs() -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.dibos import OrgDibosConfig
    from app.models.master import FireDept

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        configs = (
            db.query(OrgDibosConfig)
            .filter(OrgDibosConfig.enabled == True)  # noqa: E712
            .filter(OrgDibosConfig.auto_trace_on_event == True)  # noqa: E712
            .all()
        )
        # (org, config) Paare vorab auflösen, bevor die Session je Org neu geöffnet wird
        pairs = []
        for cfg in configs:
            org = db.get(FireDept, cfg.org_id)
            if org:
                pairs.append((org.id, cfg.id))
    finally:
        db.close()

    for org_id, config_id in pairs:
        try:
            await _check_org(org_id, config_id)
        except Exception:
            logger.exception("dibos_poll_loop: Org %s fehlgeschlagen", org_id)


async def _check_org(org_id: int, config_id: int) -> None:
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.dibos import OrgDibosConfig
    from app.services.dibos.dibos_capture import is_trace_running, start_trace_for_org
    from app.services.dibos.dibos_client import DibosClient, DibosClientError

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        config = db.get(OrgDibosConfig, config_id)
        if not config or not config.enabled or not config.auto_trace_on_event:
            return
        if not config.is_fully_configured:
            return
        if is_trace_running(org_id):
            return
        assert (
            config.base_url and config.gateway_user and config.gateway_password_enc
            and config.service_user and config.service_password_enc
        )
        base_url, host, ag = config.base_url, config.host, config.ag
        gateway_user = config.gateway_user
        gateway_password = decrypt_secret(config.gateway_password_enc)
        service_user = config.service_user
        service_password = decrypt_secret(config.service_password_enc)
        auto_trace_duration_minutes = config.auto_trace_duration_minutes
    finally:
        db.close()

    client = DibosClient(base_url, gateway_user, gateway_password, service_user, service_password, host=host, ag=ag)
    try:
        events = await client.get_current_events()
    except DibosClientError:
        logger.exception("dibos_poll_loop: GetCurrentEvents fehlgeschlagen (Org %s)", org_id)
        return
    finally:
        await client.aclose()

    if not events:
        return

    logger.info(
        "dibos_poll_loop: Org %s hat %d aktive(n) Einsatz/Einsätze - starte Auto-Trace",
        org_id, len(events),
    )
    try:
        await start_trace_for_org(org_id, duration_minutes=auto_trace_duration_minutes)
    except ValueError:
        logger.exception("dibos_poll_loop: Auto-Trace-Start für Org %s fehlgeschlagen", org_id)
