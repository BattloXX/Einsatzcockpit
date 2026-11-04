"""P2: Org-MFA fuer persoenliche Benutzer, isoliert von globalen Testdaten."""
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.crypto import decrypt_secret
from app.core.security import hash_api_key, sign_session
from app.models.admin_security import AdminTotp
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.mcp import MCPOAuthCode
from app.models.user import AuditLog, Role, UserRole
from app.services import mfa_service as service
from tests.test_admin_mfa_gate import csrf, set_session
from tests.test_admin_totp import enroll, mfa_env  # noqa: F401 -- isolierte DB-Fixture


@pytest.fixture
def user_env(mfa_env):
    db, system_admin, user, client = mfa_env
    service.clear_policy_cache()
    org = FireDept(id=982001, slug="p2-org", name="P2 Org")
    other = FireDept(id=982002, slug="p2-other", name="Andere Org")
    role = Role(code="readonly", label="Lesen")
    admin = Role(code="org_admin", label="Org-Admin")
    db.add_all([org, other, role, admin])
    db.flush()
    user.org_id = org.id
    db.add(UserRole(user_id=user.id, role_id=role.id))
    config = OrgSettings(org_id=org.id, mfa_policy="aus")
    db.add(config)
    db.commit()
    set_session(client, sign_session(user.id))
    yield db, user, client, config, admin
    service.clear_policy_cache()


def policy(db, config, value):
    config.mfa_policy = value
    db.commit()
    service.clear_policy_cache()


def fresh_code(db, user):
    row = db.get(AdminTotp, user.id)
    row.last_used_step = None
    db.commit()
    return service.totp(decrypt_secret(row.secret_enc), int(time.time()) // 30)


@pytest.mark.parametrize("value", ["aus", "optional"])
def test_policy_without_requirement(user_env, value):
    db, user, client, config, _ = user_env
    policy(db, config, value)
    assert client.get("/profil", follow_redirects=False).status_code == 200
    assert service.required_for_cached(user) is False


def test_policy_all_gate_next_and_totp(user_env):
    db, user, client, config, _ = user_env
    policy(db, config, "alle")
    response = client.get("/profil?example=a%26b", follow_redirects=False)
    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert target.path == "/mfa/verify"
    assert parse_qs(target.query)["next"] == ["/profil?example=a%26b"]
    page = client.get(response.headers["location"])
    assert "Zur Einrichtung" in page.text and "next=" in page.text
    enroll(db, user)
    response = client.post("/mfa/verify", data={"code": fresh_code(db, user), "next": "/profil?ok=1"},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/profil?ok=1"
    assert client.get("/profil").status_code == 200


@pytest.mark.parametrize("headers", [{"Accept": "application/json"}, {"HX-Request": "true"}])
def test_gate_json_htmx(user_env, headers):
    db, _, client, config, _ = user_env
    policy(db, config, "alle")
    response = client.get("/profil", headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "mfa_required"
    assert response.headers["HX-Redirect"] == "/mfa/verify"


@pytest.mark.parametrize("target", ["https://evil", "//evil", "/\\evil", "/profil\nLocation: evil"])
def test_verify_rejects_open_redirect(user_env, target):
    db, user, client, _, _ = user_env
    _, codes = enroll(db, user)
    response = client.post("/mfa/verify", data={"code": codes[0], "next": target},
                           headers=csrf(client), follow_redirects=False)
    assert response.headers["location"] == "/"


def test_policy_admin_roles(user_env):
    db, user, client, config, admin = user_env
    policy(db, config, "admins")
    assert client.get("/profil", follow_redirects=False).status_code == 200
    db.add(UserRole(user_id=user.id, role_id=admin.id))
    db.commit()
    assert client.get("/profil", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("exempt", [True, False])
def test_sso_exception(user_env, exempt):
    db, user, client, config, _ = user_env
    user.entra_oid = "p2-oid"
    config.mfa_sso_ausgenommen = exempt
    policy(db, config, "alle")
    assert client.get("/profil", follow_redirects=False).status_code == (200 if exempt else 303)


def test_personal_setup_and_admin_guards(user_env):
    db, user, client, _, _ = user_env
    page = client.get("/mfa/einrichten?next=/profil")
    assert "Manueller Schluessel" in page.text and "10 Recovery-Codes" in page.text
    code = fresh_code(db, user)
    response = client.post("/mfa/bestaetigen", data={"code": code, "next": "/profil"},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/profil"
    assert "System-Einstellungen" not in response.text
    assert client.post("/mfa/global", headers=csrf(client)).status_code == 403
    assert client.post("/mfa/zuruecksetzen", headers=csrf(client)).status_code == 403


@pytest.mark.parametrize("configured", [True, False])
def test_org_mcp_login(user_env, configured):
    db, user, client, config, _ = user_env
    policy(db, config, "alle")
    config.mcp_modul_aktiv = True
    db.add(SystemSettings(key="mcp_module_enabled", value="true"))
    db.add(MCPOAuthCode(code_hash=hash_api_key("p2-vorgang"), client_id="p2-client",
                       redirect_uri="https://example.org/callback", code_challenge="challenge", scopes="mcp",
                       expires_at=service.utcnow() + timedelta(minutes=5)))
    if configured:
        enroll(db, user)
    db.commit()
    client.cookies.delete("session", domain="testserver.local", path="/")
    data = {"vorgang": "p2-vorgang", "username": user.username, "password": "password"}
    response = client.post("/mcp/anmelden", data=data, headers=csrf(client), follow_redirects=False)
    assert response.status_code == (401 if configured else 403)
    if not configured:
        assert "Bitte zuerst im Einsatzcockpit" in response.text
        return
    db.expire_all()
    assert db.get(AdminTotp, user.id).failed_count == 1
    data["code"] = fresh_code(db, user)
    assert client.post("/mcp/anmelden", data=data, headers=csrf(client), follow_redirects=False).status_code == 302


def test_profile_recovery_and_removal(user_env):
    db, user, client, config, _ = user_env
    _, codes = enroll(db, user)
    assert "eingerichtet seit" in client.get("/profil").text
    for code in ("ungueltig", codes[0]):
        assert client.post("/profil/mfa/recovery-codes", data={"code": code}, headers=csrf(client)).status_code == 403
    response = client.post("/profil/mfa/recovery-codes", data={"code": fresh_code(db, user)}, headers=csrf(client))
    assert "10 Recovery-Codes" in response.text and response.headers["cache-control"] == "no-store"
    assert "10 Recovery-Codes" not in client.get("/profil").text
    assert not service.verify(db, user.id, codes[0])
    policy(db, config, "alle")
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    response = client.post("/profil/mfa/entfernen", data={"code": fresh_code(db, user)}, headers=csrf(client))
    assert response.status_code == 400 and "vorgeschrieben" in response.text
    policy(db, config, "aus")
    old_session = client.cookies.get("session")
    response = client.post("/profil/mfa/entfernen", data={"code": fresh_code(db, user)},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert not client.cookies.get("session")
    db.expire_all()
    assert db.get(AdminTotp, user.id) is None and user.session_epoch == 1
    set_session(client, old_session)
    assert client.get("/mfa/einrichten", follow_redirects=False).status_code in (401, 302)


def test_org_security_authorization_audit_cache_and_warning(user_env):
    db, user, client, config, admin = user_env
    assert client.get("/admin/sicherheit").status_code == 403
    assert client.post("/admin/sicherheit", data={"mfa_policy": "alle"}, headers=csrf(client)).status_code == 403
    db.add(UserRole(user_id=user.id, role_id=admin.id))
    db.commit()
    assert client.get("/admin/sicherheit").status_code == 200
    assert service.required_for(db, user) is False
    response = client.post("/admin/sicherheit", data={"mfa_policy": "admins", "mfa_sso_ausgenommen": "true"},
                           headers=csrf(client))
    assert response.status_code == 200 and "beim naechsten Seitenaufruf" in response.text
    db.expire_all()
    assert config.mfa_policy == "admins" and config.mfa_sms_erlaubt is False
    event = db.query(AuditLog).filter(AuditLog.action == "admin.mfa.policy_changed").one()
    assert event.org_id == user.org_id and event.user_id == user.id
    assert json.loads(event.payload_json)["alt"]["mfa_policy"] == "aus"
    assert json.loads(event.payload_json)["neu"]["mfa_policy"] == "admins"
    assert client.get("/profil", follow_redirects=False).status_code == 303
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    response = client.post("/admin/sicherheit", data={"org_id": 982002, "mfa_policy": "alle"}, headers=csrf(client))
    assert response.status_code == 403
    assert db.query(OrgSettings).filter(OrgSettings.org_id == 982002).first() is None
    assert client.get("/admin/sicherheit?org_id=982002").status_code == 403


def test_websocket_org_gate(user_env, monkeypatch):
    import app.main as main
    from app.routers import ws
    db, user, _, config, _ = user_env
    monkeypatch.setattr(ws, "SessionLocal", main.SessionLocal)
    policy(db, config, "alle")
    socket = SimpleNamespace(cookies={"session": sign_session(user.id)})
    assert ws._resolve_user(socket) is None
    socket.cookies["session"] = sign_session(user.id, mfa_at=int(time.time()))
    assert ws._resolve_user(socket).id == user.id


def test_gate_uses_cache_and_no_lookup_with_marker(user_env, monkeypatch):
    db, user, client, config, _ = user_env
    policy(db, config, "alle")
    assert service.required_for(db, user)
    def unexpected(*args):
        pytest.fail("Cache-Hit und MFA-Marker brauchen keinen DB-Policy-Lookup")
    monkeypatch.setattr(service, "required_for", unexpected)
    assert client.get("/mfa/verify").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 303
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    assert client.get("/", follow_redirects=False).headers.get("location", "").startswith("/mfa/") is False


@pytest.mark.parametrize("kind", ["device", "qr", "lage", "guest"])
def test_device_qr_lage_and_guest_sessions_are_exempt(user_env, kind):
    from app.models.incident import Incident, IncidentToken
    from app.models.major_incident import LageToken, MajorIncident
    from app.models.user import DeviceToken, Role
    db, user, client, config, _ = user_env
    policy(db, config, "alle")
    if kind == "device":
        user.is_device = True
        row = DeviceToken(user_id=user.id, label="P2 Tablet", token_hash=hash_api_key("p2-device"))
        db.add(row)
        db.flush()
        token = sign_session(user.id, device=True, device_token_id=row.id)
    elif kind in ("qr", "lage"):
        db.add(Role(code="recorder", label="Bearbeiten"))
        if kind == "qr":
            incident = Incident(primary_org_id=user.org_id)
            db.add(incident)
            db.flush()
            db.add(IncidentToken(incident_id=incident.id, issued_by_user_id=user.id,
                                 token_hash=hash_api_key("p2-qr")))
            token = sign_session(user.id, qr=True, incident_id=incident.id)
        else:
            lage = MajorIncident(org_id=user.org_id, name="P2 Lage")
            db.add(lage)
            db.flush()
            db.add(LageToken(lage_id=lage.id, issued_by_user_id=user.id, token_hash=hash_api_key("p2-lage")))
            token = sign_session(user.id, qr=True, lage_id=lage.id)
    else:
        user.username = "alarmlink-gast@org982001"
        token = sign_session(user.id)
    db.commit()
    set_session(client, token)
    response = client.get("/profil", follow_redirects=False)
    assert response.status_code == 200
    assert "Sicherheit</h2>" not in response.text
    response = client.get("/mfa/einrichten", follow_redirects=False)
    assert response.status_code == 403


def test_security_systemadmin_select_and_create(user_env):
    from app.models.user import User
    db, _, client, _, _ = user_env
    actor = db.query(User).filter(User.username == "mfa-admin").one()
    set_session(client, sign_session(actor.id))
    assert client.get("/admin/sicherheit", follow_redirects=False).status_code == 303
    assert "Andere Org" in client.get("/admin/sicherheit?org_id=982002").text
    response = client.post("/admin/sicherheit", data={"org_id": 982002, "mfa_policy": "optional",
                                                     "mfa_sms_erlaubt": "true"}, headers=csrf(client))
    assert response.status_code == 200
    row = db.query(OrgSettings).filter(OrgSettings.org_id == 982002).one()
    assert row.mfa_policy == "optional" and row.mfa_sms_erlaubt and not row.mfa_sso_ausgenommen


@pytest.mark.parametrize("path", ["/profil/mfa/entfernen", "/profil/mfa/recovery-codes", "/admin/sicherheit"])
def test_new_mutations_require_csrf(user_env, path):
    _, _, client, _, _ = user_env
    assert client.post(path, data={"mfa_policy": "alle", "code": "123456"}).status_code == 403


@pytest.mark.parametrize("next_url,expected", [("/profil?setup=1", "/profil?setup=1"), ("https://evil", "/"), ("", "/")])
def test_confirm_redirect_and_recovery_codes_survive(user_env, next_url, expected):
    import re
    db, user, client, _, _ = user_env
    page = client.get("/mfa/einrichten")
    codes = re.findall(r"<li><code>([A-F0-9-]+)</code></li>", page.text)
    assert len(codes) == 10
    response = client.post("/mfa/bestaetigen", data={"code": fresh_code(db, user), "next": next_url},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == expected
    assert not any(code in client.get("/mfa/einrichten").text for code in codes)
    assert service.verify(db, user.id, codes[0])
    db.commit()


def test_profile_lock_status_and_recovery_removal(user_env):
    db, user, client, _, _ = user_env
    _, codes = enroll(db, user)
    factor = db.get(AdminTotp, user.id)
    factor.locked_until = service.utcnow() + timedelta(minutes=5)
    db.commit()
    assert "Gesperrt bis" in client.get("/profil").text
    factor.locked_until = None
    db.commit()
    response = client.post("/profil/mfa/entfernen", data={"code": codes[0]},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303 and not client.cookies.get("session")


def test_systemadmin_profile_removal_follows_global_switch(user_env):
    from app.models.user import User
    db, _, client, _, _ = user_env
    actor = db.query(User).filter(User.username == "mfa-admin").one()
    enroll(db, actor)
    set_session(client, sign_session(actor.id, mfa_at=int(time.time())))
    assert "2FA entfernen" in client.get("/profil").text
    service.set_enabled(db, True, actor.id, None)
    db.commit()
    assert "2FA entfernen" not in client.get("/profil").text
    assert client.post("/profil/mfa/entfernen", data={"code": fresh_code(db, actor)},
                       headers=csrf(client)).status_code == 400


def test_security_preview_counts_only_personal_unconfigured_active_users(user_env):
    import re
    from app.models.user import User
    db, user, client, config, admin = user_env
    db.add(UserRole(user_id=user.id, role_id=admin.id))
    for name, active, device in (("inactive", False, False), ("tablet", True, True)):
        db.add(User(username="p2-" + name, display_name=name, org_id=user.org_id, active=active, is_device=device))
    db.add(User(username="alarmlink-gast@org982001", display_name="Gast", org_id=user.org_id))
    db.commit()
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    policy(db, config, "alle")
    page = client.get("/admin/sicherheit")
    assert re.search(r'id="mfa-missing">1</strong>', page.text)
    enroll(db, user)
    page = client.get("/admin/sicherheit")
    assert re.search(r'id="mfa-missing">0</strong>', page.text)


def test_gate_opens_policy_session_only_on_cache_miss(user_env, monkeypatch):
    import app.main as main
    db, user, client, config, _ = user_env
    policy(db, config, "alle")
    factory = main.SessionLocal
    opened = []
    def counted_session():
        opened.append(True)
        return factory()
    monkeypatch.setattr(main, "SessionLocal", counted_session)
    assert client.get("/_p2_not_route", follow_redirects=False).status_code == 303
    miss = len(opened)
    opened.clear()
    assert client.get("/_p2_not_route", follow_redirects=False).status_code == 303
    hit = len(opened)
    assert miss == hit + 1
    opened.clear()
    set_session(client, sign_session(user.id, mfa_at=int(time.time())))
    assert client.get("/_p2_not_route", follow_redirects=False).status_code == 404
    assert len(opened) == hit
