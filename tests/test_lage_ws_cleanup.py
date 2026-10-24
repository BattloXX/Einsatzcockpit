"""Der Lage-WebSocket wird auch bei unerwarteten Fehlern deregistriert."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import ws as ws_router


class _Db:
    def get(self, model, lage_id):
        return SimpleNamespace(org_id=7)

    def close(self):
        pass


@pytest.mark.asyncio
async def test_lage_ws_cleanup_bei_fremdexception(monkeypatch):
    websocket = SimpleNamespace(receive_text=AsyncMock(side_effect=RuntimeError("kaputt")))
    monkeypatch.setattr(ws_router, "_resolve_user", lambda ws: SimpleNamespace(org_id=7))
    monkeypatch.setattr(ws_router, "SessionLocal", _Db)
    monkeypatch.setattr(ws_router, "set_tenant_context", lambda db, org_id: None)
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(ws_router.manager, "connect", connect)
    monkeypatch.setattr(ws_router.manager, "disconnect", disconnect)

    with pytest.raises(RuntimeError, match="kaputt"):
        await ws_router.lage_ws(websocket, 42)

    connect.assert_awaited_once_with(ws_router.LAGE_WS_OFFSET + 42, websocket)
    disconnect.assert_awaited_once_with(ws_router.LAGE_WS_OFFSET + 42, websocket)


@pytest.mark.asyncio
async def test_lage_ws_prueft_berechtigung_auch_ohne_client_ping(monkeypatch):
    async def keine_nachricht():
        await asyncio.sleep(10)

    websocket = SimpleNamespace(receive_text=keine_nachricht, send_json=AsyncMock(), close=AsyncMock())
    principal = iter([SimpleNamespace(org_id=7), SimpleNamespace(org_id=7), None])
    monkeypatch.setattr(ws_router, "_resolve_user", lambda ws: next(principal))
    monkeypatch.setattr(ws_router, "_LAGE_AUTH_CHECK_SECONDS", .01, raising=False)
    monkeypatch.setattr(ws_router, "SessionLocal", _Db)
    monkeypatch.setattr(ws_router, "set_tenant_context", lambda db, org_id: None)
    monkeypatch.setattr(ws_router.manager, "connect", AsyncMock())
    disconnect = AsyncMock()
    monkeypatch.setattr(ws_router.manager, "disconnect", disconnect)

    await asyncio.wait_for(ws_router.lage_ws(websocket, 42), timeout=1)

    websocket.send_json.assert_awaited_once_with({"type": "zugang:widerrufen"})
    websocket.close.assert_awaited_once_with(code=4401)
    disconnect.assert_awaited_once_with(ws_router.LAGE_WS_OFFSET + 42, websocket)
