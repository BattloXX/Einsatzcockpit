"""Zentrales MFA-Gate unabhaengig von der Herkunft der Session."""
import time

import pytest

from app.core.security import _signer, session_security, sign_session, unsign_session
from app.models.admin_security import AdminTotp
from app.models.master import SystemSettings
from app.services import totp_service as service
from tests.test_admin_totp import enroll, mfa_env  # noqa: F401 -- geteilte isolierte DB-Fixture


def set_session(client, token):
    client.cookies.set("session", token, domain="testserver.local", path="/")


def csrf(client):
    return {"X-CSRF-Token": client.cookies.get("ec_csrf")}


def activate(db, user):
    secret, codes = enroll(db, user)
    service.set_enabled(db, True, user.id, None)
    db.commit()
    return secret, codes


@pytest.mark.parametrize("legacy", [False, True])
def test_default_off_and_legacy_cookie(mfa_env, legacy):
    db, user, _, client = mfa_env
    set_session(client, _signer.dumps(user.id) if legacy else sign_session(user.id))
    assert client.get("/admin/system-einstellungen", follow_redirects=False).status_code == 200
    assert len(unsign_session(client.cookies.get("session"))) == 8


@pytest.mark.parametrize("global_enabled", [False, True])
@pytest.mark.parametrize("kind", ["password", "sso", "switch", "pin"])
def test_login_paths_gate(mfa_env, monkeypatch, kind, global_enabled):
    db, user, _, client = mfa_env
    if global_enabled:
        activate(db, user)
    if kind == "password":
        response = client.post("/login", data={"username": user.username, "password": "password"},
                               headers=csrf(client), follow_redirects=False)
        assert response.status_code == 302
    elif kind == "switch":
        from app.core.multi_account import ACCOUNTS_COOKIE, add_account, sign_accounts
        client.cookies.set(ACCOUNTS_COOKIE, sign_accounts(add_account([], user.id, False)))
        response = client.post("/benutzer/wechseln", data={"user_id": user.id},
                               headers=csrf(client), follow_redirects=False)
        assert response.status_code == 303
    elif kind == "pin":
        from datetime import timedelta
        from app.models.login_pin import LoginPin
        from app.routers.ui_pin_login import _hash_pin
        user.phone = "+436641234567"
        db.add(LoginPin(user_id=user.id, pin_hash=_hash_pin("123456"),
                        expires_at=service.utcnow() + timedelta(minutes=10)))
        db.commit()
        response = client.post("/pin-login/code", data={"phone": user.phone, "pin": "123456"},
                               headers=csrf(client), follow_redirects=False)
        assert response.status_code == 302
    else:
        from unittest.mock import AsyncMock
        from app.core.crypto import encrypt_secret
        from app.models.master import FireDept
        from app.models.sso import OrgSsoConfig
        from app.routers import sso
        org = FireDept(slug="mfa-sso", name="SSO Org")
        db.add(org)
        db.flush()
        db.add(OrgSsoConfig(org_id=org.id, enabled=True, tenant_id="tenant", client_id="client",
                            client_secret_enc=encrypt_secret("secret"), default_role_id=user.roles[0].id))
        user.org_id, user.entra_oid = org.id, "oid"
        db.commit()
        monkeypatch.setattr(sso, "exchange_code", AsyncMock(return_value={"id_token": "token"}))
        monkeypatch.setattr(sso, "validate_id_token", AsyncMock(return_value={"oid": "oid", "tid": "tenant"}))
        monkeypatch.setattr(sso, "get_groups", AsyncMock(return_value=[]))
        client.cookies.set("sso_flow", sso._flow_signer.dumps({
            "s": "state", "slug": org.slug, "v": "verifier", "n": "nonce",
        }), domain="testserver.local", path="/")
        response = client.get("/sso/mfa-sso/callback?state=state&code=code", follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"] == "/"
    response = client.get("/admin/system-einstellungen", follow_redirects=False)
    if global_enabled:
        assert response.status_code == 303 and response.headers["location"] == "/mfa/verify"
        assert client.get("/mfa/verify").status_code == 200
    else:
        assert response.status_code == 200


def test_json_htmx_and_nonadmin(mfa_env):
    db, user, regular, client = mfa_env
    activate(db, user)
    set_session(client, sign_session(user.id))
    for headers in ({"Accept": "application/json"}, {"HX-Request": "true"}):
        response = client.get("/admin/system-einstellungen", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "mfa_required"
    assert client.get("/api/v1/device/native-link?path=/").json()["code"] == "mfa_required"
    set_session(client, sign_session(regular.id))
    response = client.get("/", follow_redirects=False)
    assert response.headers.get("location") != "/mfa/verify"


@pytest.mark.parametrize("remember", [False, True])
def test_verification_and_sliding_preserve_marker(mfa_env, remember):
    db, user, _, client = mfa_env
    _, codes = activate(db, user)
    set_session(client, sign_session(user.id, remember=remember))
    response = client.post("/mfa/verify", data={"code": codes[0]}, headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303
    before = session_security(client.cookies.get("session"))
    assert before[1] is not None
    assert unsign_session(client.cookies.get("session"))[6] == remember
    assert client.get("/admin/system-einstellungen").status_code == 200
    assert session_security(client.cookies.get("session")) == before
    response = client.post("/mfa/verify", data={"code": codes[0]}, headers=csrf(client), follow_redirects=False)
    assert "Code ungueltig" in response.text


def test_old_marker_epoch_and_legacy_revocation(mfa_env):
    db, user, _, client = mfa_env
    activate(db, user)
    set_session(client, sign_session(user.id, mfa_at=int(time.time()) - 10))
    assert client.get("/admin/system-einstellungen", follow_redirects=False).headers["location"] == "/mfa/verify"
    service.set_enabled(db, False, user.id, int(time.time()))
    db.commit()
    for token in (_signer.dumps(user.id), sign_session(user.id), sign_session(user.id, mfa_at=int(time.time()))):
        set_session(client, token)
        assert client.get("/admin/system-einstellungen", follow_redirects=False).status_code in (401, 302)
    response = client.post("/login", data={"username": user.username, "password": "password"},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 302
    assert session_security(client.cookies.get("session"))[0] == 1
    assert client.get("/admin/system-einstellungen").status_code == 200


@pytest.mark.parametrize("path", ["/mfa/verify", "/mfa/bestaetigen", "/mfa/zuruecksetzen", "/mfa/global"])
def test_csrf_required(mfa_env, path):
    db, user, _, client = mfa_env
    set_session(client, sign_session(user.id))
    assert client.post(path, data={"code": "123456"}).status_code == 403


def test_setup_confirmation_and_one_time_display(mfa_env):
    db, user, _, client = mfa_env
    set_session(client, sign_session(user.id))
    response = client.get("/mfa/einrichten")
    assert "data:image/png;base64," in response.text and response.headers["cache-control"] == "no-store"
    db.expire_all()
    from app.core.crypto import decrypt_secret
    secret = decrypt_secret(db.get(AdminTotp, user.id).secret_enc)
    response = client.post("/mfa/bestaetigen", data={"code": service.totp(secret, int(time.time()) // 30)},
                           headers=csrf(client), follow_redirects=False)
    assert "10 Recovery-Codes" in response.text and response.headers["cache-control"] == "no-store"
    assert "10 Recovery-Codes" not in client.get("/mfa/einrichten").text
    assert client.post("/mfa/bestaetigen", data={"code": "123456"}, headers=csrf(client)).status_code == 400


def test_global_routes_guard_custom_key_and_rate_limit(mfa_env):
    db, user, _, client = mfa_env
    set_session(client, sign_session(user.id))
    response = client.post("/mfa/global", data={"enabled": "true"}, headers=csrf(client))
    assert "MFA Admin" in response.text
    assert client.post("/admin/system-einstellungen", data={"new_key": "admin_2fa_enabled", "new_value": "true"},
                       headers=csrf(client)).status_code == 400
    _, codes = activate(db, user)
    for _ in range(5):
        client.post("/mfa/verify", data={"code": "invalid"}, headers=csrf(client))
    response = client.post("/mfa/verify", data={"code": codes[0]}, headers=csrf(client), follow_redirects=False)
    assert response.status_code == 200 and "gesperrt" in response.text
    assert session_security(client.cookies.get("session"))[1] is None


def test_global_disable_freshness(mfa_env):
    db, user, _, client = mfa_env
    activate(db, user)
    set_session(client, sign_session(user.id, mfa_at=int(time.time()) - 301,
                                               session_started_at=int(time.time()) - 400))
    response = client.post("/mfa/global", data={"enabled": "false"}, headers=csrf(client), follow_redirects=False)
    assert "maximal 5 Minuten" in response.text
    assert service.enabled(db)
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    response = client.post("/mfa/global", data={"enabled": "false"}, headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    assert not service.enabled(db) and db.get(type(user), user.id).session_epoch == 1


def test_reset_authorization_and_epoch(mfa_env):
    db, user, other, client = mfa_env
    _, codes = activate(db, user)
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    assert client.post("/mfa/zuruecksetzen", headers=csrf(client)).status_code == 403
    set_session(client, sign_session(other.id))
    assert client.post("/mfa/zuruecksetzen", data={"user_id": user.id}, headers=csrf(client)).status_code == 403
    from app.models.user import UserRole
    db.add(UserRole(user_id=other.id, role_id=user.roles[0].id))
    db.commit()
    # Andere Admins brauchen bei aktiver Pflicht frische MFA, auch auf /mfa/*.
    assert client.post("/mfa/zuruecksetzen", data={"user_id": user.id}, headers=csrf(client)).status_code == 403
    set_session(client, sign_session(other.id, mfa_at=int(time.time())))
    response = client.post("/mfa/zuruecksetzen", data={"user_id": user.id}, headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    assert db.get(AdminTotp, user.id) is None and user.session_epoch == 1
    assert service.enabled(db)


def test_self_reset_and_stale_account_cookie(mfa_env):
    db, user, _, client = mfa_env
    from app.core.multi_account import ACCOUNTS_COOKIE, add_account, sign_accounts
    _, codes = enroll(db, user)
    old_accounts = sign_accounts(add_account([], user.id, False))
    set_session(client, sign_session(user.id))
    response = client.post("/mfa/zuruecksetzen", data={"code": codes[0]}, headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    client.cookies.set(ACCOUNTS_COOKIE, old_accounts, domain="testserver.local", path="/")
    client.post("/benutzer/wechseln", data={"user_id": user.id}, headers=csrf(client), follow_redirects=False)
    assert not client.cookies.get("session")


def test_cli_resets_are_local_and_audited(mfa_env, monkeypatch):
    db, user, _, client = mfa_env
    from app import cli
    import app.main as main
    from app.models.user import AuditLog
    activate(db, user)
    monkeypatch.setattr(cli, "SessionLocal", main.SessionLocal)
    cli.mfa_reset(user.username)
    db.expire_all()
    assert db.get(AdminTotp, user.id) is None and user.session_epoch == 1
    cli.mfa_global_aus()
    db.expire_all()
    assert not service.enabled(db) and user.session_epoch == 2
    assert {a.action for a in db.query(AuditLog).filter(AuditLog.payload_json.contains('"local": true'))} >= {
        "admin.mfa.reset", "admin.mfa.global_disabled",
    }
    assert client.post("/mfa-global-aus", headers=csrf(client)).status_code == 404


@pytest.mark.parametrize("global_enabled", [False, True])
def test_org_mcp_admin_login(mfa_env, global_enabled):
    from datetime import timedelta
    from app.core.security import hash_api_key
    from app.models.master import FireDept, OrgSettings
    from app.models.mcp import MCPOAuthCode
    db, user, _, client = mfa_env
    org = FireDept(slug="mfa-mcp", name="MCP")
    db.add(org)
    db.flush()
    user.org_id = org.id
    db.add(OrgSettings(org_id=org.id, mcp_modul_aktiv=True))
    db.add(SystemSettings(key="mcp_module_enabled", value="true"))
    db.add(MCPOAuthCode(code_hash=hash_api_key("vorgang"), client_id="client", redirect_uri="https://example.com/cb",
                       code_challenge="challenge", scopes="mcp", expires_at=service.utcnow() + timedelta(minutes=5)))
    db.commit()
    if global_enabled:
        activate(db, user)
    response = client.post("/mcp/anmelden", data={"vorgang": "vorgang", "username": user.username, "password": "password"},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == (403 if global_enabled else 302)


def test_websocket_gate_and_epoch(mfa_env, monkeypatch):
    from types import SimpleNamespace
    from app.routers import ws
    import app.main as main
    db, user, _, _ = mfa_env
    activate(db, user)
    monkeypatch.setattr(ws, "SessionLocal", main.SessionLocal)
    socket = SimpleNamespace(cookies={"session": sign_session(user.id)})
    assert ws._resolve_user(socket) is None
    socket.cookies["session"] = sign_session(user.id, mfa_at=int(time.time()))
    assert ws._resolve_user(socket).id == user.id
    user.session_epoch += 1
    db.commit()
    assert ws._resolve_user(socket) is None


def test_native_handoff_cannot_inherit_mfa_from_revoked_cookie(mfa_env):
    from app.core.security import sign_native_link_token
    db, user, _, client = mfa_env
    activate(db, user)
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    user.session_epoch += 1
    db.commit()
    path = "/admin/system-einstellungen"
    handoff = sign_native_link_token(path, user.id)
    response = client.get(path, params={"nt": handoff}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/mfa/verify"


def test_legacy_remember_and_nonadmin_epoch(mfa_env):
    db, user, regular, client = mfa_env
    set_session(client, _signer.dumps({"u": user.id, "r": 1}))
    assert client.get("/admin/system-einstellungen").status_code == 200
    assert unsign_session(client.cookies.get("session"))[6]
    regular.session_epoch = 1
    db.commit()
    set_session(client, sign_session(regular.id))
    # Auch normale Benutzer mit widerrufener Epoche werden nicht authentifiziert.
    assert client.get("/mfa/einrichten", follow_redirects=False).status_code in (401, 302)
    set_session(client, sign_session(regular.id, session_epoch=1))
    assert client.get("/mfa/einrichten", follow_redirects=False).status_code == 403


def test_verify_does_not_redirect_to_secret_page(mfa_env):
    db, user, _, client = mfa_env
    set_session(client, sign_session(user.id))
    response = client.get("/mfa/verify", follow_redirects=False)
    assert response.status_code == 200 and 'href="/mfa/einrichten"' in response.text
    assert "data:image/png" not in response.text and "Manueller Schluessel" not in response.text
    assert db.get(AdminTotp, user.id) is None
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path,key,control", [
    ("/mfa/global", "admin_2fa_enabled", "mfa-enabled"),
    ("/admin/system-einstellungen/admin-mcp", "admin_mcp_enabled", "mcp-enabled"),
    ("/admin/system-einstellungen/admin-mcp/reparatur", "admin_repair_enabled", "repair-enabled"),
])
@pytest.mark.parametrize("enabled", [False, True])
def test_settings_switch_form_roundtrip(request, path, key, control, enabled):
    import re
    from urllib.parse import urlencode

    db, user, _, client = request.getfixturevalue("mfa_env")
    activate(db, user)
    if key != "admin_2fa_enabled":
        db.add(SystemSettings(key=key, value="true"))
        db.commit()
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    page = client.get("/admin/system-einstellungen")
    assert page.status_code == 200
    form = re.search(r'<form\b[^>]*action="' + re.escape(path) + r'"[^>]*>(.*?)</form>', page.text, re.S)
    assert form is not None
    hidden = '<input type="hidden" name="enabled" value="false">'
    checkbox = re.search(r'<input\b[^>]*id="' + control + r'"[^>]*>', form[1])
    assert checkbox is not None and 'role="switch"' in checkbox[0] and 'checked' in checkbox[0]
    assert 'name="enabled" value="true"' in checkbox[0]
    assert form[1].index(hidden) < checkbox.start()
    # Browser-Reihenfolge: ausgeschaltet nur false, eingeschaltet false gefolgt von true.
    fields = [("_csrf", client.cookies["ec_csrf"]), ("enabled", "false")]
    if enabled:
        fields.append(("enabled", "true"))
    response = client.post(path, content=urlencode(fields),
                           headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    db.expire_all()
    assert db.get(SystemSettings, key).value == str(enabled).lower()
    if key != "admin_2fa_enabled" or enabled:
        updated = client.get("/admin/system-einstellungen")
        checkbox = re.search(r'<input\b[^>]*id="' + control + r'"[^>]*>', updated.text)
        assert checkbox is not None and ('checked' in checkbox[0]) == enabled
