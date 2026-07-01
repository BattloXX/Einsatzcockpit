"""Regressionstest PR13 (STAB-7): geocode_address() darf parallele Aufrufe nicht
mehr für die gesamte HTTP-Requestdauer serialisieren — nur der Request-START
muss >= 1.1s auseinanderliegen (Nominatim-Policy), nicht der Request-ABSCHLUSS."""
import asyncio
import time

import httpx
import pytest

from app.services import geocoding


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    geocoding._last_request_ts = 0.0
    yield
    geocoding._last_request_ts = 0.0


@pytest.mark.asyncio
async def test_concurrent_geocode_calls_dont_serialize_on_slow_request(monkeypatch):
    """Ein langsamer erster Call (2s) darf einen zweiten, gleichzeitig gestarteten
    Call nicht um die volle Dauer des ersten verzögern (STAB-7-Regression)."""
    call_start_times: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        call_start_times.append(time.monotonic())
        if "first" in str(request.url):
            await asyncio.sleep(1.5)  # simuliert langsame Nominatim-Antwort
        return httpx.Response(200, json=[{"lat": "47.0", "lon": "9.7", "display_name": "x"}], request=request)

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, **kwargs):
            marker = "first" if params and params.get("q") == "strasse-first" else "second"
            request = httpx.Request("GET", f"http://mock/{marker}")
            return await handler(request)

    monkeypatch.setattr(geocoding.httpx, "AsyncClient", _MockAsyncClient)
    monkeypatch.setattr(geocoding, "_MIN_INTERVAL_S", 0.3)  # Test schneller machen

    t0 = time.monotonic()

    async def _first():
        return await geocoding.geocode_address("strasse-first", None, None)

    async def _second():
        return await geocoding.geocode_address("strasse-second", None, None)

    results = await asyncio.gather(_first(), _second())

    assert all(r is not None for r in results)
    assert len(call_start_times) == 2
    # Die beiden Request-STARTS muessen nah beieinander liegen (Drossel-Intervall,
    # nicht die volle 1.5s-Dauer des ersten, langsamen Requests).
    start_gap = abs(call_start_times[1] - call_start_times[0])
    assert start_gap < 1.0, (
        f"Request-Starts liegen {start_gap:.2f}s auseinander — Lock serialisiert "
        "offenbar noch die gesamte HTTP-Requestdauer (STAB-7-Regression)."
    )
    total = time.monotonic() - t0
    # Gesamtlaufzeit dominiert vom langsamen ersten Call (~1.5s), NICHT
    # 1.5s + 1.5s (was bei vollstaendiger Serialisierung der Fall waere).
    assert total < 2.5


@pytest.mark.asyncio
async def test_sequential_calls_respect_min_interval(monkeypatch):
    call_times: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        call_times.append(time.monotonic())
        return httpx.Response(200, json=[{"lat": "47.0", "lon": "9.7", "display_name": "x"}])

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, **kwargs):
            request = httpx.Request("GET", "http://mock/x")
            return await handler(request)

    monkeypatch.setattr(geocoding.httpx, "AsyncClient", _MockAsyncClient)
    monkeypatch.setattr(geocoding, "_MIN_INTERVAL_S", 0.3)

    await geocoding.geocode_address("a", None, None)
    await geocoding.geocode_address("b", None, None)

    assert len(call_times) == 2
    assert call_times[1] - call_times[0] >= 0.28  # kleine Toleranz fuer Scheduling-Jitter
