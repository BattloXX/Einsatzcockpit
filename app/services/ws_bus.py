"""Redis-Hinweisbus mit Wiederverbindung; Gateway-Zustellung liegt dauerhaft in der DB.

Publisher liefert ausschliesslich ueber den Subscriber zu. Adressierte Gateway-Hinweise
verweisen auf eine atomar geclaimte DB-Nachricht; lokaler Send plus eigenes Redis-Echo
koennen deshalb keine zweite Socket-Zustellung erzeugen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic

from app.config import settings
from app.core.fehlerlog import korrelation_setzen, log_fehler
from app.services.gateway_presence import worker_id

logger = logging.getLogger("einsatzleiter.ws_bus")

# Kanäle
CH_WS = "ec:ws"  # Browser-Broadcasts (ConnectionManager: Einsatz/Lage/Org)
CH_GW = "ec:gw"  # Print-&-Alarm-Gateway-Ebene (print_job, command, config_sync, job_status)
CH_LAGEDOKUMENT = "ec:lagedokument"  # Yjs-Sync-Updates (Lagedokument-Inhalt)
CH_LAGEDOKUMENT_AWARENESS = "ec:lagedokument-aw"  # Yjs-Awareness (Presence/Cursor)

_redis = None
_subscribed = False
_subscriber = None
_publisher_task: asyncio.Task | None = None
_consumer_task: asyncio.Task | None = None
_queue: asyncio.Queue | None = None
_connected_since: float | None = None
_pubsub_task: asyncio.Task | None = None
_handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
_reconnect_callbacks: list[Callable[[], Awaitable[None]]] = []
_diagnostics: dict = {"publish_failures": 0, "subscriber_failures": 0, "reconnects": 0}
_publisher_ok = False
_last_warning: dict[str, float] = {}
HEALTH_INTERVAL_S = 5
HEALTH_TIMEOUT_S = 15
READ_TIMEOUT_S = 1
QUEUE_SIZE = 1000
BACKOFF_MIN_S = 0.1
BACKOFF_MAX_S = 5
STABLE_CONNECTION_S = 30


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _failure(code: str, exc: BaseException | None = None, *, channel: str | None = None) -> None:
    # Niemals Ausnahmetexte oder Redis-URLs in Diagnosefelder uebernehmen.
    _diagnostics["last_error"] = code
    _diagnostics["last_error_at"] = _now()
    if exc is not None:
        _diagnostics["last_exception_class"] = type(exc).__name__
    if monotonic() - _last_warning.get(code, float("-inf")) >= 30:
        log_fehler(logger, code, exc if exc is not None else RuntimeError(),
                   dauer_s=_diagnostics.get("connection_duration_s"), channel="redis")
        _last_warning[code] = monotonic()


def enabled() -> bool:
    """True, wenn ein Redis-Bus konfiguriert ist (REDIS_URL gesetzt)."""
    return bool(getattr(settings, "REDIS_URL", ""))


def status() -> dict:
    """Status des Redis-Bus für die Admin-Anzeige (sync-sicher, ohne Live-Ping).

    Alle Felder gelten nur fuer diesen Worker. Die gemeinsame Anzeige aggregiert
    die ablaufenden DB-Heartbeats saemtlicher Worker."""
    running = _pubsub_task is not None and not _pubsub_task.done()
    subscriber = bool(_subscriber is not None and running and _subscribed)
    return {"configured": enabled(), "connected": subscriber and _publisher_ok,
            "subscriber_connected": subscriber, "publisher_ok": _publisher_ok,
            "channels": sorted(_handlers), "last_exception_class": None,
            "subscriber_exception_class": None,
            "last_pong_at": None, "dropped": 0, **_diagnostics,
            "worker_id": worker_id(),
            "connection_duration_s": (round(monotonic() - _connected_since, 3)
                                      if _connected_since is not None
                                      else _diagnostics.get("connection_duration_s", 0))}


def register(channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
    """Registriert den lokalen Zusteller für einen Kanal (beim Import der Module).

    Muss vor ``start()`` erfolgen, damit ``start()`` alle Kanäle abonniert.
    """
    _handlers[channel] = handler


def on_reconnect(callback: Callable[[], Awaitable[None]]) -> None:
    """Nach jeder bestaetigten Subscription aufrufen, auch beim ersten Connect."""
    if callback not in _reconnect_callbacks:
        _reconnect_callbacks.append(callback)


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
    except Exception as exc:
        _publisher_ok = False
        _diagnostics["publish_failures"] += 1
        _failure("publish_failed", exc, channel=channel)
        return False


async def _close(resource) -> None:
    if resource is not None:
        try:
            await asyncio.wait_for(resource.aclose(), timeout=2)
        except Exception as exc:
            _failure("close_failed", exc)


async def _connect():
    global _subscriber, _subscribed, _connected_since, _queue, _consumer_task
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=0,
        socket_connect_timeout=2,
        socket_timeout=None,
        # RESP2 liefert PONG als Array; redis-py 8.1 zerlegt dekodierte
        # RESP3-Strings sonst als einzelne Buchstaben.
        protocol=2,
    )
    pubsub = None
    try:
        _queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        _consumer_task = asyncio.create_task(_consume(_queue), name="redis-handler")
        pubsub = client.pubsub()
        channels = list(_handlers) or [CH_WS, CH_GW]
        await pubsub.subscribe(*channels)
        # subscribe() sendet nur den Befehl. Erst alle Redis-ACKs bestaetigen,
        # bevor Monitoring oder ein Test "connected" sehen darf.
        pending = set(channels)
        async with asyncio.timeout(5):
            while pending:
                message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=READ_TIMEOUT_S)
                if message and message.get("type") == "subscribe":
                    pending.discard(message.get("channel"))
                elif message and message.get("type") == "message":
                    await _dispatch(message)
    except BaseException:
        await _stop_consumer()
        await _close(pubsub)
        await _close(client)
        raise
    _subscriber = client
    _subscribed = True
    _connected_since = monotonic()
    _diagnostics["last_connected_at"] = _now()
    _diagnostics["reconnects"] += 1
    for callback in list(_reconnect_callbacks):
        try:
            await callback()
        except Exception as exc:
            _failure("reconnect_callback_failed", exc)
    return pubsub


async def _connect_publisher() -> None:
    global _redis, _publisher_ok
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True,
        health_check_interval=0, socket_connect_timeout=2, socket_timeout=5,
    )
    try:
        await client.ping()
    except BaseException:
        await _close(client)
        raise
    _redis = client
    _publisher_ok = True


async def _supervise_publisher() -> None:
    global _redis, _publisher_ok
    korrelation_setzen()
    delay = BACKOFF_MIN_S
    stabil_seit = monotonic() if _redis is not None else None
    while True:
        try:
            if _redis is None:
                await _connect_publisher()
                stabil_seit = monotonic()
            await asyncio.sleep(HEALTH_INTERVAL_S)
            client = _redis
            if client is not None:
                await client.ping()
            _publisher_ok = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _publisher_ok = False
            if stabil_seit is not None and monotonic() - stabil_seit >= STABLE_CONNECTION_S:
                delay = BACKOFF_MIN_S
            stabil_seit = None
            _failure("publisher_disconnected", exc)
            client, _redis = _redis, None
            await _close(client)
            await asyncio.sleep(random.uniform(delay, min(delay * 1.2, BACKOFF_MAX_S)))
            delay = min(delay * 2, BACKOFF_MAX_S)


async def start() -> None:
    global _pubsub_task, _publisher_task
    if not enabled() or (_pubsub_task is not None and not _pubsub_task.done()):
        return
    try:
        await _connect_publisher()
    except Exception as exc:
        _failure("publisher_connect_failed", exc)
    _publisher_task = asyncio.create_task(_supervise_publisher(), name="redis-publisher")
    pubsub = None
    try:
        pubsub = await _connect()
    except Exception as exc:
        _failure("connect_failed", exc)
    _pubsub_task = asyncio.create_task(_supervise(pubsub), name="redis-subscriber")


async def _stop_consumer() -> None:
    global _consumer_task, _queue
    if _consumer_task is not None:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
        _consumer_task = None
    _queue = None


async def _supervise(pubsub) -> None:
    global _subscriber, _subscribed, _connected_since
    korrelation_setzen()
    delay = BACKOFF_MIN_S
    while True:
        try:
            if pubsub is None:
                pubsub = await _connect()
            await _reader(pubsub)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _diagnostics["subscriber_failures"] += 1
            _diagnostics["connection_duration_s"] = (
                round(monotonic() - _connected_since, 3) if _connected_since is not None else 0
            )
            _diagnostics["subscriber_exception_class"] = type(exc).__name__
            _failure("subscriber_disconnected", exc)
        finally:
            if _connected_since is not None and monotonic() - _connected_since >= STABLE_CONNECTION_S:
                delay = BACKOFF_MIN_S
            _connected_since = None
            _subscribed = False
            _diagnostics["last_disconnected_at"] = _now()
            await _stop_consumer()
            client, _subscriber = _subscriber, None
            await _close(pubsub)
            pubsub = None
            await _close(client)
        await asyncio.sleep(random.uniform(delay, min(delay * 1.2, BACKOFF_MAX_S)))
        delay = min(delay * 2, BACKOFF_MAX_S)


async def _reader(pubsub) -> None:
    """Auch ein stilles Netzloch erkennen: PING/PONG auf der Subscriber-Verbindung."""
    last_ping = monotonic()
    waiting_since: float | None = None
    while True:
        msg = await pubsub.get_message(ignore_subscribe_messages=False, timeout=READ_TIMEOUT_S)
        now = monotonic()
        if waiting_since is not None and now - waiting_since >= HEALTH_TIMEOUT_S:
            raise TimeoutError("subscriber_heartbeat_timeout")
        if msg is not None:
            _diagnostics["last_subscriber_seen_at"] = _now()
            if msg.get("type") == "pong":
                waiting_since = None
                _diagnostics["last_pong_at"] = _now()
            await _dispatch(msg)
        if waiting_since is None and now - last_ping >= HEALTH_INTERVAL_S:
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
    if _queue is not None:
        if _queue.full():
            _queue.get_nowait()
            _queue.task_done()
            _diagnostics["dropped"] = _diagnostics.get("dropped", 0) + 1
        _queue.put_nowait((channel, handler, payload))


async def _consume(queue: asyncio.Queue) -> None:
    korrelation_setzen()
    while True:
        channel, handler, payload = await queue.get()
        try:
            await handler(payload)
        except Exception as exc:
            _failure("handler_failed", exc, channel=channel)
        finally:
            queue.task_done()


async def stop() -> None:
    """Beendet Subscriber-Task und Redis-Verbindung (im lifespan-Shutdown)."""
    global _pubsub_task, _publisher_task, _redis, _subscribed, _publisher_ok, _subscriber, _connected_since
    _subscribed = False
    _publisher_ok = False
    if _pubsub_task is not None:
        _pubsub_task.cancel()
        try:
            await _pubsub_task
        except asyncio.CancelledError, Exception:
            pass
        _pubsub_task = None
    if _publisher_task is not None:
        _publisher_task.cancel()
        try:
            await _publisher_task
        except asyncio.CancelledError:
            pass
        _publisher_task = None
    await _stop_consumer()
    # Auch unmittelbar nach start() kann der Supervisor noch ungestartet sein.
    if _subscriber is not None:
        await _close(_subscriber)
        _subscriber = None
    _connected_since = None
    if _redis is not None:
        await _close(_redis)
        _redis = None
    _publisher_ok = False
