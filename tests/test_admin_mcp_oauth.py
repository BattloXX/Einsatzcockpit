"""Admin-MCP durch die echte ASGI-App: OAuth, Discovery und Live-Widerruf."""
import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from app.admin_mcp.context import admin_session as SessionLocal
from app.core.security import hash_api_key, hash_password, sign_session
from app.models.admin_mcp import AdminMCPOAuthCode, AdminMCPOAuthToken
from app.models.admin_security import AdminTotp, utcnow
from app.models.master import SystemSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services import totp_service as totp
from tests.test_mcp_fundament import _enable_mcp, _mcp, _oauth_tokens, _pkce

BASE = "http://localhost:8092"
RESOURCE = BASE + "/mcp/admin"


@pytest.fixture
def admin_env():
    from app.admin_mcp.server import _tool_limiter
    _tool_limiter.storage.reset()
    with SessionLocal() as db:
        old = {}
        for key, value in (("admin_mcp_enabled", "true"), ("admin_2fa_enabled", "false")):
            row = db.get(SystemSettings, key)
            old[key] = row.value if row else None
            if row is None:
                row = SystemSettings(key=key)
                db.add(row)
            row.value = value
        user = User(username="amcp-" + uuid4().hex[:12], display_name="Admin MCP",
                    password_hash=hash_password("Test1234!"), admin_mcp_access=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "system_admin").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        data = {"id": user.id, "username": user.username, "role_id": role.id}
    yield data
    with SessionLocal() as db:
        for key, value in old.items():
            row = db.get(SystemSettings, key)
            if value is None:
                if row is not None:
                    db.delete(row)
            else:
                row.value = value
        db.commit()


def start_flow(client):
    registration = client.post("/mcp/admin/register", json={
        "client_name": "admin pytest", "redirect_uris": ["http://localhost/callback"],
    })
    assert registration.status_code == 201, registration.text
    info = registration.json()
    assert info["token_endpoint_auth_method"] == "none" and not info.get("client_secret")
    verifier, challenge = _pkce()
    authorization = client.get("/mcp/admin/authorize", params={
        "client_id": info["client_id"], "response_type": "code", "redirect_uri": "http://localhost/callback",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "mcp-admin", "resource": RESOURCE,
    }, follow_redirects=False)
    assert authorization.status_code == 302, authorization.text
    url = authorization.headers["location"]
    flow = parse_qs(urlsplit(url).query)["vorgang"][0]
    page = client.get(url)
    assert page.status_code == 200
    assert "form-action 'self' http://localhost" in page.headers["content-security-policy"]
    return {"flow": flow, "verifier": verifier, "client_id": info["client_id"]}


def login(client, user, flow, **extra):
    return client.post("/mcp/admin/anmelden", data={
        "vorgang": flow["flow"], "username": user["username"], "password": "Test1234!",
        "_csrf": client.cookies["ec_csrf"], **extra,
    }, follow_redirects=False)


def tokens(client, user, **extra):
    flow = start_flow(client)
    logged_in = login(client, user, flow, **extra)
    assert logged_in.status_code == 302, logged_in.text
    code = parse_qs(urlsplit(logged_in.headers["location"]).query)["code"][0]
    result = client.post("/mcp/admin/token", data={
        "grant_type": "authorization_code", "client_id": flow["client_id"], "code": code,
        "code_verifier": flow["verifier"], "redirect_uri": "http://localhost/callback", "resource": RESOURCE,
    })
    assert result.status_code == 200, result.text
    return {**result.json(), "client_id": flow["client_id"]}


def admin_call(client, token, method="tools/list", params=None):
    return client.post("/mcp/admin", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25", "Host": "localhost:8092",
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def refresh(client, values):
    return client.post("/mcp/admin/token", data={"grant_type": "refresh_token",
        "client_id": values["client_id"], "refresh_token": values["refresh_token"]})


def test_discovery_and_cross_server(client, admin_env):
    from app.admin_mcp.registry import ADMIN_TOOLS
    from app.mcp.registry import TOOLS
    org = client.get("/.well-known/oauth-authorization-server").json()
    admin = client.get("/.well-known/oauth-authorization-server/mcp/admin").json()
    assert org["issuer"].rstrip("/") == BASE
    assert org["token_endpoint"] == BASE + "/token" and org["scopes_supported"] == ["mcp"]
    assert admin["issuer"] == RESOURCE and admin["scopes_supported"] == ["mcp-admin"]
    for field, path in (("authorization_endpoint", "authorize"), ("token_endpoint", "token"),
                        ("registration_endpoint", "register"), ("revocation_endpoint", "revoke")):
        assert admin[field] == RESOURCE + "/" + path
    assert admin["token_endpoint_auth_methods_supported"] == ["none"]
    assert admin["code_challenge_methods_supported"] == ["S256"]
    for suffix, scope, issuer in (("mcp", "mcp", BASE), ("mcp/admin", "mcp-admin", RESOURCE)):
        metadata = client.get("/.well-known/oauth-protected-resource/" + suffix).json()
        assert metadata["resource"] == BASE + "/" + suffix
        assert metadata["authorization_servers"] == [issuer]
        assert metadata["scopes_supported"] == [scope]
    assert client.get("/.well-known/assetlinks.json").status_code == 200
    assert not set(TOOLS) & set(ADMIN_TOOLS)
    org_token = _oauth_tokens(client, _enable_mcp("org-" + uuid4().hex[:8], "org-" + uuid4().hex[:8]))
    admin_token = tokens(client, admin_env)
    assert admin_call(client, org_token["access_token"]).status_code == 401
    assert _mcp(client, admin_token["access_token"], "tools/list", {}, 1).status_code == 401
    assert "mcp_whoami" in _mcp(client, org_token["access_token"], "tools/list", {}, 1).text
    listed = admin_call(client, admin_token["access_token"])
    assert listed.status_code == 200, listed.text
    assert all(name in listed.text for name in ADMIN_TOOLS)
    assert "mcp_whoami" not in listed.text


@pytest.mark.parametrize("change", ["admin", "org_admin", "device", "access", "global", "inactive", "missing"])
def test_login_denied_after_password(client, admin_env, change):
    with SessionLocal() as db:
        user = db.get(User, admin_env["id"])
        if change in ("admin", "org_admin"):
            db.delete(db.query(UserRole).filter(UserRole.user_id == user.id).one())
            role = db.query(Role).filter(Role.code == change).one()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        elif change == "device":
            user.is_device = True
        elif change == "access":
            user.admin_mcp_access = False
        elif change == "inactive":
            user.active = False
        elif change == "global":
            db.get(SystemSettings, "admin_mcp_enabled").value = "false"
        else:
            db.delete(db.get(SystemSettings, "admin_mcp_enabled"))
        db.commit()
    result = login(client, admin_env, start_flow(client))
    assert result.status_code == 401
    if change == "inactive":
        assert "Anmeldung oder Zugriff nicht moeglich." in result.text
    else:
        assert "Anmeldung oder Zugriff nicht moeglich." not in result.text
    with SessionLocal() as db:
        assert db.query(AuditLog).filter(AuditLog.action.in_(("admin_mcp.denied", "admin_mcp.login_failed"))).first()


@pytest.mark.parametrize("change", ["role", "inactive", "access", "global", "epoch", "device", "mfa"])
def test_immediate_live_revocation(client, admin_env, change):
    values = tokens(client, admin_env)
    with SessionLocal() as db:
        user = db.get(User, admin_env["id"])
        if change == "role":
            db.delete(db.query(UserRole).filter(UserRole.user_id == user.id).one())
        elif change == "inactive":
            user.active = False
        elif change == "access":
            user.admin_mcp_access = False
        elif change == "device":
            user.is_device = True
        elif change == "global":
            db.get(SystemSettings, "admin_mcp_enabled").value = "false"
        elif change == "mfa":
            db.get(SystemSettings, "admin_2fa_enabled").value = "true"
        else:
            user.session_epoch += 1
        db.commit()
    assert admin_call(client, values["access_token"]).status_code == 401
    assert admin_call(client, values["access_token"], "tools/call", {"name": "admin_redis_pruefen"}).status_code == 401
    assert refresh(client, values).status_code == 400


def test_rotation_absolute_limit_and_revoke(client, admin_env):
    values = tokens(client, admin_env)
    assert 895 <= values["expires_in"] <= 900
    with SessionLocal() as db:
        original = db.query(AdminMCPOAuthToken).filter(
            AdminMCPOAuthToken.token_hash == hash_api_key(values["refresh_token"])).one().family_expires_at
    rotated = refresh(client, values)
    assert rotated.status_code == 200, rotated.text
    assert refresh(client, values).status_code == 400
    assert admin_call(client, values["access_token"]).status_code == 401
    current = {**rotated.json(), "client_id": values["client_id"]}
    with SessionLocal() as db:
        rows = db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.user_id == admin_env["id"]).all()
        assert all(row.family_expires_at == original for row in rows)
        assert all(row.token_hash not in (values["access_token"], values["refresh_token"]) for row in rows)
    revoked = client.post("/mcp/admin/revoke", data={
        "client_id": current["client_id"], "token": current["refresh_token"]})
    assert revoked.status_code == 200
    assert admin_call(client, current["access_token"]).status_code == 401
    assert refresh(client, current).status_code == 400
    expired = tokens(client, admin_env)
    with SessionLocal() as db:
        for row in db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.user_id == admin_env["id"]):
            row.family_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert refresh(client, expired).status_code == 400
    assert admin_call(client, expired["access_token"]).status_code == 401


def test_mfa_totp_recovery_replay_and_audit(client, admin_env):
    from app.core.crypto import decrypt_secret
    with SessionLocal() as db:
        user = db.get(User, admin_env["id"])
        factor = totp.start_setup(db, user)
        secret = decrypt_secret(factor.secret_enc)
        factor.confirmed_at = utcnow()
        recovery = totp.create_recovery_codes(db, user.id)
        db.get(SystemSettings, "admin_2fa_enabled").value = "true"
        db.commit()
    flow = start_flow(client)
    assert login(client, admin_env, flow).status_code == 401
    code = totp.totp(secret, int(time.time()) // 30)
    values = tokens(client, admin_env, code=code)
    assert refresh(client, values).status_code == 200
    assert login(client, admin_env, flow, code=code).status_code == 401
    other = tokens(client, admin_env, code=recovery[0])
    assert login(client, admin_env, flow, code=recovery[0]).status_code == 401
    with SessionLocal() as db:
        for row in db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.user_id == admin_env["id"]):
            row.mfa_at = utcnow() - timedelta(hours=8, seconds=1)
        db.commit()
    assert refresh(client, other).status_code == 400
    with SessionLocal() as db:
        audits = db.query(AuditLog).filter(AuditLog.user_id == admin_env["id"]).all()
        text = json.dumps([(row.action, row.payload_json) for row in audits])
        assert all(value not in text for value in [secret, code, *recovery, "Test1234!"])
        assert {r.action for r in audits} >= {"admin_mcp.login", "admin_mcp.login_failed", "admin.mfa.failed"}


def test_csrf_and_expired_code(client, admin_env):
    flow = start_flow(client)
    assert client.post("/mcp/admin/anmelden", data={"vorgang": flow["flow"],
        "username": admin_env["username"], "password": "Test1234!"}).status_code == 403
    with SessionLocal() as db:
        db.query(AdminMCPOAuthCode).filter(AdminMCPOAuthCode.code_hash == hash_api_key(flow["flow"])).one()\
            .expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert login(client, admin_env, flow).status_code == 401


def test_ui_revokes_only_admin_tokens_without_epoch(client, admin_env):
    values = tokens(client, admin_env)
    client.cookies.set("session", sign_session(admin_env["id"]), domain="testserver.local", path="/")
    assert client.get("/admin/system-einstellungen").status_code == 200
    result = client.post("/admin/system-einstellungen/admin-mcp/zugang", data={
        "user_id": admin_env["id"], "enabled": "false", "_csrf": client.cookies["ec_csrf"]}, follow_redirects=False)
    assert result.status_code == 303, result.text
    with SessionLocal() as db:
        assert db.get(User, admin_env["id"]).session_epoch == 0
        assert not db.get(User, admin_env["id"]).admin_mcp_access
    assert admin_call(client, values["access_token"]).status_code == 401


def test_ui_mfa_freshness_and_global_kill_switch(client, admin_env):
    from app.core.crypto import encrypt_secret
    values = tokens(client, admin_env)
    now = int(time.time())
    with SessionLocal() as db:
        db.get(SystemSettings, "admin_2fa_enabled").value = "true"
        db.add(AdminTotp(user_id=admin_env["id"], secret_enc=encrypt_secret(totp.generate_secret()),
                         confirmed_at=utcnow()))
        db.commit()
    client.cookies.set("session", sign_session(admin_env["id"], mfa_at=now - 600, session_started_at=now - 700),
                       domain="testserver.local", path="/")
    data = {"user_id": admin_env["id"], "enabled": "true", "_csrf": client.cookies["ec_csrf"]}
    assert client.post("/admin/system-einstellungen/admin-mcp/zugang", data=data).status_code == 403
    client.cookies.set("session", sign_session(admin_env["id"], mfa_at=now, session_started_at=now),
                       domain="testserver.local", path="/")
    result = client.post("/admin/system-einstellungen/admin-mcp/zugang", data=data, follow_redirects=False)
    assert result.status_code == 303
    with SessionLocal() as db:
        assert db.get(User, admin_env["id"]).session_epoch == 0
        rows = db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.user_id == admin_env["id"])
        assert all(r.revoked_at for r in rows)
    assert client.post("/admin/system-einstellungen/admin-mcp", data={
        "enabled": "false", "_csrf": client.cookies["ec_csrf"]}, follow_redirects=False).status_code == 303
    assert admin_call(client, values["access_token"]).status_code == 401
    assert client.post("/admin/system-einstellungen", data={
        "new_key": "admin_mcp_enabled", "new_value": "true", "_csrf": client.cookies["ec_csrf"]}).status_code == 400


def test_resource_binding_and_client_binding(client, admin_env):
    values = tokens(client, admin_env)
    other = start_flow(client)
    wrong_client = {**values, "client_id": other["client_id"]}
    assert refresh(client, wrong_client).status_code == 400
    flow = start_flow(client)
    logged_in = login(client, admin_env, flow)
    code = parse_qs(urlsplit(logged_in.headers["location"]).query)["code"][0]
    wrong_resource = client.post("/mcp/admin/token", data={
        "grant_type": "authorization_code", "client_id": flow["client_id"], "code": code,
        "code_verifier": flow["verifier"], "redirect_uri": "http://localhost/callback", "resource": BASE + "/mcp",
    })
    assert wrong_resource.status_code == 400


def test_admin_lifespan_twice():
    from fastapi.testclient import TestClient

    from app.main import app
    for _ in range(2):
        with TestClient(app) as client:
            result = client.post("/mcp/admin", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                 headers={"Accept": "application/json, text/event-stream"})
            assert result.status_code == 401
            assert "/.well-known/oauth-protected-resource/mcp/admin" in result.headers["www-authenticate"]
