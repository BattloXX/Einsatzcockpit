"""Redis Pub/Sub-Bus für worker-übergreifende WebSocket-Zustellung.

Problem: Bei mehreren Gunicorn/Uvicorn-Workern (deploy/einsatzleiter.service: -w 2)
hält jeder Worker seine eigenen In-Process-Socket-Registries (ConnectionManager,
_print_gateways). NGINX (ip_hash) pinnt eine WS-Verbindung auf genau einen Worker.
Ein Broadcast oder ein Gateway-Druckauftrag, der auf einem anderen Worker entsteht,
erreicht den Ziel-Socket dann nicht.

Lösung: Jede worker-übergreifend zuzustellende Nachricht wird auf einen Redis-Kanal
publiziert; jeder Worker abonniert die Kanäle und stellt eingehende Nachrichten an
seine EIGENEN lokalen Sockets zu. So erreicht ein Publish von Worker A die Sockets
auf allen Workern – und exakt einmal je Worker (Redis liefert an jeden Abonnenten,
inkl. des Publishers selbst).

Ohne REDIS_URL ist der Bus inaktiv (``enabled()`` False) → die Aufrufer fallen auf
die direkte In-Process-Zustellung zurück (Dev / -w 1 bleiben unverändert, keine neue
Abhängigkeit zur Laufzeit nötig).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from app.config import settings

logger = logging.getLogger("einsatzleiter.ws_bus")

# Kanäle
CH_WS = "ec:ws"   # Browser-Broadcasts (ConnectionManager: Einsatz/Lage/Org)
CH_GW = "ec:gw"   # Print-&-Alarm-Gateway-Ebene (print_job, command, config_sync, job_status)
CH_LAGEDOKUMENT = "ec:lagedokument"              # Yjs-Sync-Updates (Lagedokument-Inhalt)
CH_LAGEDOKUMENT_AWARENESS = "ec:lagedokument-aw"  # Yjs-Awareness (Presence/Cursor)

_redis = None
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
    return {"configured": enabled(), "connected": bool(_redis is not None and running)}


def register(channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
    """Registriert den lokalen Zusteller für einen Kanal (beim Import der Module).

    Muss vor ``start()`` erfolgen, damit ``start()`` alle Kanäle abonniert.
    """
    _handlers[channel] = handler


async def publish(channel: str, message: dict) -> None:
    """Publiziert eine Nachricht auf einen Kanal (No-Op ohne Redis)."""
    if _redis is None:
        return
    try:
        await _redis.publish(channel, json.dumps(message, ensure_ascii=False, default=str))
    except Exception:
        logger.exception("ws_bus: publish fehlgeschlagen (channel=%s)", channel)


async def start() -> None:
    """Verbindet zu Redis und startet die Subscriber-Schleife (im lifespan)."""
    global _redis, _pubsub_task
    if not enabled():
        logger.info("ws_bus: REDIS_URL nicht gesetzt – In-Process-Modus (nur bei -w 1 korrekt).")
        return
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415
        _redis = aioredis.from_url(
            settings.REDIS_URL, encoding="utf-8", decode_responses=True,
            health_check_interval=30,
        )
        pubsub = _redis.pubsub()
        channels = list(_handlers.keys()) or [CH_WS, CH_GW]
        await pubsub.subscribe(*channels)
        _pubsub_task = asyncio.create_task(_reader(pubsub))
        logger.info("ws_bus: verbunden mit Redis, Kanäle=%s", channels)
    except Exception:
        # Redis darf den Start nicht blockieren – bei Ausfall lieber degradiert
        # (In-Process) laufen als gar nicht.
        logger.exception("ws_bus: Redis-Start fehlgeschlagen – In-Process-Fallback.")
        _redis = None


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
            except (json.JSONDecodeError, TypeError):
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
    global _pubsub_task, _redis
    if _pubsub_task is not None:
        _pubsub_task.cancel()
        try:
            await _pubsub_task
        except (asyncio.CancelledError, Exception):
            pass
        _pubsub_task = None
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
