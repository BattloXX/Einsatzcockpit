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

from app.services.loop_utils import Backoff, iteration_watch, jittered

if TYPE_CHECKING:
    from app.services.dibos.dibos_client import DibosClient

logger = logging.getLogger("einsatzleiter.dibos.loop")

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
_next_poll_at: dict[int, float] = {}
_last_activity_at: dict[int, float] = {}
_startup_active_until: dict[int, float] = {}
_backoffs: dict[int, Backoff] = {}
_last_units_at: dict[int, float] = {}
_last_events_fingerprint: dict[int, str] = {}
_cached_units: dict[int, list[dict]] = {}


def _schedule_success(org_id: int, has_events: bool, started_at: float | None = None) -> None:
    from app.config import settings

    now = monotonic()
    _startup_active_until.setdefault(org_id, now + 60)
    if has_events:
        _last_activity_at[org_id] = now
    active = now < _startup_active_until[org_id] or (
        org_id in _last_activity_at
        and now - _last_activity_at[org_id] < settings.DIBOS_ACTIVE_WINDOW_S
    )
    interval = settings.dibos_active_interval() if active else settings.DIBOS_POLL_IDLE_INTERVAL_S
    _next_poll_at[org_id] = (now if started_at is None else started_at) + jittered(interval, settings.POLL_JITTER_PCT)



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

    interval = settings.dibos_active_interval()
    logger.info("dibos_poll_loop gestartet (Intervall %ds)", interval)
    try:
        while True:
            # Start-zu-Start; fällige Orgs dürfen nicht einen weiteren Tick warten.
            started = monotonic()
            try:
                with iteration_watch(logger, "dibos_poll_loop", interval * (1 + settings.POLL_JITTER_PCT)):
                    await _run_all_orgs()
            except Exception:
                logger.exception("dibos_poll_loop: Iteration fehlgeschlagen")
            now = monotonic()
            wake_at = started + jittered(interval, settings.POLL_JITTER_PCT)
            future_polls = [due for due in _next_poll_at.values() if due > now]
            if future_polls:
                wake_at = min(wake_at, min(future_polls))
            await asyncio.sleep(max(0.0, wake_at - now))
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
    org_ids = {org_id for org_id, _ in pairs}
    for stale_org_id in (set(_org_clients) | set(_next_poll_at)) - org_ids:
        await _close_org_client(stale_org_id)
        for state in (
            _next_poll_at, _last_activity_at, _startup_active_until, _backoffs,
            _last_units_at, _last_events_fingerprint, _cached_units,
            _previous_event_numbers, _last_public_events_at, _last_enrich_fingerprint,
        ):
            state.pop(stale_org_id, None)

    for org_id, config_id in pairs:
        if monotonic() < _next_poll_at.get(org_id, 0.0):
            continue
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


def _lade_konfiguration_sync(config_id: int) -> tuple | None:
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.dibos import OrgDibosConfig
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
            return (values, auto_trace_on_event, enrich_incidents, create_incidents,
                    wache_unid, auto_trace_duration_minutes)
    finally:
        db.close()
    return None


async def _check_org(org_id: int, config_id: int) -> None:
    from app.config import settings
    from app.services.dibos.dibos_capture import is_trace_running, start_trace_for_org
    from app.services.dibos.dibos_client import DibosClientError

    konfiguration = await asyncio.to_thread(_lade_konfiguration_sync, config_id)
    if konfiguration is None:
        await _close_org_client(org_id)
        return
    (values, auto_trace_on_event, enrich_incidents, create_incidents,
     wache_unid, auto_trace_duration_minutes) = konfiguration

    started_at = monotonic()
    _startup_active_until.setdefault(org_id, started_at + 60)
    client = await _get_client(org_id, values)
    backoff = _backoffs.setdefault(org_id, Backoff(settings.POLL_BACKOFF_MAX_S))
    try:
        events = await client.get_current_events()
        from app.services.dienst_monitor_service import record_probe
        await asyncio.to_thread(record_probe, org_id, "alarm_dibos", True)
    except DibosClientError as exc:
        from app.services.dienst_monitor_service import record_probe
        await asyncio.to_thread(record_probe, org_id, "alarm_dibos", False, str(exc))
        logger.exception("dibos_poll_loop: GetCurrentEvents fehlgeschlagen (Org %s)", org_id)
        backoff.failure()
        _next_poll_at[org_id] = monotonic() + jittered(backoff.delay(), settings.POLL_JITTER_PCT)
        # Session/Cookie evtl. ungültig — erst nach der Fehlerpause neu aufbauen
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
    backoff.success()
    _schedule_success(org_id, bool(events), started_at)
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
    events_fingerprint = _fingerprint(events)
    fetch_units = enrich_incidents and bool(events) and (
        bool(new_numbers)
        or events_fingerprint != _last_events_fingerprint.get(org_id)
        or monotonic() - _last_units_at.get(org_id, float("-inf")) >= settings.DIBOS_UNITS_MIN_INTERVAL_S
    )
    _last_events_fingerprint[org_id] = events_fingerprint

    async def _units() -> list[dict] | None:
        if not fetch_units:
            return _cached_units.get(org_id) if enrich_incidents and events else None
        try:
            _last_units_at[org_id] = monotonic()
            result = await client.get_current_units()
            _cached_units[org_id] = result
            return result
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

    if auto_trace_on_event and not await is_trace_running(org_id):
        logger.info(
            "dibos_poll_loop: Org %s hat %d aktive(n) Einsatz/Einsätze - starte Auto-Trace",
            org_id, len(events),
        )
        try:
            await start_trace_for_org(org_id, duration_minutes=auto_trace_duration_minutes)
        except ValueError:
            logger.exception("dibos_poll_loop: Auto-Trace-Start für Org %s fehlgeschlagen", org_id)
