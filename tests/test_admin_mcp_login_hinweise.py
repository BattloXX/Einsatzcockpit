"""Passwortbeweis, nebenwirkungsfreie MFA-Diagnose und ChatGPT-OAuth ohne Netz."""
import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.admin_mcp.context import DENIED, admin_session as SessionLocal
from app.admin_mcp.login_hinweise import HINWEISE
from app.core.crypto import decrypt_secret
from app.core.security import hash_api_key, sign_session
from app.models.admin_mcp import AdminMCPOAuthCode
from app.models.admin_security import AdminRecoveryCode, AdminTotp, utcnow
from app.models.master import SystemSettings
from app.models.user import AuditLog, User, UserRole
from app.services import totp_service as totp
from tests.test_admin_mcp_oauth import RESOURCE, admin_env, login, start_flow
from tests.test_mcp_fundament import _pkce


@pytest.fixture
def factor(admin_env, monkeypatch):
    now = time.time()
    matching = totp.matching_step
    monkeypatch.setattr(totp, "matching_step", lambda secret, code: matching(secret, code, now=now))
    with SessionLocal() as db:
        row = totp.start_setup(db, db.get(User, admin_env["id"]))
        secret = decrypt_secret(row.secret_enc)
        row.confirmed_at = utcnow()
        recovery = totp.create_recovery_codes(db, admin_env["id"])
        db.get(SystemSettings, "admin_2fa_enabled").value = "true"
        db.commit()
    return {"code": totp.totp(secret, int(now) // 30), "secret": secret, "recovery": recovery}


def audit_reason(user_id, grund):
    with SessionLocal() as db:
        entry = db.query(AuditLog).filter(
            AuditLog.action.in_(("admin_mcp.login_failed", "admin_mcp.denied")),
            AuditLog.user_id == user_id).order_by(AuditLog.id.desc()).first()
        assert json.loads(entry.payload_json) == {"grund": grund}


@pytest.mark.parametrize("grund", ["global_aus", "kein_zugriff", "kein_systemadministrator",
                                  "nicht_eingerichtet", "code_leer", "ungueltig", "gesperrt"])
def test_hinweise_nach_passwort(client, admin_env, factor, grund):
    with SessionLocal() as db:
        user = db.get(User, admin_env["id"])
        row = db.get(AdminTotp, user.id)
        if grund == "global_aus":
            db.get(SystemSettings, "admin_mcp_enabled").value = "false"
        elif grund == "kein_zugriff":
            user.admin_mcp_access = False
        elif grund == "kein_systemadministrator":
            db.delete(db.query(UserRole).filter(UserRole.user_id == user.id).one())
        elif grund == "nicht_eingerichtet":
            row.confirmed_at = None
        elif grund == "gesperrt":
            row.failed_count = 5
            row.locked_until = utcnow() + timedelta(seconds=290)
        db.commit()
    result = login(client, admin_env, start_flow(client), code="" if grund == "code_leer" else "falsch")
    assert result.status_code == 401
    assert HINWEISE[grund].format(minuten=5) in result.text
    audit_reason(admin_env["id"], grund)
    flow = start_flow(client)
    wrong = login(client, admin_env, flow, password="falsch")
    unknown = login(client, admin_env, flow, username="unbekanntes-konto", password="falsch")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.text == unknown.text and DENIED in wrong.text
    if grund == "gesperrt":
        with SessionLocal() as db:
            assert db.get(AdminTotp, admin_env["id"]).failed_count == 5


@pytest.mark.parametrize("setup_started", [False, True])
def test_fehlende_einrichtung(client, admin_env, setup_started):
    if setup_started:
        with SessionLocal() as db:
            totp.start_setup(db, db.get(User, admin_env["id"]))
            db.commit()
    with SessionLocal() as db:
        db.get(SystemSettings, "admin_2fa_enabled").value = "true"
        db.commit()
    result = login(client, admin_env, start_flow(client))
    assert result.status_code == 401
    assert HINWEISE["nicht_eingerichtet"] in result.text


def test_keine_enumeration_und_abgelaufener_vorgang(client, admin_env):
    flow = start_flow(client)
    wrong = login(client, admin_env, flow, password="falsch")
    unknown = login(client, admin_env, flow, username="unbekanntes-konto", password="falsch")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.text == unknown.text
    assert DENIED in wrong.text
    audit_reason(None, "authentifizierung_fehlgeschlagen")
    with SessionLocal() as db:
        db.query(AdminMCPOAuthCode).filter(
            AdminMCPOAuthCode.code_hash == hash_api_key(flow["flow"])).one().expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    expired = login(client, admin_env, flow)
    assert expired.status_code == 401 and expired.text == wrong.text
    audit_reason(admin_env["id"], "vorgang_abgelaufen")


@pytest.mark.parametrize("recovery", [False, True])
def test_replay_und_diagnose_ohne_nebenwirkungen(client, admin_env, factor, recovery):
    code = factor["recovery"][0] if recovery else factor["code"]
    assert login(client, admin_env, start_flow(client), code=code).status_code == 302
    result = login(client, admin_env, start_flow(client), code=code)
    assert result.status_code == 401 and "bereits verwendet" in result.text
    audit_reason(admin_env["id"], "verbraucht")
    with SessionLocal() as db:
        row = db.get(AdminTotp, admin_env["id"])
        before = (row.failed_count, row.last_used_step, row.locked_until)
        assert row.failed_count == 1  # Wie bisher erhoeht verify() auch bei Replay den Zaehler.
        used = [r.used_at for r in db.query(AdminRecoveryCode).filter_by(user_id=row.user_id)]
        count = db.query(AuditLog).count()
        for _ in range(2):
            assert totp.diagnose_failure(db, row.user_id, code) == ("verbraucht", None)
        db.refresh(row)
        assert (row.failed_count, row.last_used_step, row.locked_until) == before
        assert used == [r.used_at for r in db.query(AdminRecoveryCode).filter_by(user_id=row.user_id)]
        assert db.query(AuditLog).count() == count
        assert not db.dirty and not db.new
        audits = json.dumps([(r.action, r.payload_json) for r in db.query(AuditLog).filter_by(user_id=row.user_id)])
        assert all(secret not in audits for secret in [code, factor["secret"], "Test1234!", *factor["recovery"]])


def test_fuenfter_fehlversuch_sperrt_unveraendert(client, admin_env, factor):
    flow = start_flow(client)
    for index in range(5):
        result = login(client, admin_env, flow, code="falsch")
        assert result.status_code == 401
        assert HINWEISE["ungueltig"] in result.text if index < 4 else "in ca. 5 Minuten" in result.text
    audit_reason(admin_env["id"], "gesperrt")
    with SessionLocal() as db:
        row = db.get(AdminTotp, admin_env["id"])
        assert row.failed_count == 5 and row.last_used_step is None
        assert 280 < (row.locked_until - utcnow()).total_seconds() <= 300


REDIRECTS = ["https://chatgpt.com/connector_platform_oauth_redirect",
             "https://chatgpt.com/connector/oauth/test-id",
             "https://chat.openai.com/aip/test-id/oauth/callback"]


@pytest.mark.parametrize("redirect", REDIRECTS)
@pytest.mark.parametrize("scope", [None, "mcp-admin"])
def test_chatgpt_flow(client, admin_env, factor, redirect, scope):
    registration = client.post("/mcp/admin/register", json={"redirect_uris": [redirect],
        "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"]})
    assert registration.status_code == 201, registration.text
    info = registration.json()
    assert info["token_endpoint_auth_method"] == "none" and not info.get("client_secret")
    assert info["scope"] == "mcp-admin"
    verifier, challenge = _pkce()
    params = {"client_id": info["client_id"], "redirect_uri": redirect, "response_type": "code",
              "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE, "state": "test-state"}
    if scope is not None:
        params["scope"] = scope
    authorization = client.get("/mcp/admin/authorize", params=params, follow_redirects=False)
    assert authorization.status_code == 302, authorization.text
    url = authorization.headers["location"]
    flow = {"flow": parse_qs(urlsplit(url).query)["vorgang"][0]}
    page = client.get(url)
    assert page.status_code == 200
    assert "inputmode=\"numeric\"" in page.text and "pattern=" not in page.text
    assert "autocomplete=\"one-time-code\"" in page.text
    logged_in = login(client, admin_env, flow, code=factor["code"])
    assert logged_in.status_code == 302, logged_in.text
    query = parse_qs(urlsplit(logged_in.headers["location"]).query)
    assert query["state"] == ["test-state"]
    response = client.post("/mcp/admin/token", data={"grant_type": "authorization_code",
        "client_id": info["client_id"], "redirect_uri": redirect, "code": query["code"][0],
        "code_verifier": verifier, "resource": RESOURCE})
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "mcp-admin"


def test_unbekannte_scopes(client, admin_env):
    bad = client.post("/mcp/admin/register", json={"redirect_uris": [REDIRECTS[0]], "scope": "openid"})
    assert bad.status_code == 400
    info = client.post("/mcp/admin/register", json={"redirect_uris": [REDIRECTS[0]]}).json()
    _, challenge = _pkce()
    bad = client.get("/mcp/admin/authorize", params={"client_id": info["client_id"],
        "redirect_uri": REDIRECTS[0], "response_type": "code", "scope": "openid",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE}, follow_redirects=False)
    assert bad.status_code == 302
    assert parse_qs(urlsplit(bad.headers["location"]).query)["error"] == ["invalid_scope"]


def test_discovery_aliases(client, admin_env):
    expected = client.get("/.well-known/oauth-authorization-server/mcp/admin").json()
    for path in ("/.well-known/openid-configuration/mcp/admin", "/mcp/admin/.well-known/openid-configuration"):
        assert client.get(path).json() == expected
    resource = client.get("/.well-known/oauth-protected-resource/mcp/admin").json()
    assert client.get("/.well-known/oauth-protected-resource").json() == resource
    assert resource["resource"] == RESOURCE and resource["authorization_servers"] == [RESOURCE]
    assert resource["scopes_supported"] == ["mcp-admin"]
    assert client.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"] == ["mcp"]
    response = client.post("/mcp/admin", json={}, headers={"Accept": "application/json, text/event-stream"})
    assert response.status_code == 401
    assert 'resource_metadata="http://localhost:8092/.well-known/oauth-protected-resource/mcp/admin"' in response.headers["www-authenticate"]


def test_audit_zeigt_nur_bekannte_gruende(client, admin_env):
    with SessionLocal() as db:
        db.add(AuditLog(action="admin_mcp.login_failed", payload_json=json.dumps(
            {"grund": "verbraucht", "password": "GEHEIMER-INHALT"})))
        db.add(AuditLog(action="admin_mcp.denied", payload_json='{"grund":"GEHEIMER-INHALT"}'))
        db.commit()
    client.cookies.set("session", sign_session(admin_env["id"]), domain="testserver.local", path="/")
    page = client.get("/admin/systemdiagnose/teil/audit")
    assert page.status_code == 200
    assert "bereits verwendet" in page.text and "GEHEIMER-INHALT" not in page.text


def test_rate_limit_bleibt_fuenf_pro_minute(client, admin_env):
    flow = start_flow(client)
    for _ in range(5):
        assert login(client, admin_env, flow, password="falsch").status_code == 401
    assert login(client, admin_env, flow, password="falsch").status_code == 429
