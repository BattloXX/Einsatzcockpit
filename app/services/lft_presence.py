"""Gemeinsame Lageführungs-Presence und weiche Bearbeitungssperren."""
import json
import logging
import time
from typing import Any

from app.services import shared_cache

logger = logging.getLogger("einsatzleiter.lft_presence")
# lagefuehrung.js sendet alle 10 s; 45 s tolerieren mehrere verlorene Heartbeats
# und liegen über dem geforderten doppelten Intervall.
PRESENCE_TTL_S = 45
LOCK_TTL_MS = 15000
_last_warning = -float("inf")
_ACQUIRE = """
if redis.call('set', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) then return 1 end
local value = redis.call('get', KEYS[1])
if value and cjson.decode(value).user_id == cjson.decode(ARGV[1]).user_id then
    redis.call('set', KEYS[1], ARGV[1], 'PX', ARGV[2])
    return 1
end
return 0
"""
_RELEASE = """
local value = redis.call('get', KEYS[1])
if value and tostring(cjson.decode(value).user_id) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_PRUNE = """
if redis.call('hget', KEYS[1], ARGV[1]) == ARGV[2] then
    return redis.call('hdel', KEYS[1], ARGV[1])
end
return 0
"""


def _failure() -> None:
    global _last_warning
    now = time.monotonic()
    if now - _last_warning >= 60:
        _last_warning = now
        # Exception-Texte können Zugangsdaten aus der Redis-URL enthalten.
        logger.warning("Lageführung-Redis nicht verfügbar; lokaler Betrieb")


class Backend:
    def __init__(self) -> None:
        self._presence: dict[int, dict[str, dict]] = {}
        self._locks: dict[int, dict[int, dict]] = {}
        self._owned: dict[tuple[int, str], dict[int, int]] = {}

    async def touch(self, incident_id: int, connection_id: str, user_id: int, name: str) -> None:
        value = {"user_id": user_id, "name": name, "expires_at": time.time() + PRESENCE_TTL_S}
        self._presence.setdefault(incident_id, {})[connection_id] = value
        try:
            connection = shared_cache.client()
            if connection is not None:
                await connection.hset(f"lft:{incident_id}:presence", connection_id, json.dumps(value))
        except Exception:
            _failure()

    async def users(self, incident_id: int) -> list[dict]:
        now = time.time()
        local = self._presence.get(incident_id, {})
        for cid in list(local):
            if local[cid]["expires_at"] <= now:
                del local[cid]
        values = list(local.values())
        try:
            connection = shared_cache.client()
            if connection is not None:
                key = f"lft:{incident_id}:presence"
                entries = await connection.hgetall(key)
                values = []
                for cid, raw in entries.items():
                    value = json.loads(raw)
                    if value["expires_at"] > now:
                        values.append(value)
                    else:
                        # Ein paralleler Heartbeat darf nicht mitgelöscht werden.
                        await connection.eval(_PRUNE, 1, key, cid, raw)
        except Exception:
            _failure()
            values = list(local.values())
        users = {value["user_id"]: {"user_id": value["user_id"], "name": value["name"]}
                 for value in values}
        return [users[uid] for uid in sorted(users)]

    async def acquire(self, incident_id: int, connection_id: str, feature_id: int,
                      user_id: int, name: str) -> bool:
        value = {"user_id": user_id, "name": name}
        locks = self._locks.setdefault(incident_id, {})
        existing = locks.get(feature_id)
        acquired = not existing or existing["expires_at"] <= time.monotonic() or existing["user_id"] == user_id
        try:
            connection = shared_cache.client()
            if connection is not None:
                acquired = bool(await connection.eval(_ACQUIRE, 1, f"lft:{incident_id}:lock:{feature_id}",
                                                      json.dumps(value), LOCK_TTL_MS))
        except Exception:
            _failure()
        if acquired:
            locks[feature_id] = {**value, "expires_at": time.monotonic() + LOCK_TTL_MS / 1000}
            self._owned.setdefault((incident_id, connection_id), {})[feature_id] = user_id
        return bool(acquired)

    async def release(self, incident_id: int, feature_id: int, user_id: int) -> bool:
        locks = self._locks.get(incident_id, {})
        existing = locks.get(feature_id)
        released = bool(existing and existing["user_id"] == user_id and existing["expires_at"] > time.monotonic())
        try:
            connection = shared_cache.client()
            if connection is not None:
                released = bool(await connection.eval(_RELEASE, 1, f"lft:{incident_id}:lock:{feature_id}",
                                                       str(user_id)))
        except Exception:
            _failure()
        if existing and existing["user_id"] == user_id:
            del locks[feature_id]
        return released

    async def disconnect(self, incident_id: int, connection_id: str) -> list[int]:
        presence = self._presence.get(incident_id, {})
        presence.pop(connection_id, None)
        if not presence:
            self._presence.pop(incident_id, None)
        try:
            connection: Any = shared_cache.client()
            if connection is not None:
                await connection.hdel(f"lft:{incident_id}:presence", connection_id)
        except Exception:
            _failure()
        released = []
        for fid, uid in self._owned.pop((incident_id, connection_id), {}).items():
            if await self.release(incident_id, fid, uid):
                released.append(fid)
        if not self._locks.get(incident_id):
            self._locks.pop(incident_id, None)
        return released


backend = Backend()
