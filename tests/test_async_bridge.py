from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.async_bridge import run_async


async def _value_from_running_loop(value: str) -> str:
    asyncio.get_running_loop()
    return value


def test_run_async_from_sync_route_reads_form_and_uses_request_loop() -> None:
    app = FastAPI()

    @app.post("/")
    def endpoint(request: Request) -> dict[str, str]:
        form = run_async(request.form)
        return {"value": run_async(_value_from_running_loop, "ok"), "name": form["name"]}

    response = TestClient(app).post("/", data={"name": "Ada"})

    assert response.status_code == 200
    assert response.json() == {"value": "ok", "name": "Ada"}


def test_run_async_outside_worker_thread_uses_asyncio_run() -> None:
    assert run_async(_value_from_running_loop, "fallback") == "fallback"


@pytest.mark.asyncio
async def test_run_async_inside_running_loop_raises_error() -> None:
    with pytest.raises(RuntimeError, match="laufenden Event-Loop"):
        run_async(_value_from_running_loop, "not reached")
