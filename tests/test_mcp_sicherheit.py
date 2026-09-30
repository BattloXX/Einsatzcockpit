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


def test_mcp_akzeptiert_oeffentlichen_host_header_hinter_proxy(client):
    """Regression: das SDK wies fremde Host-Header mit 421 ab (nur localhost erlaubt)."""
    from tests.test_mcp_fundament import _enable_mcp, _oauth_tokens

    tokens = _oauth_tokens(client, _enable_mcp(username="hostheader_user", slug="hostheader-org"))
    for host in ("test.einsatzcockpit.com", "einsatzcockpit.example:8443"):
        response = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Host": host,
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert response.status_code == 200, (host, response.text)
