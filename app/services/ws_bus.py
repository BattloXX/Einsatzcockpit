"""Redis-Hinweisbus mit Wiederverbindung; Gateway-Zustellung liegt dauerhaft in der DB.

Publisher liefert ausschliesslich ueber den Subscriber zu. Adressierte Gateway-Hinweise
verweisen auf eine atomar geclaimte DB-Nachricht; lokaler Send plus eigenes Redis-Echo
koennen deshalb keine zweite Socket-Zustellung erzeugen.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic

from app.config import settings

logger = logging.getLogger("einsatzleiter.ws_bus")

# Kanäle
CH_WS = "ec:ws"  # Browser-Broadcasts (ConnectionManager: Einsatz/Lage/Org)
CH_GW = "ec:gw"  # Print-&-Alarm-Gateway-Ebene (print_job, command, config_sync, job_status)
CH_LAGEDOKUMENT = "ec:lagedokument"  # Yjs-Sync-Updates (Lagedokument-Inhalt)
CH_LAGEDOKUMENT_AWARENESS = "ec:lagedokument-aw"  # Yjs-Awareness (Presence/Cursor)

_redis = None
_subscribed = False
_pubsub_task: asyncio.Task | None = None
_handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
_diagnostics: dict = {"publish_failures": 0, "subscriber_failures": 0, "reconnects": 0}
_publisher_ok = False
_last_warning: dict[str, float] = {}
HEALTH_INTERVAL_S = 5
HEALTH_TIMEOUT_S = 5


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _failure(code: str, *, channel: str | None = None) -> None:
    # Exceptiontexte koennen Redis-URLs, Tokens oder Handler-Nutzdaten enthalten.
    _diagnostics["last_error"] = code
    _diagnostics["last_error_at"] = _now()
    if monotonic() - _last_warning.get(code, float("-inf")) >= 30:
        logger.warning("ws_bus: %s (channel=%s)", code, channel if channel in _handlers else "-")
        _last_warning[code] = monotonic()


def enabled() -> bool:
    """True, wenn ein Redis-Bus konfiguriert ist (REDIS_URL gesetzt)."""
    return bool(getattr(settings, "REDIS_URL", ""))


def status() -> dict:
    """Status des Redis-Bus für die Admin-Anzeige (sync-sicher, ohne Live-Ping).

    Alle Felder gelten nur fuer diesen Worker. Die gemeinsame Anzeige aggregiert
    die ablaufenden DB-Heartbeats saemtlicher Worker."""
    running = _pubsub_task is not None and not _pubsub_task.done()
    subscriber = bool(_redis is not None and running and _subscribed)
    return {"configured": enabled(), "connected": subscriber and _publisher_ok,
            "subscriber_connected": subscriber, "publisher_ok": _publisher_ok,
            "channels": sorted(_handlers), **_diagnostics}


def register(channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
    """Registriert den lokalen Zusteller für einen Kanal (beim Import der Module).

    Muss vor ``start()`` erfolgen, damit ``start()`` alle Kanäle abonniert.
    """
    _handlers[channel] = handler


async def publish(channel: str, message: dict) -> bool:
    """True = Redis nahm den Hinweis fuer Subscriber an, niemals Auftragszustellung."""
    global _publisher_ok
    if _redis is None:
        if enabled():
            _diagnostics["publish_failures"] += 1
            _failure("publish_unavailable", channel=channel)
        return False
    try:
        count = await _redis.publish(channel, json.dumps(message, ensure_ascii=False, default=str))
        if not count:
            _diagnostics["publish_failures"] += 1
            _failure("publish_no_subscribers", channel=channel)
            return False
        _publisher_ok = True
        _diagnostics["last_publish_at"] = _now()
        return True
    except Exception:
        _publisher_ok = False
        _diagnostics["publish_failures"] += 1
        _failure("publish_failed", channel=channel)
        return False


async def _close(resource) -> None:
    if resource is not None:
        try:
            await asyncio.wait_for(resource.aclose(), timeout=2)
        except Exception:
            _failure("close_failed")


async def _connect():
    global _redis, _subscribed, _publisher_ok
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=5,
        socket_connect_timeout=2,
        socket_timeout=5,
    )
    pubsub = None
    try:
        await client.ping()
        pubsub = client.pubsub()
        channels = list(_handlers) or [CH_WS, CH_GW]
        await pubsub.subscribe(*channels)
        # subscribe() sendet nur den Befehl. Erst alle Redis-ACKs bestaetigen,
        # bevor Monitoring oder ein Test "connected" sehen darf.
        pending = set(channels)
        async with asyncio.timeout(5):
            while pending:
                message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1)
                if message and message.get("type") == "subscribe":
                    pending.discard(message.get("channel"))
                elif message and message.get("type") == "message":
                    await _dispatch(message)
    except BaseException:
        await _close(pubsub)
        await _close(client)
        raise
    _redis = client
    _subscribed = True
    _publisher_ok = True
    _diagnostics["last_connected_at"] = _now()
    _diagnostics["reconnects"] += 1
    return pubsub


async def start() -> None:
    global _pubsub_task
    if not enabled() or (_pubsub_task is not None and not _pubsub_task.done()):
        return
    pubsub = None
    try:
        pubsub = await _connect()
    except Exception:
        _failure("connect_failed")
    _pubsub_task = asyncio.create_task(_supervise(pubsub), name="redis-subscriber")


async def _supervise(pubsub) -> None:
    global _redis, _subscribed, _publisher_ok
    delay = 0.1
    while True:
        try:
            if pubsub is None:
                pubsub = await _connect()
            delay = 0.1
            await _reader(pubsub)
        except asyncio.CancelledError:
            raise
        except Exception:
            _diagnostics["subscriber_failures"] += 1
            _failure("subscriber_disconnected")
        finally:
            _subscribed = False
            _publisher_ok = False
            _diagnostics["last_disconnected_at"] = _now()
            client, _redis = _redis, None
            await _close(pubsub)
            pubsub = None
            await _close(client)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 5)


async def _reader(pubsub) -> None:
    """Auch ein stilles Netzloch erkennen: PING/PONG auf der Subscriber-Verbindung."""
    global _publisher_ok
    last_ping = monotonic()
    waiting_since: float | None = None
    while True:
        msg = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1)
        now = monotonic()
        if msg is not None:
            _diagnostics["last_subscriber_seen_at"] = _now()
            if msg.get("type") == "pong":
                waiting_since = None
            await _dispatch(msg)
        if waiting_since is not None and now - waiting_since >= HEALTH_TIMEOUT_S:
            raise TimeoutError("subscriber_heartbeat_timeout")
        if waiting_since is None and now - last_ping >= HEALTH_INTERVAL_S:
            if _redis is not None:
                await _redis.ping()
                _publisher_ok = True
            await pubsub.ping()
            last_ping = now
            waiting_since = now


async def _dispatch(msg: dict) -> None:
    if msg.get("type") != "message":
        return
    channel = msg.get("channel")
    if not isinstance(channel, str):
        return
    handler = _handlers.get(channel)
    if handler is None:
        return
    try:
        raw = msg.get("data")
        if not isinstance(raw, (str, bytes, bytearray)):
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return
    except (json.JSONDecodeError, TypeError):
        return
    try:
        await handler(payload)
    except Exception:
        _failure("handler_failed", channel=channel)


async def stop() -> None:
    """Beendet Subscriber-Task und Redis-Verbindung (im lifespan-Shutdown)."""
    global _pubsub_task, _redis, _subscribed, _publisher_ok
    _subscribed = False
    _publisher_ok = False
    if _pubsub_task is not None:
        _pubsub_task.cancel()
        try:
            await _pubsub_task
        except asyncio.CancelledError, Exception:
            pass
        _pubsub_task = None
    if _redis is not None:
        await _close(_redis)
        _redis = None
