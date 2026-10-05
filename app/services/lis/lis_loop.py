"""Background-Loop für die LIS/IPR-Anbindung mit persistenten Clients je Org."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from time import monotonic
from typing import TYPE_CHECKING

from app.services.loop_utils import iteration_watch

if TYPE_CHECKING:
    from app.services.lis.lis_client import LisClient

logger = logging.getLogger("einsatzleiter.lis.loop")

_TICK_INTERVAL_S = 5
_org_clients: dict[int, tuple[str, LisClient]] = {}
_last_started_at: dict[int, float] = {}


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def _close_org_client(org_id: int) -> None:
    entry = _org_clients.pop(org_id, None)
    if entry is not None:
        try:
            await entry[1].aclose()
        except Exception:
            logger.exception("lis_poll_loop: Client für Org %s konnte nicht geschlossen werden", org_id)
    _last_started_at.pop(org_id, None)


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
    for stale_org_id in set(_org_clients) - {org_id for org_id, _ in pairs}:
        await _close_org_client(stale_org_id)
    for org_id, config_id in pairs:
        try:
            await _sync_one_org(org_id, config_id)
        except Exception:
            logger.exception("lis_poll_loop: Org %s fehlgeschlagen", org_id)


async def _sync_one_org(org_id: int, config_id: int) -> None:
    from app.core.crypto import decrypt_secret
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
        if not org or not config or not config.enabled or not config.is_fully_configured:
            await _close_org_client(org_id)
            return
        now = monotonic()
        interval = max(10, config.poll_interval_seconds)
        if now - _last_started_at.get(org_id, float("-inf")) < interval:
            return
        assert config.base_url and config.username and config.password_enc and config.organization_id
        values = (
            config.base_url, config.site, config.username, decrypt_secret(config.password_enc), config.password_enc,
            config.project_id, config.password_is_hash, config.organization_id,
        )
        client = await _get_client(org_id, values)
        _last_started_at[org_id] = now
        await sync_organization(db, org, config, client=client)
    finally:
        db.close()
