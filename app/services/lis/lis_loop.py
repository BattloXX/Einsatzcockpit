"""Background-Loop für die LIS/IPR-Anbindung mit persistenten Clients je Org."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from time import monotonic
from typing import TYPE_CHECKING, cast

from app.services.loop_utils import Backoff, iteration_watch, jittered

if TYPE_CHECKING:
    from app.services.lis.lis_client import LisClient

logger = logging.getLogger("einsatzleiter.lis.loop")

_TICK_INTERVAL_S = 5
_org_clients: dict[int, tuple[str, LisClient]] = {}
_next_poll_at: dict[int, float] = {}
_last_activity_at: dict[int, float] = {}
_startup_active_until: dict[int, float] = {}
_backoffs: dict[int, Backoff] = {}


def _schedule_success(
    org_id: int, has_events: bool, interval: int, started_at: float, *, has_active_incident: bool = False,
) -> None:
    from app.config import settings

    now = monotonic()
    if has_events:
        _last_activity_at[org_id] = now
    active = has_active_incident or now < _startup_active_until.setdefault(org_id, started_at + 60) or (
        org_id in _last_activity_at
        and now - _last_activity_at[org_id] < settings.LIS_ACTIVE_WINDOW_S
    )
    interval = max(20, interval)
    if not active:
        interval = max(settings.LIS_POLL_IDLE_INTERVAL_S, interval)
    _backoffs.setdefault(org_id, Backoff(settings.POLL_BACKOFF_MAX_S)).success()
    _next_poll_at[org_id] = started_at + jittered(interval, settings.POLL_JITTER_PCT)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def _close_org_client(org_id: int) -> None:
    entry = _org_clients.pop(org_id, None)
    if entry is not None:
        try:
            await entry[1].aclose()
        except Exception:
            logger.exception("lis_poll_loop: Client für Org %s konnte nicht geschlossen werden", org_id)


async def _close_all_clients() -> None:
    for org_id in list(_org_clients):
        await _close_org_client(org_id)


async def _get_client(org_id: int, values: tuple) -> LisClient:
    from app.services.lis.lis_client import LisClient

    # Ohne Klartext-Passwort: password_enc deckt Passwortänderungen bereits ab.
    fingerprint = _fingerprint(values[:3] + values[4:])
    entry = _org_clients.get(org_id)
    if entry is not None and entry[0] == fingerprint:
        return entry[1]
    await _close_org_client(org_id)
    base_url, site, username, password, _password_enc, project_id, password_is_hash, organization_id = values
    client = LisClient(
        base_url, site, username, password, project_id=project_id,
        password_is_hash=password_is_hash, organization_id=organization_id,
    )
    _org_clients[org_id] = (fingerprint, client)
    return client


async def lis_poll_loop() -> None:
    from app.config import settings
    if not getattr(settings, "LIS_ENABLED", True):
        logger.info("lis_poll_loop: deaktiviert (LIS_ENABLED=False)")
        return
    logger.info("lis_poll_loop gestartet (Takt %ds, Intervall je Org)", _TICK_INTERVAL_S)
    try:
        while True:
            started = monotonic()
            try:
                with iteration_watch(logger, "lis_poll_loop", _TICK_INTERVAL_S):
                    await _run_all_orgs()
            except Exception:
                logger.exception("lis_poll_loop: Iteration fehlgeschlagen")
            await asyncio.sleep(max(0.0, _TICK_INTERVAL_S - (monotonic() - started)))
    except asyncio.CancelledError:
        logger.info("lis_poll_loop beendet")
        raise
    finally:
        await _close_all_clients()


async def _run_all_orgs() -> None:
    def _load_pairs() -> list[tuple[int, int]]:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.lis import OrgLisConfig
        from app.models.master import FireDept
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            configs = db.query(OrgLisConfig).filter(OrgLisConfig.enabled == True).all()  # noqa: E712
            return [(org.id, cfg.id) for cfg in configs if (org := db.get(FireDept, cfg.org_id))]
        finally:
            db.close()

    pairs = await asyncio.to_thread(_load_pairs)
    for stale_org_id in (set(_org_clients) | set(_next_poll_at)) - {org_id for org_id, _ in pairs}:
        await _close_org_client(stale_org_id)
        for state in (_next_poll_at, _last_activity_at, _startup_active_until, _backoffs):
            state.pop(stale_org_id, None)
    for org_id, config_id in pairs:
        if monotonic() < _next_poll_at.get(org_id, 0.0):
            continue
        try:
            await _sync_one_org(org_id, config_id)
        except Exception:
            from app.services.dienst_monitor_service import record_probe
            await asyncio.to_thread(record_probe, org_id, "alarm_lis", False, "LIS-Poll fehlgeschlagen.")
            logger.exception("lis_poll_loop: Org %s fehlgeschlagen", org_id)


async def _bereite_poll_vor(org_id: int, schluessel: str, values: tuple) -> bool:
    from app.core.crypto import decrypt_secret
    from app.services.loop_thread_io import registriere_loop_client

    now = monotonic()
    if now < _next_poll_at.get(org_id, 0.0):
        return False
    values = values[:3] + (decrypt_secret(values[3]),) + values[4:]
    client = await _get_client(org_id, values)
    _startup_active_until.setdefault(org_id, now + 60)
    registriere_loop_client(schluessel, client)
    return True


def _synchronisiere_org_sync(org_id: int, config_id: int, schluessel: str) -> tuple[bool, bool, int] | None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.models.lis import OrgLisConfig
    from app.models.master import FireDept
    from app.services.lis.lis_sync import sync_organization
    from app.services.loop_thread_io import LoopClientBruecke, auf_event_loop, thread_ablauf

    # Eine Session fuer den gesamten bisherigen Ablauf, auch ueber HTTP hinweg.
    # Auf dem Hauptloop landen nur Konfigurationswerte und Client-Schluessel.
    with SessionLocal() as db:
        set_tenant_context(db, None)

        async def synchronisieren() -> tuple[bool, bool, int] | None:
            org = db.get(FireDept, org_id)
            config = db.get(OrgLisConfig, config_id)
            if not org or not config or not config.enabled or not config.is_fully_configured:
                await auf_event_loop(_close_org_client, org_id)
                return None
            assert config.base_url and config.username and config.password_enc and config.organization_id
            values = (
                config.base_url, config.site, config.username, config.password_enc, config.password_enc,
                config.project_id, config.password_is_hash, config.organization_id,
            )
            if not await auf_event_loop(
                _bereite_poll_vor, org_id, schluessel, values,
            ):
                return None
            delivered = await sync_organization(
                db, org, config, client=cast("LisClient", LoopClientBruecke(schluessel)),
            )
            if delivered is None:
                from app.services.lis.lis_client import LisClientError
                raise LisClientError("LIS-Poll fehlgeschlagen.")
            has_active_incident = not delivered and db.query(Incident.id).filter(
                Incident.primary_org_id == org_id,
                Incident.status == "active",
                Incident.lis_operation_id.isnot(None),
            ).first() is not None
            return delivered, has_active_incident, config.poll_interval_seconds

        return thread_ablauf(synchronisieren())


async def _sync_one_org(org_id: int, config_id: int) -> None:
    from app.config import settings
    from app.services.loop_thread_io import im_db_thread, loop_client_schluessel

    if monotonic() < _next_poll_at.get(org_id, 0.0):
        return
    try:
        started_at = monotonic()
        with loop_client_schluessel() as schluessel:
            result = await im_db_thread(_synchronisiere_org_sync, org_id, config_id, schluessel)
        if result is not None:
            _schedule_success(org_id, result[0], result[2], started_at, has_active_incident=result[1])
    except Exception:
        backoff = _backoffs.setdefault(org_id, Backoff(settings.POLL_BACKOFF_MAX_S))
        backoff.failure()
        _next_poll_at[org_id] = monotonic() + jittered(backoff.delay(), settings.POLL_JITTER_PCT)
        await _close_org_client(org_id)
        raise
