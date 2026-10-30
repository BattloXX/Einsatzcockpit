"""Workerübergreifende UI-Caches und Herkunft des lokalen Logpuffers."""
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from starlette.requests import Request

from app.config import settings
from app.routers import ws
from app.services import abfluss_service as abfluss
from app.services import shared_cache
from tests.test_worker_monitor import runtime_orgs, worker  # noqa: F401
from app.services.sms_outbox import database


@pytest.fixture
def redis(monkeypatch):
    connection = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(shared_cache, "client", lambda: connection)
    monkeypatch.setattr(ws, "_passthrough_status", {})
    monkeypatch.setattr(abfluss, "_store", {})
    return connection


async def test_passthrough_second_worker_and_ttl(redis):
    payload = {"enabled": True, "listening": True, "clients": 2}
    await ws.set_passthrough_status(17, payload)
    assert 0 < await redis.ttl("ecp:passthrough:17") <= 120
    ws._passthrough_status.clear()
    assert await ws.get_passthrough_status(17) == payload
    assert await ws.get_passthrough_status(18) is None
    await redis.delete("ecp:passthrough:17")
    assert await ws.get_passthrough_status(17) is None


async def test_passthrough_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(ws, "_passthrough_status", {})
    payload = {"enabled": False, "listening": False, "clients": 0}
    await ws.set_passthrough_status(17, payload)
    assert await ws.get_passthrough_status(17) == payload
    assert await ws.get_passthrough_status(18) is None
    assert await ws.get_passthrough_status(None) is None


async def test_passthrough_failure_is_local_and_warning_throttled(monkeypatch, caplog):
    monkeypatch.setattr(ws, "_passthrough_status", {})
    monkeypatch.setattr(ws, "_passthrough_last_warning", -float("inf"))
    monkeypatch.setattr(shared_cache, "client", lambda: (_ for _ in ()).throw(OSError("SECRET")))
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.ws"):
        await ws.set_passthrough_status(17, {"enabled": True})
        assert (await ws.get_passthrough_status(17))["enabled"]
        assert await ws.get_passthrough_status(18) is None
    assert len(caplog.records) == 1
    assert "SECRET" not in caplog.text


async def test_hq_second_worker(redis, monkeypatch):
    hq = abfluss.HQWerte(hq1=10, hq10=25, hq100=80)
    fetch = AsyncMock(return_value=(None, None, hq))
    monkeypatch.setattr(abfluss, "_fetch_from_vowis", fetch)
    await abfluss.refresh_station(17, "200048", "Test")
    abfluss._store.clear()
    assert await abfluss.get_hq_werte(17, "200048") == hq
    assert await abfluss.get_hq_werte(18, "200048") is None
    # Ein noch nicht befüllter lokaler State darf den gemeinsamen Cache nicht verdecken.
    abfluss._state(17, "200048", "Test")
    assert await abfluss.get_hq_werte(17, "200048") == hq
    assert fetch.await_count == 1


async def test_hq_without_redis_and_failure(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(abfluss, "_store", {})
    hq = abfluss.HQWerte(hq1=10)
    monkeypatch.setattr(abfluss, "_fetch_from_vowis", AsyncMock(return_value=(None, None, hq)))
    await abfluss.refresh_station(17, "200048", "Test")
    assert await abfluss.get_hq_werte(17, "200048") == hq
    monkeypatch.setattr(shared_cache, "client", lambda: (_ for _ in ()).throw(OSError("SECRET")))
    assert await abfluss.get_hq_werte(17, "200048") == hq
    assert await abfluss.get_hq_werte(18, "200048") is None


def test_weather_history_worker_limitation_documented():
    service = Path("app/services/weather_station_service.py").read_text()
    rollout = Path("docs/multi-worker/multi-worker-rollout.md").read_text()
    assert "Fallback gilt pro Worker" in service
    assert "Wetter-DB benötigt" in service
    assert "Wetterstationsverläufe gilt pro Worker" in rollout


@pytest.mark.parametrize("worker_count", [1, 2])
def test_log_view_worker_note(runtime_orgs, monkeypatch, worker_count):
    from app.routers.ui_admin import server_log_json, server_log_page
    from app.services import gateway_presence

    monkeypatch.setattr(gateway_presence, "worker_id", lambda: "worker-a")
    request = Request({"type": "http", "headers": [], "method": "GET", "scheme": "http",
                       "server": ("localhost", 80), "path": "/admin/system/server-log", "query_string": b""})
    from app.models.user import User

    request.state.user = User(id=1, org_id=None, active=True)
    with database() as db:
        worker(db, "worker-a")
        if worker_count == 2:
            worker(db, "worker-b")
        worker(db, "expired", age=60)
        db.commit()
        response = server_log_page(request, db=db)
        html = bytes(response.body).decode()
        data = server_log_json(request, db=db)
        expected = (f"Protokoll von Worker worker-a (Prozess {os.getpid()}) – "
                    "andere Worker führen eigene Puffer")
        assert (expected in html) == (worker_count == 2)
        assert data["worker_note"] == (expected if worker_count == 2 else None)
