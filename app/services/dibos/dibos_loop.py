"""Background-Loop für die DIBOS-EventHub-Auto-Erkennung (Poll-Intervall konfigurierbar).

Fragt leichtgewichtig `Main/GetCurrentEvents` ab (die eigenen aktiven Einsätze
der Org) und tut damit — je nach Org-Konfiguration — eines oder mehrere:

1. auto_trace_on_event: startet, sobald die Liste nicht mehr leer ist, einen
   vollständigen Trace (dibos_capture.py::start_trace_for_org), der Rohdaten
   auf Platte aufzeichnet. Der Trace ist reine Diagnose und schreibt nichts in
   die DB — dieser Loop pollt währenddessen unverändert weiter.
2. enrich_incidents: reichert einen bereits bestehenden Einsatz direkt aus
   diesem leichten Poll an (dibos_enrich.enrich_and_broadcast).
3. create_incidents: legt für ein Event ohne zuordenbaren Einsatz selbst einen
   neuen an (ebenfalls über dibos_enrich.enrich_and_broadcast, Matching über
   die Leitstellennummer) — Ersatz für die LIS/IPR-Anbindung, sobald diese
   abgeschaltet wird. Unabhängig von enrich_incidents aktivierbar.

Auf schnelle Einsatzanlage optimiert (Analyse Mitschnitt Einsatz f26008764 vom
04.10.2026):

- Fast Path: Taucht eine neue Leitstellennummer auf, wird SOFORT nur mit
  GetCurrentEvents angelegt/alarmiert — GetCurrentUnits und GetPublicEvents
  kommen erst danach (parallel) und fließen in einen zweiten, idempotenten
  Anreicherungs-Durchlauf.
- Ein DibosClient pro Org bleibt über die Polls erhalten: Ohne Session-Cookie
  liefert DIBOS beim ersten Request immer 401 (siehe dibos_client.py) — ein
  Client pro Poll hieß bisher doppelte Latenz + neuer TLS-Handshake je Poll.
- GetPublicEvents (~490 KB je Abruf, 15 landesweite Events inkl. vollem
  Kommentarprotokoll) wird nur noch für das Auto-Schließen geholt: wenn ein
  aktiver Einsatz der Org nicht mehr in GetCurrentEvents steht. Im Mitschnitt
  verschwand das Event im selben Zyklus aus GetCurrentEvents, in dem es in
  GetPublicEvents als "closed" auftauchte. Bisher: bei jedem Poll, auch im
  Leerlauf.
- Unveränderte Daten (im Mitschnitt 68 von 108 Polls mit Einsatz) lösen keinen
  erneuten Anreicherungs-Durchlauf aus.

Muster: app/services/lis/lis_loop.py — globaler Kill-Switch + pro-Org-Filter,
ein Org-Fehler blockiert nie den Zyklus für andere Orgs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from time import monotonic
from typing import TYPE_CHECKING

from app.services.loop_utils import iteration_watch

if TYPE_CHECKING:
    from app.services.dibos.dibos_client import DibosClient

logger = logging.getLogger("einsatzleiter.dibos.loop")

_DEFAULT_INTERVAL_S = 20

# Solange ein aktiver Einsatz in GetCurrentEvents fehlt (z.B. LIS-Einsatz ohne
# DIBOS-Pendant, oder das Event ist aus den 15 neuesten GetPublicEvents
# herausgerutscht), höchstens so oft erneut nachsehen. Beim ERSTEN Verschwinden
# einer Nummer wird sofort abgefragt.
_PUBLIC_EVENTS_MIN_INTERVAL_S = 60

# Zustand pro Org (In-Memory, geht bei Neustart verloren — dann gilt der erste
# Poll als "alles neu", was nur einen zusätzlichen idempotenten Durchlauf kostet).
_org_clients: dict[int, tuple[str, DibosClient]] = {}
_previous_event_numbers: dict[int, set[str]] = {}
_last_public_events_at: dict[int, float] = {}
_last_enrich_fingerprint: dict[int, str] = {}


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def _close_org_client(org_id: int) -> None:
    entry = _org_clients.pop(org_id, None)
    if entry is None:
        return
    try:
        await entry[1].aclose()
    except Exception:
        logger.exception("dibos_poll_loop: Client für Org %s konnte nicht geschlossen werden", org_id)


async def _close_all_clients() -> None:
    for org_id in list(_org_clients):
        await _close_org_client(org_id)


async def dibos_poll_loop() -> None:
    from app.config import settings
    if not getattr(settings, "DIBOS_TRACE_ENABLED", True):
        logger.info("dibos_poll_loop: deaktiviert (DIBOS_TRACE_ENABLED=False)")
        return

    interval = getattr(settings, "DIBOS_POLL_INTERVAL_S", _DEFAULT_INTERVAL_S)
    logger.info("dibos_poll_loop gestartet (Intervall %ds)", interval)
    try:
        while True:
            # Fester Takt (Start-zu-Start) statt "Arbeit + volles Intervall": die
            # Arbeitszeit eines Zyklus verlängert sonst jedes Erkennungsfenster.
            started = monotonic()
            try:
                with iteration_watch(logger, "dibos_poll_loop", interval):
                    await _run_all_orgs()
            except Exception:
                logger.exception("dibos_poll_loop: Iteration fehlgeschlagen")
            await asyncio.sleep(max(0.0, interval - (monotonic() - started)))
    except asyncio.CancelledError:
        logger.info("dibos_poll_loop beendet")
        raise
    finally:
        await _close_all_clients()


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
            # Org braucht mindestens EINE der drei Fähigkeiten, sonst gibt es für
            # diesen Loop nichts zu tun (siehe Modul-Docstring: auto_trace_on_event,
            # enrich_incidents, create_incidents — unabhängig voneinander aktivierbar).
            configs = (
                db.query(OrgDibosConfig)
                .filter(OrgDibosConfig.enabled == True)  # noqa: E712
                .filter(or_(
                    OrgDibosConfig.auto_trace_on_event == True,  # noqa: E712
                    OrgDibosConfig.enrich_incidents == True,  # noqa: E712
                    OrgDibosConfig.create_incidents == True,  # noqa: E712
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

    # Clients von Orgs schließen, die nicht mehr pollen (deaktiviert/gelöscht)
    for stale_org_id in set(_org_clients) - {org_id for org_id, _ in pairs}:
        await _close_org_client(stale_org_id)

    for org_id, config_id in pairs:
        try:
            await _check_org(org_id, config_id)
        except Exception:
            logger.exception("dibos_poll_loop: Org %s fehlgeschlagen", org_id)


async def _has_missing_active_incidents(
    org_id: int, event_numbers: set[str], ignored_event_numbers: set[str] | None = None,
) -> bool:
    """True, wenn ein aktiver Einsatz der Org eine Leitstellennummer hat, die
    nicht (mehr) in GetCurrentEvents steht — nur dann lohnt GetPublicEvents, um
    ein "closed" für das Auto-Schließen zu finden."""
    def _query() -> bool:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.incident import Incident
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            rows = (
                db.query(Incident.lis_operation_number)
                .filter(
                    Incident.primary_org_id == org_id,
                    Incident.status == "active",
                    Incident.lis_operation_number.isnot(None),
                )
                .all()
            )
            ignored_numbers = ignored_event_numbers or set()
            return any(
                number not in event_numbers and number not in ignored_numbers
                for (number,) in rows
            )
        finally:
            db.close()

    return await asyncio.to_thread(_query)


async def _get_client(org_id: int, values: tuple) -> DibosClient:
    """Liefert den persistenten Client der Org; baut ihn neu, wenn sich die
    Verbindungsdaten geändert haben. Der Fingerprint ist ein Hash — keine
    Klartext-Secrets im Modulzustand außer im Client selbst."""
    from app.services.dibos.dibos_client import DibosClient

    fingerprint = _fingerprint(values)
    entry = _org_clients.get(org_id)
    if entry is not None and entry[0] == fingerprint:
        return entry[1]
    await _close_org_client(org_id)
    base_url, host, ag, gateway_user, gateway_password, service_user, service_password = values
    client = DibosClient(
        base_url, gateway_user, gateway_password, service_user, service_password, host=host, ag=ag,
    )
    _org_clients[org_id] = (fingerprint, client)
    return client


async def _check_org(org_id: int, config_id: int) -> None:
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.dibos import OrgDibosConfig
    from app.services.dibos.dibos_capture import is_trace_running, start_trace_for_org
    from app.services.dibos.dibos_client import DibosClientError

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        config = db.get(OrgDibosConfig, config_id)
        if not config or not config.enabled or not config.is_fully_configured:
            config = None
        else:
            auto_trace_on_event = config.auto_trace_on_event
            enrich_incidents = config.enrich_incidents
            create_incidents = config.create_incidents
            wache_unid = config.wache_unid
            if not auto_trace_on_event and not enrich_incidents and not create_incidents:
                config = None  # nichts, was dieser Loop für die Org tun müsste
        if config is not None:
            assert (
                config.base_url and config.gateway_user and config.gateway_password_enc
                and config.service_user and config.service_password_enc
            )
            values = (
                config.base_url, config.host, config.ag,
                config.gateway_user, decrypt_secret(config.gateway_password_enc),
                config.service_user, decrypt_secret(config.service_password_enc),
            )
            auto_trace_duration_minutes = config.auto_trace_duration_minutes
    finally:
        db.close()
    if config is None:
        await _close_org_client(org_id)
        return

    client = await _get_client(org_id, values)
    try:
        events = await client.get_current_events()
        from app.services.dienst_monitor_service import record_probe
        record_probe(org_id, "alarm_dibos", True)
    except DibosClientError as exc:
        from app.services.dienst_monitor_service import record_probe
        record_probe(org_id, "alarm_dibos", False, str(exc))
        logger.exception("dibos_poll_loop: GetCurrentEvents fehlgeschlagen (Org %s)", org_id)
        # Session/Cookie evtl. ungültig — im nächsten Poll frisch aufbauen
        await _close_org_client(org_id)
        return

    from app.services.dibos.dibos_enrich import (
        enrich_and_broadcast,
        is_ignored_f30_probe_call,
        log_ignored_f30_probe_call,
    )

    ignored_events = [event for event in events if is_ignored_f30_probe_call(event)]
    events = [event for event in events if not is_ignored_f30_probe_call(event)]
    for event in ignored_events:
        log_ignored_f30_probe_call(event.get("eventNumber"))
    ignored_numbers = {str(event["eventNumber"]) for event in ignored_events if event.get("eventNumber")}
    numbers = {str(e["eventNumber"]) for e in events if e.get("eventNumber")}
    previous_numbers = _previous_event_numbers.get(org_id, set())
    new_numbers = numbers - previous_numbers
    disappeared_numbers = previous_numbers - numbers
    _previous_event_numbers[org_id] = numbers

    # Fast Path: neuer Einsatz → sofort anlegen + alarmieren, ohne auf Units/
    # PublicEvents zu warten (Wachenstatus/Auto-Close holt der zweite Durchlauf).
    fast_path = bool(new_numbers) and (enrich_incidents or create_incidents)
    fast_path_ok = False
    if fast_path:
        fast_path_ok = await enrich_and_broadcast(
            org_id, events, raw_public_events=None, raw_units=None,
            wache_unid=wache_unid, create_incidents=create_incidents,
        )

    need_public = (enrich_incidents or create_incidents) and await _has_missing_active_incidents(
        org_id, numbers, ignored_numbers,
    )
    public_due = need_public and (
        bool(disappeared_numbers)
        or monotonic() - _last_public_events_at.get(org_id, 0.0) >= _PUBLIC_EVENTS_MIN_INTERVAL_S
    )
    fetch_units = enrich_incidents and bool(events)

    async def _units() -> list[dict] | None:
        if not fetch_units:
            return None
        try:
            return await client.get_current_units()
        except DibosClientError:
            logger.exception("dibos_poll_loop: GetCurrentUnits fehlgeschlagen (Org %s)", org_id)
            return None

    async def _public() -> list[dict] | None:
        if not public_due:
            return None
        try:
            result = await client.get_public_events()
        except DibosClientError:
            logger.exception("dibos_poll_loop: GetPublicEvents fehlgeschlagen (Org %s)", org_id)
            return None
        _last_public_events_at[org_id] = monotonic()
        return result

    units, public_events = await asyncio.gather(_units(), _public())

    # Zweiter (bzw. regulärer) Durchlauf — übersprungen, wenn der Fast Path
    # schon alles hatte oder sich seit dem letzten erfolgreichen Durchlauf nichts
    # geändert hat.
    data_fingerprint = _fingerprint({"events": events, "units": units, "public_events": public_events})
    if fast_path_ok and units is None and public_events is None:
        _last_enrich_fingerprint[org_id] = data_fingerprint
    if (
        (events or public_events)
        and (enrich_incidents or create_incidents)
        and not (fast_path and units is None and public_events is None)
        and data_fingerprint != _last_enrich_fingerprint.get(org_id)
    ):
        ok = await enrich_and_broadcast(
            org_id, events, raw_public_events=public_events,
            raw_units=(units or []) if enrich_incidents else None,
            wache_unid=wache_unid, create_incidents=create_incidents,
        )
        if ok:
            _last_enrich_fingerprint[org_id] = data_fingerprint

    if not events:
        return

    if auto_trace_on_event and not is_trace_running(org_id):
        logger.info(
            "dibos_poll_loop: Org %s hat %d aktive(n) Einsatz/Einsätze - starte Auto-Trace",
            org_id, len(events),
        )
        try:
            await start_trace_for_org(org_id, duration_minutes=auto_trace_duration_minutes)
        except ValueError:
            logger.exception("dibos_poll_loop: Auto-Trace-Start für Org %s fehlgeschlagen", org_id)
