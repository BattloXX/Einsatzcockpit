"""Retry-Matrix, harte Grenzen und Protokollierung ohne Zugangsdaten."""
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.services import kachelmann_service as ks
from app.services import weather_health as health
from app.services import weather_http as wh
from app.services import weather_service as ws


@pytest.mark.parametrize("failure,retries,calls", [
    ("timeout", 1, 2), ("connection", 1, 2), (500, 1, 2), (503, 2, 3),
    (404, 1, 1), (429, 2, 1), (401, 1, 1), (500, 0, 1), (500, 999, 3),
])
async def test_retry_matrix(monkeypatch, failure, retries, calls):
    monkeypatch.setattr(settings, "WEATHER_HTTP_RETRIES", retries)
    monkeypatch.setattr(settings, "WEATHER_RETRY_BACKOFF_S", 0)
    request = httpx.Request("GET", "https://weather.test")
    if failure == "timeout":
        error = httpx.ReadTimeout("timeout")
    elif failure == "connection":
        error = httpx.ConnectError("connection")
    else:
        error = httpx.HTTPStatusError("error", request=request,
                                      response=httpx.Response(failure, request=request))
    client = AsyncMock()
    client.get.side_effect = error
    with pytest.raises(type(error)):
        await wh.get(client, str(request.url))
    assert client.get.await_count == calls


async def test_retry_recovers(monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_RETRY_BACKOFF_S", 0)
    client = AsyncMock()
    response = httpx.Response(200, json={}, request=httpx.Request("GET", "https://weather.test"))
    client.get.side_effect = [httpx.ReadTimeout("timeout"), response]
    assert await wh.get(client, "https://weather.test") is response
    assert client.get.await_count == 2


async def test_kachelmann_log_masks_exception_and_query(monkeypatch, caplog):
    monkeypatch.setattr(ks, "_get_api_key", lambda _: "secret-api-key")
    monkeypatch.setattr(wh, "get", AsyncMock(side_effect=RuntimeError(
        "https://weather.test?api_key=secret-api-key")))
    with caplog.at_level(logging.WARNING):
        assert await ks._get("/current?key=secret-api-key", 1) is None
    assert "secret-api-key" not in caplog.text
    assert "https://weather.test" not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


async def test_provider_structured_logs_and_health(monkeypatch, caplog):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(health, "_logged", {})
    monkeypatch.setattr(health, "_health", {})
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["kachelmann", "geosphere"])
    fail = AsyncMock(side_effect=ValueError("secret-api-key?query=secret"))
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert await ws._provider_fetch("kachelmann", fail) is None
    records = [r for r in caplog.records if getattr(r, "provider", None) == "kachelmann"]
    assert len(records) == 1
    assert records[0].kind == "current"
    assert records[0].fallback_to == "geosphere"
    assert records[0].reason == "provider_error"
    assert "secret" not in caplog.text
    assert not records[0].exc_info
    assert (await health.provider_health())["kachelmann"]["error_count"] == 3


async def test_hung_http_attempt_has_hard_deadline(monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "WEATHER_HTTP_TIMEOUT", 0.01)
    monkeypatch.setattr(settings, "WEATHER_HTTP_RETRIES", 1)
    monkeypatch.setattr(settings, "WEATHER_RETRY_BACKOFF_S", 0)

    async def hung(*args, **kwargs):
        await asyncio.Event().wait()

    client = AsyncMock()
    client.get.side_effect = hung
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await wh.get(client, "https://weather.test")
    assert 1 <= client.get.await_count <= 2


@pytest.mark.parametrize("provider", ["openmeteo", "geosphere", "kachelmann"])
async def test_all_http_clients_use_retry(monkeypatch, provider):
    from app.services import weather_openmeteo as om

    monkeypatch.setattr(settings, "WEATHER_RETRY_BACKOFF_S", 0)
    monkeypatch.setattr(settings, "WEATHER_HTTP_RETRIES", 1)
    monkeypatch.setattr(ks, "_get_api_key", lambda _: "secret")
    response = httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "https://weather.test"))
    client = AsyncMock()
    client.get.side_effect = [httpx.ConnectError("secret"), response]
    factory = AsyncMock()
    factory.__aenter__.return_value = client
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory)
    if provider == "openmeteo":
        result = await om._fetch(47, 9, {}, None)
    elif provider == "geosphere":
        result = await ws._geosphere_get("forecast", "resource", {})
    else:
        result = await ks._get("/current", 1)
    assert result == {"ok": True}
    assert client.get.await_count == 2
