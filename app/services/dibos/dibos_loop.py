"""Background-Loop für die DIBOS-EventHub-Auto-Erkennung (Poll-Intervall konfigurierbar).

Fragt leichtgewichtig `Main/GetCurrentEvents` ab (die eigenen aktiven Einsätze
der Org) und tut damit — je nach Org-Konfiguration — eines oder beides:

1. auto_trace_on_event: startet, sobald die Liste nicht mehr leer ist, einen
   vollständigen Trace (dibos_capture.py::start_trace_for_org), der Rohdaten
   auf Platte aufzeichnet. So wird ein echter Einsatz ohne manuelles Zutun
   aufgezeichnet, ohne dass ständig alle Endpunkte abgefragt werden müssen.
2. enrich_incidents: reichert einen bereits bestehenden Einsatz direkt aus
   diesem leichten Poll an (dibos_enrich.enrich_and_broadcast) — OHNE dass
   dafür eine Voll-Aufzeichnung laufen muss. Das ist die einzige Schreib-
   Aktion, die dieser Loop selbst auslöst (delegiert an dibos_enrich.py) und
   spart die Speicherlast einer Voll-Aufzeichnung für Orgs, die nur die
   Anreicherung wollen.

Läuft bereits ein Trace für die Org, überlässt dieser Loop ihm das Polling +
die Anreicherung (dessen eigener Poll-Zyklus deckt beides ab, siehe
dibos_capture.py::_capture_once()) und fragt selbst nichts erneut ab — sonst
würde GetCurrentEvents doppelt abgefragt.

Muster: app/services/lis/lis_loop.py — globaler Kill-Switch + pro-Org-Filter,
ein Org-Fehler blockiert nie den Zyklus für andere Orgs.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.loop_utils import iteration_watch

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
            with iteration_watch(logger, "dibos_poll_loop", interval):
                await _run_all_orgs()
        except asyncio.CancelledError:
            logger.info("dibos_poll_loop beendet")
            break
        except Exception:
            logger.exception("dibos_poll_loop: Iteration fehlgeschlagen")


async def _run_all_orgs() -> None:
    def _lade_paare() -> list[tuple[int, int]]:
        from sqlalchemy import or_

        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.dibos import OrgDibosConfig
        from app.models.master import FireDept
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            # Org braucht mindestens EINE der beiden Fähigkeiten, sonst gibt es für
            # diesen Loop nichts zu tun (siehe Modul-Docstring: auto_trace_on_event
            # UND/ODER enrich_incidents, unabhängig voneinander aktivierbar).
            configs = (
                db.query(OrgDibosConfig)
                .filter(OrgDibosConfig.enabled == True)  # noqa: E712
                .filter(or_(
                    OrgDibosConfig.auto_trace_on_event == True,  # noqa: E712
                    OrgDibosConfig.enrich_incidents == True,  # noqa: E712
                ))
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
        if not config or not config.enabled or not config.is_fully_configured:
            return
        auto_trace_on_event = config.auto_trace_on_event
        enrich_incidents = config.enrich_incidents
        if not auto_trace_on_event and not enrich_incidents:
            return  # nichts, was dieser Loop für die Org tun müsste
        if is_trace_running(org_id):
            # Ein laufender Trace deckt GetCurrentEvents + (falls aktiviert) die
            # Anreicherung bereits selbst ab (dibos_capture.py::_capture_once()) —
            # nicht doppelt abfragen.
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

    # Anreicherung: unabhängig von auto_trace_on_event, direkt aus diesem
    # leichten Poll — keine Voll-Aufzeichnung (und damit keine Rohdaten-Dateien
    # auf Platte) nötig, wenn eine Org nur das will.
    if enrich_incidents:
        from app.services.dibos.dibos_enrich import enrich_and_broadcast
        await enrich_and_broadcast(org_id, events)

    if auto_trace_on_event:
        logger.info(
            "dibos_poll_loop: Org %s hat %d aktive(n) Einsatz/Einsätze - starte Auto-Trace",
            org_id, len(events),
        )
        try:
            await start_trace_for_org(org_id, duration_minutes=auto_trace_duration_minutes)
        except ValueError:
            logger.exception("dibos_poll_loop: Auto-Trace-Start für Org %s fehlgeschlagen", org_id)
