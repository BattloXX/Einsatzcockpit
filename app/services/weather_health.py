"""Kleine Betriebsdiagnose ohne Datenbank; Redis-Zähler verfallen nach 24 Stunden."""
import logging
import time
from datetime import UTC, datetime
from typing import Any

from app.services import shared_cache

_PROVIDERS = {"geosphere", "open-meteo", "open-meteo-geosphere", "kachelmann"}
_health: dict[str, dict[str, Any]] = {}
_logged: dict[tuple[str, str], float] = {}
logger = logging.getLogger("einsatzleiter.weather")


def log_failure(provider: str, kind: str, reason: str, fallback_to: str | None = None) -> None:
    if provider not in _PROVIDERS or kind not in {"current", "forecast", "nowcast", "daily", "warnings"}:
        return
    key = (provider, kind)
    now = time.monotonic()
    if now - _logged.get(key, -float("inf")) >= 60:
        _logged[key] = now
        logger.warning("Wetterprovider ausgefallen/ersetzt", extra={
            "provider": provider, "kind": kind, "reason": reason, "fallback_to": fallback_to})


async def record(provider: str, success: bool, latency_ms: float, reason: str = "unavailable") -> None:
    if provider not in _PROVIDERS:
        return
    row = _health.setdefault(provider, {"last_success_at": None, "last_error_at": None,
                                      "last_error": None, "last_latency_ms": None,
                                      "success_count": 0, "error_count": 0})
    now = datetime.now(UTC).isoformat()
    stamp = "last_success_at" if success else "last_error_at"
    counter = "success_count" if success else "error_count"
    # Nur interne, kurze Fehlercodes; niemals Exception-Nachrichten übernehmen.
    reason = reason if reason in {"unavailable", "timeout", "connection", "invalid_response"} else "provider_error"
    row[stamp] = now
    row[counter] += 1
    row["last_latency_ms"] = round(latency_ms, 2)
    if not success:
        row["last_error"] = reason
    try:
        connection = shared_cache.client()
        if connection is not None:
            key = f"wx:health:v1:{provider}"
            async with connection.pipeline(transaction=True) as pipe:
                pipe.hincrby(key, counter, 1)
                pipe.hset(key, mapping={stamp: now, "last_latency_ms": str(row["last_latency_ms"]),
                                       **({"last_error": reason} if not success else {})})
                pipe.expire(key, 86400)
                await pipe.execute()
    except Exception:
        shared_cache.redis_error()


async def provider_health() -> dict[str, dict[str, Any]]:
    result = {provider: row.copy() for provider, row in _health.items()}
    try:
        connection = shared_cache.client()
        if connection is not None:
            for provider in sorted(_PROVIDERS):
                row = await connection.hgetall(f"wx:health:v1:{provider}")
                if row:
                    result[provider] = {
                        "last_success_at": row.get("last_success_at"), "last_error_at": row.get("last_error_at"),
                        "last_error": row.get("last_error"),
                        "last_latency_ms": float(row["last_latency_ms"]),
                        "success_count": int(row.get("success_count", 0)),
                        "error_count": int(row.get("error_count", 0)),
                    }
    except Exception:
        shared_cache.redis_error()
    return result
