"""Org-MFA-Richtlinien und kompatible Login-Metadaten fuer Phase P1."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import _signer, session_login_method, session_security, sign_session
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.admin_security import AdminRecoveryCode, AdminTotp, UserRecoveryCode, UserTotp
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from app.services import mfa_service, totp_service
from tests.test_admin_totp import enroll, mfa_env  # noqa: F401 -- isolierte Integrations-Fixture


@pytest.fixture(autouse=True)
def isolated_cache():
    mfa_service.clear_policy_cache()
    yield
    mfa_service.clear_policy_cache()


@pytest.fixture
def policy_env():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        set_tenant_context(db, None)
        org = FireDept(id=74401, slug="mfa-policy-a", name="MFA Policy A")
        other = FireDept(id=74402, slug="mfa-policy-b", name="MFA Policy B")
        config = OrgSettings(org_id=org.id)
        user = User(username="policy-user", display_name="Policy User", org_id=org.id)
        db.add_all([org, other, config, user, OrgSettings(org_id=other.id, mfa_policy="alle")])
        db.commit()
        yield db, user, config
    engine.dispose()


@pytest.mark.parametrize("policy", mfa_service.MFA_POLICIES)
@pytest.mark.parametrize("role", ["admin", "org_admin", "readonly", "fahrtenbuch_admin"])
def test_policies_and_roles(policy_env, policy, role):
    db, user, config = policy_env
    assigned = Role(code=role, label=role)
    user.user_roles.append(UserRole(role=assigned))
    config.mfa_policy = policy
    db.commit()
    assert mfa_service.required_for(db, user) == (
        policy == "alle" or (policy == "admins" and role in {"admin", "org_admin"})
    )


@pytest.mark.parametrize("exempt", [False, True])
@pytest.mark.parametrize("policy", ["admins", "alle"])
def test_sso_exemption(policy_env, exempt, policy):
    db, user, config = policy_env
    user.user_roles.append(UserRole(role=Role(code="org_admin", label="Org Admin")))
    user.entra_oid = "policy-oid"
    config.mfa_policy = policy
    config.mfa_sso_ausgenommen = exempt
    db.commit()
    assert mfa_service.required_for(db, user) is (not exempt)


@pytest.mark.parametrize("system_admin", [False, True])
def test_devices_never_require_mfa(policy_env, system_admin):
    db, user, config = policy_env
    user.is_device = True
    if system_admin:
        user.user_roles.append(UserRole(role=Role(code="system_admin", label="Systemadmin")))
    config.mfa_policy = "alle"
    db.add(SystemSettings(key="admin_2fa_enabled", value="true"))
    db.commit()
    assert not mfa_service.required_for(db, user)


@pytest.mark.parametrize("global_enabled", [False, True])
@pytest.mark.parametrize("policy", mfa_service.MFA_POLICIES)
def test_system_admin_uses_global_switch(policy_env, global_enabled, policy):
    db, user, config = policy_env
    user.user_roles.append(UserRole(role=Role(code="system_admin", label="Systemadmin")))
    user.entra_oid = "system-oid"
    config.mfa_policy = policy
    db.add(SystemSettings(key="admin_2fa_enabled", value=str(global_enabled).lower()))
    db.commit()
    assert mfa_service.required_for(db, user) is global_enabled


def test_missing_org_policy_defaults_off(policy_env):
    db, user, config = policy_env
    db.delete(config)
    db.commit()
    assert not mfa_service.required_for(db, user)
    user.org_id = None
    assert not mfa_service.required_for(db, user)


def test_policy_cache_ttl_clear_and_org_isolation(policy_env, monkeypatch):
    db, user, config = policy_env
    clock = [100.0]
    monkeypatch.setattr(mfa_service.time, "monotonic", lambda: clock[0])
    lookups = []

    def count_lookup(state):
        if "org_settings.mfa_policy" in str(state.statement):
            lookups.append(state.statement)

    event.listen(db, "do_orm_execute", count_lookup)
    # Die explizite Benutzer-Org muss auch bei abweichendem Session-Tenant gelten.
    set_tenant_context(db, 74402)
    assert not mfa_service.required_for(db, user)
    assert not mfa_service.required_for(db, user)
    assert len(lookups) == 1
    config.mfa_policy = "alle"
    db.flush()
    clock[0] = 129.9
    assert not mfa_service.required_for(db, user)
    clock[0] = 130.0
    assert mfa_service.required_for(db, user)
    assert len(lookups) == 2
    config.mfa_sso_ausgenommen = False
    user.entra_oid = "cached-oid"
    db.flush()
    assert not mfa_service.required_for(db, user)
    mfa_service.clear_policy_cache()
    assert mfa_service.required_for(db, user)
    user.org_id = 74402
    user.entra_oid = None
    assert mfa_service.required_for(db, user)
    assert len(lookups) == 4


def test_reexports_and_internal_monkeypatch(monkeypatch):
    assert totp_service is mfa_service
    for name in ("MAX_FAILED", "LOCK_SECONDS", "enabled", "admins", "verify", "reset", "notify_reset",
                 "fresh", "set_enabled", "start_setup", "create_recovery_codes", "diagnose_failure",
                 "matching_step", "totp", "provisioning_uri", "qr_data_uri", "audit", "generate_secret",
                 "recovery_hash"):
        assert getattr(totp_service, name) is getattr(mfa_service, name)
    assert UserTotp is AdminTotp
    assert UserRecoveryCode is AdminRecoveryCode
    monkeypatch.setattr(totp_service, "totp", lambda *args: "123456")
    assert mfa_service.matching_step("unused", "123456", now=3000) == 101


@pytest.mark.parametrize("method", ["pw", "sso", "pin", "switch"])
def test_session_login_methods(method):
    token = sign_session(123, login_method=method, session_epoch=3, mfa_at=20, session_started_at=10)
    assert session_login_method(token) == method
    assert session_security(token) == (3, 20, 10)
    assert _signer.loads(token)["lm"] == method


@pytest.mark.parametrize("make_token", [
    lambda: _signer.dumps(123), lambda: _signer.dumps({"u": 123, "r": 1}), lambda: sign_session(123),
    lambda: "invalid",
], ids=["int", "dict_ohne_lm", "neu_ohne_lm", "ungueltig"])
def test_legacy_login_method(make_token):
    assert session_login_method(make_token()) == "pw"


@pytest.mark.parametrize("kind", ["qr", "device"])
def test_special_sessions_do_not_store_login_method(kind):
    token = sign_session(123, **{kind: True}, login_method="pin")
    assert "lm" not in _signer.loads(token)
    assert session_login_method(token) == "pw"


@pytest.mark.parametrize("remember", [False, True])
@pytest.mark.parametrize("method", ["pw", "sso", "pin", "switch"])
def test_verification_and_sliding_preserve_login_method(request, remember, method):
    db, user, _, client = request.getfixturevalue("mfa_env")
    _, codes = enroll(db, user)
    client.cookies.set("session", sign_session(user.id, remember=remember, login_method=method),
                       domain="testserver.local", path="/")
    response = client.post("/mfa/verify", data={"code": codes[0]},
                           headers={"X-CSRF-Token": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert response.status_code == 303
    token = client.cookies.get("session")
    assert session_login_method(token) == method
    security = session_security(token)
    assert security[1] is not None
    response = client.get("/admin/system-einstellungen")
    assert response.status_code == 200
    assert "session=" in response.headers["set-cookie"]
    assert session_login_method(client.cookies.get("session")) == method
    assert session_security(client.cookies.get("session")) == security


@pytest.mark.parametrize("method", ["pw", "sso", "pin", "switch"])
def test_login_routes_record_method(request, monkeypatch, method):
    db, user, _, client = request.getfixturevalue("mfa_env")
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    if method == "pw":
        response = client.post("/login", data={"username": user.username, "password": "password"},
                               headers=headers, follow_redirects=False)
    elif method == "switch":
        from app.core.multi_account import ACCOUNTS_COOKIE, add_account, sign_accounts
        client.cookies.set(ACCOUNTS_COOKIE, sign_accounts(add_account([], user.id, False)))
        response = client.post("/benutzer/wechseln", data={"user_id": user.id},
                               headers=headers, follow_redirects=False)
    elif method == "pin":
        from app.models.login_pin import LoginPin
        from app.routers.ui_pin_login import _hash_pin
        user.phone = "+436649876543"
        db.add(LoginPin(user_id=user.id, pin_hash=_hash_pin("123456"),
                        expires_at=totp_service.utcnow() + timedelta(minutes=10)))
        db.commit()
        response = client.post("/pin-login/code", data={"phone": user.phone, "pin": "123456"},
                               headers=headers, follow_redirects=False)
    else:
        from app.core.crypto import encrypt_secret
        from app.models.sso import OrgSsoConfig
        from app.routers import sso
        org = FireDept(id=74403, slug="policy-sso", name="Policy SSO")
        db.add(org)
        db.flush()
        db.add(OrgSsoConfig(org_id=org.id, enabled=True, tenant_id="tenant", client_id="client",
                            client_secret_enc=encrypt_secret("secret"), default_role_id=user.roles[0].id))
        user.org_id, user.entra_oid = org.id, "policy-oid"
        db.commit()
        monkeypatch.setattr(sso, "exchange_code", AsyncMock(return_value={"id_token": "token"}))
        monkeypatch.setattr(sso, "validate_id_token", AsyncMock(return_value={"oid": "policy-oid", "tid": "tenant"}))
        monkeypatch.setattr(sso, "get_groups", AsyncMock(return_value=[]))
        client.cookies.set("sso_flow", sso._flow_signer.dumps({
            "s": "state", "slug": org.slug, "v": "verifier", "n": "nonce",
        }), domain="testserver.local", path="/")
        response = client.get("/sso/policy-sso/callback?state=state&code=code", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert _signer.loads(client.cookies.get("session"))["lm"] == method
