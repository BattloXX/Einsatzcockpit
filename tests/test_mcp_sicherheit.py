"""MCP-Sicherheits- und Lifespan-Regressionen."""

from fastapi.testclient import TestClient

from app.main import app


def test_mcp_session_manager_is_recreated_for_each_lifespan():
    """Ein wiederverwendetes FastAPI-App-Objekt darf zweimal starten."""
    for _ in range(2):
        with TestClient(app) as client:
            response = client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            # Die Route ist erreichbar; ohne Bearer-Token verweigert sie korrekt.
            assert response.status_code == 401
