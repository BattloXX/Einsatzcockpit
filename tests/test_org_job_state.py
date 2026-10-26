"""Org-Aufträge über getrennte Worker-Zustände mit gemeinsamem Redis."""
import asyncio
import json
import logging
from unittest.mock import AsyncMock

import anyio
import fakeredis.aioredis
import pytest

from app.config import settings
from app.services import org_job_state as jobs
from app.services import shared_cache


@pytest.fixture
def redis(monkeypatch):
    connection = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(shared_cache, "client", lambda: connection)
    return connection


async def test_start_status_and_duplicate_between_workers(redis):
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start("dibos", 7, 120, "A", run_id="run-7")
    assert token
    raw = json.loads(await redis.get("ecp:job:dibos:7"))
    assert raw["token"] == token
    assert raw["worker"] == "A"
    assert raw["started_at"] > 0
    assert await b.status("dibos", 7) == {
        "running": True, "worker": "A", "run_id": "run-7", "result": None,
    }
    assert await b.try_start("dibos", 7, 120, "B") is None
    assert await b.try_start("lis", 7, 120, "B")
    assert await b.try_start("dibos", 8, 120, "B")


async def test_result_once_on_other_worker(redis):
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start("osm", 1, 120, "A")
    result = {"meldung": "fertig", "fehler": False}
    await a.finish("osm", 1, token, result)
    assert (await b.status("osm", 1))["running"] is False
    assert (await b.status("osm", 1))["result"] == result
    assert 3590 < await redis.ttl("ecp:job:osm:1:result") <= 3600
    assert await b.pop_result("osm", 1) == result
    assert await a.pop_result("osm", 1) is None
    assert (await a.status("osm", 1))["result"] is None


async def test_remote_cancel_is_bound_to_run(redis):
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start("lis", 1, 120, "A")
    assert await b.request_cancel("lis", 1)
    assert await a.cancel_requested("lis", 1, token)
    assert not await a.cancel_requested("lis", 1, "wrong-token")
    assert 0 < await redis.ttl("ecp:job:lis:1:cancel") <= 120
    await a.finish("lis", 1, token)
    assert not await b.request_cancel("lis", 1)
    next_token = await a.try_start("lis", 1, 120, "A")
    assert not await a.cancel_requested("lis", 1, next_token)


async def test_expiry_heartbeat_and_stale_owner_fencing(redis):
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start("osm", 1, 120, "A")
    await redis.pexpire("ecp:job:osm:1", 1000)
    assert not await b.heartbeat("osm", 1, "wrong", 120)
    assert await redis.pttl("ecp:job:osm:1") <= 1000
    assert await a.heartbeat("osm", 1, token, 120)
    assert await redis.ttl("ecp:job:osm:1") > 118
    await redis.pexpire("ecp:job:osm:1", 1)
    await asyncio.sleep(0.03)
    assert not (await a.status("osm", 1))["running"]
    other = await b.try_start("osm", 1, 120, "B")
    assert other
    assert not await a.heartbeat("osm", 1, token, 120)
    await a.finish("osm", 1, token, {"stale": True})
    assert (await a.status("osm", 1))["worker"] == "B"
    assert await a.pop_result("osm", 1) is None


async def test_without_redis_keeps_local_state(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start("osm", 1, 1, "A")
    assert token
    monkeypatch.setattr(jobs.time, "monotonic", lambda: 10**20)
    assert (await a.status("osm", 1))["running"]
    assert not (await b.status("osm", 1))["running"]
    assert await a.try_start("osm", 1, 1, "A") is None
    assert await a.request_cancel("osm", 1)
    assert await a.cancel_requested("osm", 1, token)
    await a.finish("osm", 1, token, {"meldung": "fertig"})
    assert await a.pop_result("osm", 1) == {"meldung": "fertig"}
    assert await a.pop_result("osm", 1) is None


async def test_redis_failure_falls_back_and_redacts_logs(monkeypatch, caplog):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://secret")
    connection = AsyncMock()
    for method in ("set", "eval", "mget", "getdel", "get"):
        getattr(connection, method).side_effect = RuntimeError("redis://secret")
    monkeypatch.setattr(shared_cache, "client", lambda: connection)
    monkeypatch.setattr(jobs, "_last_warning", -float("inf"))
    a = jobs.Backend()
    with caplog.at_level(logging.WARNING, logger=jobs.logger.name):
        token = await a.try_start("osm", 1, 120, "A")
        assert token
        assert await a.try_start("osm", 1, 120, "A") is None
        assert (await a.status("osm", 1))["running"]
        assert await a.heartbeat("osm", 1, token, 120)
        assert await a.request_cancel("osm", 1)
        assert await a.cancel_requested("osm", 1, token)
        await a.finish("osm", 1, token, {"ok": True})
        assert await a.pop_result("osm", 1) == {"ok": True}
        assert not (await a.status("osm", 1))["running"]
    assert len(caplog.records) == 1
    assert "secret" not in caplog.text


@pytest.mark.parametrize("system,noun,registry", [
    ("dibos", "trace", "_active_traces"), ("lis", "capture", "_active_captures"),
])
async def test_capture_remote_status_and_cancel(redis, monkeypatch, tmp_path, system, noun, registry):
    from importlib import import_module
    mod = import_module(f"app.services.{system}.{system}_capture")
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start(system, 7, 120, "A", run_id="run-7")
    monkeypatch.setattr(jobs, "status", b.status)
    monkeypatch.setattr(jobs, "request_cancel", b.request_cancel)
    monkeypatch.setattr(jobs, "cancel_requested", a.cancel_requested)
    monkeypatch.setattr(mod, registry, {})
    assert await getattr(mod, f"is_{noun}_running")(7)
    assert await mod._current_run_id(7) == "run-7"
    with pytest.raises(ValueError, match="läuft bereits"):
        await getattr(mod, f"start_{noun}_for_org")(7)
    client = AsyncMock()
    recorder = mod.ExchangeRecorder(tmp_path, 7, "run-7")
    polled = asyncio.Event()

    async def capture_once(*args):
        polled.set()
        return [] if system == "dibos" else False

    monkeypatch.setattr(mod, "_capture_once", capture_once)
    args = (client, recorder, "organization", 5, 0.01) if system == "lis" else (client, recorder, 5, 0.01)
    task = asyncio.create_task(a.run(system, 7, token, lambda: mod.capture_traffic(*args, job_token=token)))
    await polled.wait()
    assert await getattr(mod, f"cancel_{noun}")(7)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    client.aclose.assert_awaited_once()
    assert json.loads((tmp_path / "summary.json").read_text())["finished"] is False
    assert not (await b.status(system, 7))["running"]


async def test_task_heartbeat_and_cleanup(redis, monkeypatch):
    a = jobs.Backend()
    token = await a.try_start("osm", 1, 120, "A")
    monkeypatch.setattr(jobs, "HEARTBEAT_S", 0.01)
    await redis.expire("ecp:job:osm:1", 2)
    entered = asyncio.Event()

    async def action():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(a.run("osm", 1, token, action))
    await entered.wait()
    await asyncio.sleep(0.04)
    assert await redis.ttl("ecp:job:osm:1") > 118
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await a.status("osm", 1))["running"]


async def test_new_start_discards_old_result(redis):
    a, b = jobs.Backend(), jobs.Backend()
    token = await a.try_start("osm", 1, 120, "A")
    await a.finish("osm", 1, token, {"old": True})
    assert await b.try_start("osm", 1, 120, "B")
    assert await a.pop_result("osm", 1) is None


async def test_osm_start_and_result_from_other_worker(redis, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.routers import ui_planspiel_admin as mod
    from app.services.planspiel_ort_service import PoolErgebnis

    a, b = jobs.Backend(), jobs.Backend()
    monkeypatch.setattr(jobs, "try_start", a.try_start)
    monkeypatch.setattr(jobs, "finish", a.finish)
    monkeypatch.setattr(jobs, "backend", a)
    monkeypatch.setattr(jobs, "status", b.status)
    monkeypatch.setattr(jobs, "pop_result", b.pop_result)
    monkeypatch.setattr(mod, "_settings_audit", Mock())
    monkeypatch.setattr(mod, "_pool_context", lambda *args: {})
    monkeypatch.setattr(mod, "_pool_result", lambda *args: "empty")
    monkeypatch.setattr(mod.templates, "TemplateResponse", lambda request, template, context: (template, context))
    release = asyncio.Event()
    entered = asyncio.Event()

    async def importieren(*args):
        entered.set()
        await release.wait()
        return PoolErgebnis(neu=2, aktualisiert=3)

    importer = AsyncMock(side_effect=importieren)
    monkeypatch.setattr(mod, "osm_importieren", importer)
    db, user = Mock(), SimpleNamespace(org_id=7)
    response = await mod.pool_osm_post(None, db, user)
    assert response[0] == "planspiel/admin/_osm_laeuft.html"
    await entered.wait()
    monkeypatch.setattr(jobs, "try_start", b.try_start)
    response = await mod.pool_osm_post(None, db, user)
    assert response[0] == "planspiel/admin/_osm_laeuft.html"
    importer.assert_awaited_once()
    db.commit.assert_called_once()
    release.set()
    await asyncio.gather(*mod._osm_tasks)
    response = await anyio.to_thread.run_sync(mod.pool_osm_status, None, db, user)
    assert response == ("planspiel/admin/_pool_ergebnis.html", {
        "meldung": "OSM-Import fertig: 2 neu, 3 aktualisiert", "fehler": False,
    })
    assert await anyio.to_thread.run_sync(mod.pool_osm_status, None, db, user) == "empty"
    if mod._osm_tasks:
        await asyncio.gather(*mod._osm_tasks)
