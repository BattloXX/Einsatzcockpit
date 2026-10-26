"""Getrennte Worker-Zustände mit gemeinsamem Redis für Presence und Soft-Locks."""
import asyncio
import json
import logging
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.config import settings
from app.services import lft_presence as lft
from app.services import shared_cache


@pytest.fixture
def redis(monkeypatch):
    connection = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(shared_cache, "client", lambda: connection)
    return connection


async def test_presence_between_workers_and_expiry(redis):
    a, b = lft.Backend(), lft.Backend()
    await a.touch(1, "a", 1, "Alice")
    await b.touch(1, "b", 2, "Bob")
    await b.touch(1, "second-tab", 1, "Alice")
    expected = [{"user_id": 1, "name": "Alice"}, {"user_id": 2, "name": "Bob"}]
    assert await a.users(1) == await b.users(1) == expected
    value = json.loads(await redis.hget("lft:1:presence", "a"))
    assert 44 < value["expires_at"] - lft.time.time() <= 45
    value["expires_at"] = lft.time.time() - 1
    await redis.hset("lft:1:presence", "a", json.dumps(value))
    assert await b.users(1) == expected  # zweiter Tab hält Alice sichtbar
    assert await redis.hget("lft:1:presence", "a") is None
    await b.disconnect(1, "second-tab")
    assert await a.users(1) == [{"user_id": 2, "name": "Bob"}]
    await b.touch(1, "b", 2, "Bob")
    assert json.loads(await redis.hget("lft:1:presence", "b"))["expires_at"] > lft.time.time()


async def test_lock_blocks_renews_and_expires(redis):
    a, b = lft.Backend(), lft.Backend()
    assert await a.acquire(1, "a", 42, 1, "Alice")
    assert json.loads(await redis.get("lft:1:lock:42")) == {"user_id": 1, "name": "Alice"}
    assert not await b.acquire(1, "b", 42, 2, "Bob")
    await redis.pexpire("lft:1:lock:42", 1000)
    assert await a.acquire(1, "a", 42, 1, "Alice")
    assert 14000 < await redis.pttl("lft:1:lock:42") <= 15000
    await redis.pexpire("lft:1:lock:42", 1)
    await asyncio.sleep(0.03)
    assert await b.acquire(1, "b", 42, 2, "Bob")
    # Später Cleanup des alten Besitzers darf B nicht entsperren.
    assert await a.disconnect(1, "a") == []
    assert not await a.acquire(1, "a", 42, 1, "Alice")


async def test_release_checks_owner(redis):
    a, b = lft.Backend(), lft.Backend()
    assert await a.acquire(1, "a", 42, 1, "Alice")
    assert not await b.release(1, 42, 2)
    assert not await b.acquire(1, "b", 42, 2, "Bob")
    assert await a.release(1, 42, 1)
    assert await b.acquire(1, "b", 42, 2, "Bob")


async def test_disconnect_removes_only_own_presence_and_locks(redis):
    a, b = lft.Backend(), lft.Backend()
    await a.touch(1, "a", 1, "Alice")
    await b.touch(1, "b", 2, "Bob")
    assert await a.acquire(1, "a", 42, 1, "Alice")
    assert await b.acquire(1, "b", 43, 2, "Bob")
    assert await a.disconnect(1, "a") == [42]
    assert await b.users(1) == [{"user_id": 2, "name": "Bob"}]
    assert await b.acquire(1, "b", 42, 2, "Bob")
    assert not await a.acquire(1, "a", 43, 1, "Alice")


async def test_without_redis_local_behavior(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    a, b = lft.Backend(), lft.Backend()
    await a.touch(1, "a", 1, "Alice")
    assert await a.users(1) == [{"user_id": 1, "name": "Alice"}]
    assert await b.users(1) == []
    assert await a.acquire(1, "a", 42, 1, "Alice")
    assert not await a.acquire(1, "b", 42, 2, "Bob")
    assert await a.acquire(1, "a", 42, 1, "Alice")
    assert not await a.release(1, 42, 2)
    assert await a.disconnect(1, "a") == [42]
    assert await a.users(1) == []
    assert await a.acquire(1, "b", 42, 2, "Bob")


async def test_redis_failure_falls_back_without_secrets(monkeypatch, caplog):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://secret@invalid")
    broken = AsyncMock()
    for method in ("hset", "hgetall", "hdel", "eval"):
        getattr(broken, method).side_effect = OSError("redis://secret@invalid")
    monkeypatch.setattr(shared_cache, "client", lambda: broken)
    monkeypatch.setattr(lft, "_last_warning", -float("inf"))
    with caplog.at_level(logging.WARNING, logger=lft.logger.name):
        a = lft.Backend()
        await a.touch(1, "a", 1, "Alice")
        assert await a.users(1) == [{"user_id": 1, "name": "Alice"}]
        assert await a.acquire(1, "a", 42, 1, "Alice")
        assert not await a.acquire(1, "b", 42, 2, "Bob")
        assert not await a.release(1, 42, 2)
        assert await a.disconnect(1, "a") == [42]
        assert await a.users(1) == []
    assert len(caplog.records) == 1
    assert "secret" not in caplog.text
    assert "OSError" not in caplog.text
