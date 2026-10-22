"""Optionaler Wetter-JSON-Cache mit eigener Redis-Verbindung und Worker-Sperren."""
import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.config import settings

logger = logging.getLogger("einsatzleiter.weather")
_clients: WeakKeyDictionary = WeakKeyDictionary()
_last_error = -float("inf")
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class LockTimeout(TimeoutError):
    """Gesunde Redis-Verbindung, aber Sperre weiterhin belegt."""


def redis_error() -> None:
    """Keine Exception-Texte: auch Verbindungs-URLs können Zugangsdaten enthalten."""
    global _last_error
    now = time.monotonic()
    if now - _last_error >= 60:
        _last_error = now
        logger.warning("Wetter-Redis nicht verfügbar; lokaler Betrieb")


def client() -> Any:
    if not settings.REDIS_URL:
        return None
    loop = asyncio.get_running_loop()
    entry = _clients.get(loop)
    if entry is None or entry[0] != settings.REDIS_URL:
        connection = Redis.from_url(settings.REDIS_URL, decode_responses=True,
                                    socket_connect_timeout=0.25, socket_timeout=0.25,
                                    retry=Retry(NoBackoff(), 0))
        _clients[loop] = (settings.REDIS_URL, connection)
    return _clients[loop][1]


def key_for(key: str, namespace: str = "cache") -> str:
    # Weder API-URLs samt Query noch Zugangsdaten als Redis-Key ablegen.
    return f"wx:{namespace}:v1:{hashlib.sha256(key.encode()).hexdigest()}"


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Cache-Zeitpunkt braucht Zeitzone")
        return {"@datetime": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        return {"@class": type(value).__name__, "fields": {
            f.name: _encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple):
        return {"@tuple": [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Nicht unterstützter Cache-Typ")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "@datetime" in value:
        dt = datetime.fromisoformat(value["@datetime"])
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("Cache-Zeitpunkt braucht Zeitzone")
        return dt
    if "@tuple" in value:
        return tuple(_decode(v) for v in value["@tuple"])
    if "@class" in value:
        from app.services import weather_service
        # Ausschließlich bekannte Datenklassen, keine dynamischen Imports/Objekt-Hooks.
        allowed = {name: getattr(weather_service, name) for name in (
            "DataMeta", "CurrentWeather", "ForecastResult", "ForecastHorizon", "NowcastResult",
            "NowcastStep", "DailyForecast", "DailyForecastDay", "WarningsResult", "WeatherWarning")}
        cls = allowed[value["@class"]]
        return cls(**{k: _decode(v) for k, v in value["fields"].items()})
    return {k: _decode(v) for k, v in value.items()}


def dumps(value: Any) -> str:
    return json.dumps(_encode(value), allow_nan=False, separators=(",", ":"))


def loads(value: str) -> Any:
    return _decode(json.loads(value))


async def get_entry(key: str) -> tuple[Any, float] | None:
    """Wert und Restlaufzeit atomar lesen, damit lokale Treffer nicht länger leben."""
    try:
        connection = client()
        if connection is not None:
            redis_key = key_for(key)
            async with connection.pipeline(transaction=True) as pipe:
                pipe.get(redis_key)
                pipe.pttl(redis_key)
                raw, remaining_ms = await pipe.execute()
            if raw is not None and remaining_ms > 0:
                return loads(raw), remaining_ms / 1000
    except Exception:
        redis_error()
    return None


async def get(key: str) -> Any:
    entry = await get_entry(key)
    return entry[0] if entry is not None else None


async def set(key: str, value: Any, ttl: int) -> None:
    try:
        connection = client()
        if connection is not None and ttl > 0:
            await connection.setex(key_for(key), ttl, dumps(value))
    except Exception:
        redis_error()


@asynccontextmanager
async def lock(key: str) -> AsyncIterator[None]:
    connection = None
    token = uuid4().hex
    acquired = False
    redis_key = key_for(key, "lock")
    try:
        connection = client()
        if connection is not None:
            ttl = settings.WEATHER_LOCK_TTL_S
            deadline = time.monotonic() + ttl
            while True:
                acquired = bool(await connection.set(redis_key, token, nx=True, px=max(1, int(ttl * 1000))))
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    # Ein gesunder, aber belegter Lock darf keinen parallelen Fetch auslösen.
                    raise LockTimeout("Wetter-Worker-Sperre belegt")
                await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    except LockTimeout:
        raise
    except Exception:
        redis_error()
    try:
        yield
    finally:
        if acquired and connection is not None:
            try:
                await connection.eval(_RELEASE, 1, redis_key, token)
            except Exception:
                redis_error()
