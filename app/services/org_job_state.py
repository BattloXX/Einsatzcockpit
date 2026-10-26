"""Hintergrundauftrag je Organisation, mit Redis-Lease und lokalem Fallback."""
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from app.config import settings
from app.services import shared_cache

logger = logging.getLogger("einsatzleiter.org_job_state")
TTL_S = 120
HEARTBEAT_S = 30
_last_warning = -float("inf")
_HEARTBEAT = """
local value = redis.call('get', KEYS[1])
if value and cjson.decode(value).token == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_FINISH = """
local value = redis.call('get', KEYS[1])
if value and cjson.decode(value).token == ARGV[1] then
    if ARGV[2] ~= '' then redis.call('set', KEYS[2], ARGV[2], 'EX', 3600) end
    redis.call('del', KEYS[1], KEYS[3])
    return 1
end
return 0
"""
_CANCEL = """
local value = redis.call('get', KEYS[1])
if value then
    redis.call('set', KEYS[2], cjson.decode(value).token, 'EX', ARGV[1])
    return 1
end
return 0
"""


def _failure() -> None:
    global _last_warning
    now = time.monotonic()
    if now - _last_warning >= 60:
        _last_warning = now
        logger.warning("Hintergrundauftrag-Redis nicht verfügbar; lokaler Betrieb")


class Backend:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._results: dict[str, tuple[dict, float]] = {}
        self._cancels: dict[str, str] = {}

    @staticmethod
    def _key(kind: str, org_id: int) -> str:
        return f"ecp:job:{kind}:{org_id}"

    def _local(self, key: str) -> dict | None:
        value = self._jobs.get(key)
        if value and value["expires_at"] <= time.monotonic():
            self._jobs.pop(key, None)
            return None
        return value

    @staticmethod
    def _deadline(ttl_s: int) -> float:
        return time.monotonic() + ttl_s if settings.REDIS_URL else float("inf")

    async def try_start(self, kind: str, org_id: int, ttl_s: int, worker_id: str,
                        *, run_id: str | None = None) -> str | None:
        key = self._key(kind, org_id)
        token = uuid4().hex
        value = {"token": token, "worker": worker_id, "started_at": time.time(), "run_id": run_id}
        acquired = self._local(key) is None
        try:
            connection = shared_cache.client()
            if connection is not None:
                acquired = bool(await connection.set(key, json.dumps(value), nx=True, ex=ttl_s))
                if acquired:
                    await connection.delete(key + ":result", key + ":cancel")
        except Exception:
            _failure()
        if not acquired:
            return None
        self._jobs[key] = {**value, "expires_at": self._deadline(ttl_s)}
        self._results.pop(key, None)
        self._cancels.pop(key, None)
        return token

    async def heartbeat(self, kind: str, org_id: int, token: str, ttl_s: int) -> bool:
        key = self._key(kind, org_id)
        value = self._local(key)
        owned = bool(value and value["token"] == token)
        try:
            connection = shared_cache.client()
            if connection is not None:
                owned = bool(await connection.eval(_HEARTBEAT, 1, key, token, ttl_s))
        except Exception:
            _failure()
        if owned and value and value["token"] == token:
            value["expires_at"] = self._deadline(ttl_s)
        return owned

    async def finish(self, kind: str, org_id: int, token: str, result: dict | None = None) -> None:
        key = self._key(kind, org_id)
        value = self._local(key)
        owned = bool(value and value["token"] == token)
        try:
            connection = shared_cache.client()
            if connection is not None:
                owned = bool(await connection.eval(_FINISH, 3, key, key + ":result", key + ":cancel",
                                                   token, json.dumps(result) if result is not None else ""))
        except Exception:
            _failure()
        if owned:
            if result is not None:
                self._results[key] = (result, self._deadline(3600))
            self._cancels.pop(key, None)
        if value and value["token"] == token:
            self._jobs.pop(key, None)

    async def status(self, kind: str, org_id: int) -> dict[str, Any]:
        key = self._key(kind, org_id)
        value = self._local(key)
        entry = self._results.get(key)
        result = entry[0] if entry and entry[1] > time.monotonic() else None
        try:
            connection = shared_cache.client()
            if connection is not None:
                raw, raw_result = await connection.mget(key, key + ":result")
                value = json.loads(raw) if raw else None
                result = json.loads(raw_result) if raw_result else None
        except Exception:
            _failure()
        return {"running": value is not None, "worker": value["worker"] if value else None,
                "run_id": value.get("run_id") if value else None, "result": result}

    async def pop_result(self, kind: str, org_id: int) -> dict | None:
        key = self._key(kind, org_id)
        entry = self._results.pop(key, None)
        try:
            connection = shared_cache.client()
            if connection is not None:
                raw = await connection.getdel(key + ":result")
                return json.loads(raw) if raw else None
        except Exception:
            _failure()
        return entry[0] if entry and entry[1] > time.monotonic() else None

    async def request_cancel(self, kind: str, org_id: int, ttl_s: int = TTL_S) -> bool:
        key = self._key(kind, org_id)
        try:
            connection = shared_cache.client()
            if connection is not None:
                return bool(await connection.eval(_CANCEL, 2, key, key + ":cancel", ttl_s))
        except Exception:
            _failure()
        value = self._local(key)
        if value:
            self._cancels[key] = value["token"]
        return value is not None

    async def cancel_requested(self, kind: str, org_id: int, token: str) -> bool:
        key = self._key(kind, org_id)
        try:
            connection = shared_cache.client()
            if connection is not None:
                return await connection.get(key + ":cancel") == token
        except Exception:
            _failure()
        return self._cancels.get(key) == token

    async def run(self, kind: str, org_id: int, token: str,
                  action: Callable[[], Awaitable[Any]]) -> Any:
        """Lease auch während langsamer Netzwerkaufrufe verlängern."""
        async def pulse() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_S)
                await self.heartbeat(kind, org_id, token, TTL_S)

        heartbeat = asyncio.create_task(pulse())
        try:
            return await action()
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.finish(kind, org_id, token)


backend = Backend()
try_start = backend.try_start
heartbeat = backend.heartbeat
finish = backend.finish
status = backend.status
pop_result = backend.pop_result
request_cancel = backend.request_cancel
cancel_requested = backend.cancel_requested
