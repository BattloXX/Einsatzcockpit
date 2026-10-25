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


def enabled() -> bool:
    """True, wenn ein Redis-Bus konfiguriert ist (REDIS_URL gesetzt)."""
    return bool(getattr(settings, "REDIS_URL", ""))


def status() -> dict:
    """Status des Redis-Bus für die Admin-Anzeige (sync-sicher, ohne Live-Ping).

    configured = REDIS_URL gesetzt; connected = Redis-Client aktiv UND Subscriber-Loop
    läuft (start() war erfolgreich). Spiegelt den Zustand DIESES Workers – da alle
    Worker denselben Redis nutzen, ist er repräsentativ."""
    running = _pubsub_task is not None and not _pubsub_task.done()
    return {"configured": enabled(), "connected": bool(_redis is not None and running and _subscribed)}


def register(channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
    """Registriert den lokalen Zusteller für einen Kanal (beim Import der Module).

    Muss vor ``start()`` erfolgen, damit ``start()`` alle Kanäle abonniert.
    """
    _handlers[channel] = handler


async def publish(channel: str, message: dict) -> bool:
    """False bei Transportfehler; kein erfolgreicher Publish wird vorgetaeuscht."""
    if _redis is None:
        return False
    try:
        await _redis.publish(channel, json.dumps(message, ensure_ascii=False, default=str))
        return True
    except Exception:
        logger.warning("ws_bus: publish fehlgeschlagen (channel=%s)", channel)
        return False


async def _connect():
    global _redis, _subscribed
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
        pubsub = client.pubsub()
        await pubsub.subscribe(*(list(_handlers) or [CH_WS, CH_GW]))
    except Exception:
        if pubsub is not None:
            await pubsub.aclose()
        await client.aclose()
        raise
    _redis = client
    _subscribed = True
    return pubsub


async def start() -> None:
    global _pubsub_task
    if not enabled() or (_pubsub_task is not None and not _pubsub_task.done()):
        return
    pubsub = None
    try:
        pubsub = await _connect()
    except Exception:
        logger.warning("ws_bus: Redis nicht erreichbar; Wiederverbindung folgt")
    _pubsub_task = asyncio.create_task(_supervise(pubsub))


async def _supervise(pubsub) -> None:
    global _redis, _subscribed
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
            logger.warning("ws_bus: Verbindung unterbrochen; erneutes Subscribe folgt")
        finally:
            _subscribed = False
            if pubsub is not None:
                await pubsub.aclose()
                pubsub = None
            if _redis is not None:
                await _redis.aclose()
                _redis = None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 5)


async def _reader(pubsub) -> None:
    """Empfängt Redis-Nachrichten und ruft den lokalen Zusteller je Kanal auf."""
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            handler = _handlers.get(msg.get("channel"))
            if handler is None:
                continue
            try:
                payload = json.loads(msg.get("data"))
            except json.JSONDecodeError, TypeError:
                continue
            try:
                await handler(payload)
            except Exception:
                logger.exception("ws_bus: Handler-Fehler (channel=%s)", msg.get("channel"))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("ws_bus: Subscriber-Schleife abgebrochen.")


async def stop() -> None:
    """Beendet Subscriber-Task und Redis-Verbindung (im lifespan-Shutdown)."""
    global _pubsub_task, _redis, _subscribed
    _subscribed = False
    if _pubsub_task is not None:
        _pubsub_task.cancel()
        try:
            await _pubsub_task
        except asyncio.CancelledError, Exception:
            pass
        _pubsub_task = None
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
