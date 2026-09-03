"""Der Lage-WebSocket wird auch bei unerwarteten Fehlern deregistriert."""
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
