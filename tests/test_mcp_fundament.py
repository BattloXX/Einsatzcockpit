"""MCP-Fundament: OAuth und Streamable HTTP gegen die echte ASGI-App."""
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole


def _enable_mcp(username="mcp_test_user", slug="mcp-test"):
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=slug, name="MCP Test")
    db.add(org)
    db.flush()
    user = User(username=username, display_name="MCP Test", org_id=org.id, password_hash=hash_password("Test1234!"))
    db.add(user)
    db.flush()
    role = db.query(Role).filter(Role.code == "readonly").first()
    assert role
    db.add(UserRole(user_id=user.id, role_id=role.id))
    setting = OrgSettings(org_id=org.id, mcp_modul_aktiv=True)
    db.add(setting)
    row = db.query(SystemSettings).filter(SystemSettings.key == "mcp_module_enabled").first()
    if row is None:
        row = SystemSettings(key="mcp_module_enabled", value="true")
        db.add(row)
    else:
        row.value = "true"
    result = {"id": user.id, "org_id": org.id, "username": username}
    db.commit()
    db.close()
    return result


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _oauth_tokens(client, user, auth_method: str | None = "none") -> dict:
    payload = {"client_name": "pytest public client", "redirect_uris": ["http://localhost/callback"]}
    if auth_method is not None:
        payload["token_endpoint_auth_method"] = auth_method
    registration = client.post("/register", json=payload)
    assert registration.status_code == 201
    registered = registration.json()
    verifier, challenge = _pkce()
    authorize = client.get(
        "/authorize",
        params={
            "client_id": registered["client_id"],
            "redirect_uri": "http://localhost/callback",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "resource": "http://localhost:8092/mcp",
        },
        follow_redirects=False,
    )
    flow = parse_qs(urlsplit(authorize.headers["location"]).query)["vorgang"][0]
    assert client.get(authorize.headers["location"]).status_code == 200
    csrf = client.cookies["ec_csrf"]
    login = client.post(
        "/mcp/anmelden",
        data={"vorgang": flow, "username": user["username"], "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert login.status_code == 302
    code = parse_qs(urlsplit(login.headers["location"]).query)["code"][0]
    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": registered["client_id"],
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "http://localhost/callback",
            "resource": "http://localhost:8092/mcp",
        },
    )
    assert token.status_code == 200, token.text
    return {**token.json(), "client_id": registered["client_id"]}


def _mcp(client, access_token: str, method: str, params: dict | None, request_id: int):
    response = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Host": "localhost:8092",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-11-25",
        },
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
    )
    return response


def test_mcp_full_public_dcr_pkce_flow_and_refresh_rotation(client):
    assert client.get("/.well-known/oauth-authorization-server").status_code == 200
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
    assert client.get("/.well-known/assetlinks.json").status_code == 200
    assert client.post("/mcp/anmelden", data={"vorgang": "x", "username": "x", "password": "x"}).status_code == 403
    confidential = client.post(
        "/register",
        json={"client_name": "secret client", "redirect_uris": ["http://localhost/callback"]},
    )
    # Clients ohne/mit client_secret-Verfahren werden als oeffentliche PKCE-Clients registriert
    assert confidential.status_code == 201
    assert confidential.json()["token_endpoint_auth_method"] == "none"
    assert not confidential.json().get("client_secret")
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    tokens = _oauth_tokens(client, _enable_mcp())
    initialized = _mcp(
        client,
        tokens["access_token"],
        "initialize",
        {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
        1,
    )
    assert initialized.status_code == 200, initialized.text
    listed = _mcp(client, tokens["access_token"], "tools/list", {}, 2)
    assert listed.status_code == 200 and "mcp_whoami" in listed.text
    called = _mcp(client, tokens["access_token"], "tools/call", {"name": "mcp_whoami", "arguments": {}}, 3)
    assert called.status_code == 200 and "MCP Test" in called.text
    rotated = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": tokens["client_id"],
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert rotated.status_code == 200
    assert client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": tokens["client_id"],
            "refresh_token": tokens["refresh_token"],
        },
    ).status_code == 400


def test_client_ohne_auth_verfahren_durchlaeuft_flow_als_public_client(client):
    """claude.ai registriert evtl. ohne token_endpoint_auth_method (SDK-Default client_secret_post)."""
    for methode in (None, "client_secret_post"):
        tokens = _oauth_tokens(
            client, _enable_mcp(username=f"dcr_{methode}", slug=f"dcr-{methode}"), auth_method=methode
        )
        listed = _mcp(client, tokens["access_token"], "tools/list", {}, 2)
        assert listed.status_code == 200 and "mcp_whoami" in listed.text
