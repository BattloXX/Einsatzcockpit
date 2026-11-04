"""P4: SMS-Faktor, Zwecktrennung und alle interaktiven Sicherheitsgrenzen."""
import hashlib
import json
import re
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.core.crypto import decrypt_secret
from app.core.security import hash_api_key, session_security, sign_session
from app.models.admin_security import AdminRecoveryCode, AdminTotp, UserMfaSms
from app.models.login_pin import LoginPin
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.mcp import MCPOAuthCode
from app.models.user import AuditLog, Role, UserRole
from app.services import mail_service, sms_service
from app.services import mfa_service as mfa
from app.services import mfa_sms_service as sms
from app.services.sms_service import SmsDeliveryResult
from tests.test_admin_mfa_gate import csrf, set_session
from tests.test_admin_totp import enroll, mfa_env  # noqa: F401 -- isolierte DB-Fixture


@pytest.fixture
def env(request, monkeypatch):
    db, system, user, client = request.getfixturevalue("mfa_env")
    mfa.clear_policy_cache()
    org = FireDept(id=984501, slug="p4-sms", name="P4 SMS")
    other = FireDept(id=984502, slug="p4-other", name="P4 Andere")
    role = Role(code="readonly", label="Lesen")
    db.add_all([org, other, role])
    db.flush()
    config = OrgSettings(org_id=org.id, mfa_policy="alle", mfa_sms_erlaubt=True)
    user.org_id = system.org_id = org.id
    user.phone, user.email = "+436641111111", "p4@example.org"
    db.add_all([config, UserRole(user_id=user.id, role_id=role.id)])
    db.commit()
    messages, mails = [], []

    async def send(org_id, phone, text):
        messages.append((org_id, phone, text))
        return SmsDeliveryResult(success=True)

    async def deliver(session, org_id, message):
        mails.append(message)

    monkeypatch.setattr(sms, "sms_available", lambda org_id, db: True)
    monkeypatch.setattr(sms_service, "sms_available", lambda org_id, db: True)
    monkeypatch.setattr(sms, "send_sms", send)
    monkeypatch.setattr(sms_service, "send_sms", send)
    monkeypatch.setattr(mail_service, "mail_available", lambda *args: True)
    monkeypatch.setattr(mail_service, "deliver", deliver)
    set_session(client, sign_session(user.id, login_method="pw"))
    yield SimpleNamespace(db=db, system=system, user=user, client=client, config=config,
                          messages=messages, mails=mails, other=other)
    mfa.clear_policy_cache()


def post(e, path, **data):
    return e.client.post(path, data=data, headers=csrf(e.client), follow_redirects=False)


def code(e):
    return re.search(r"Sicherheitscode: ([0-9]{6})", e.messages[-1][2]).group(1)


def setup(e):
    sms.start_sms_setup(e.db, e.user, "0664 2222222")
    codes = sms.confirm_sms_setup(e.db, e.user, code(e))
    e.db.commit()
    assert codes is not None
    return codes


def send_login(e):
    sms.send_code(e.db, e.user, "mfa")
    e.db.commit()
    return code(e)


def test_setup_new_number_recovery_mail_and_profile_unchanged(env):
    e = env
    response = post(e, "/mfa/sms/einrichten", phone="0664 2222222", next="/profil")
    assert response.status_code == 200
    assert e.messages[-1][1] == "+436642222222"
    assert "http" not in e.messages[-1][2]
    response = post(e, "/mfa/sms/bestaetigen", code=code(e), next="/profil")
    assert response.status_code == 200 and "Recovery-Codes" in response.text
    e.db.expire_all()
    factor = e.db.get(UserMfaSms, e.user.id)
    assert factor.verified_at and decrypt_secret(factor.phone_enc) == "+436642222222"
    assert e.user.phone == "+436641111111" and e.user.mfa_reset_at is None
    assert e.db.query(AdminRecoveryCode).filter_by(user_id=e.user.id, used_at=None).count() == 10
    assert len(e.mails) == 1 and e.mails[0]["Subject"] == "SMS als zweiter Faktor eingerichtet"
    assert "+436642222222" not in e.mails[0].get_content()
    audit = e.db.query(AuditLog).filter_by(action="admin.mfa.sms_sent").one()
    assert code(e) not in (audit.payload_json or "")
    assert "+436642222222" not in (audit.payload_json or "")
    assert session_security(e.client.cookies.get("session"))[1] is not None
    assert e.client.get("/profil").status_code == 200
    assert "SMS-Nummer aendern" in e.client.get("/profil").text


@pytest.mark.parametrize("method", ["pw", "sso"])
def test_password_sso_verify_marker_next_and_single_use(env, method):
    e = env
    setup(e)
    set_session(e.client, sign_session(e.user.id, login_method=method))
    assert "Code per SMS senden" in e.client.get("/mfa/verify").text
    response = post(e, "/mfa/sms/senden", next="/profil?ok=1")
    assert response.status_code == 200 and 'value="/profil?ok=1"' in response.text
    current = code(e)
    response = post(e, "/mfa/sms/verify", code=current, next="/profil?ok=1")
    assert response.status_code == 303 and response.headers["location"] == "/profil?ok=1"
    assert session_security(e.client.cookies.get("session"))[1] is not None
    assert e.client.get("/profil").status_code == 200
    assert not sms.verify_sms(e.db, e.user, current, "mfa")
    e.db.commit()


def test_wrong_code_five_attempts_lock_and_expired_code(env):
    e = env
    setup(e)
    current = send_login(e)
    wrong = "000000" if current != "000000" else "111111"
    for i in range(5):
        assert "Code ungueltig" in post(e, "/mfa/sms/verify", code=wrong).text
        e.db.expire_all()
        assert e.db.get(UserMfaSms, e.user.id).failed_count == i + 1
    assert e.db.get(UserMfaSms, e.user.id).locked_until > mfa.utcnow()
    assert not sms.verify_sms(e.db, e.user, current, "mfa")
    e.db.commit()
    factor = e.db.get(UserMfaSms, e.user.id)
    factor.locked_until = mfa.utcnow() - timedelta(seconds=1)
    factor.failed_count = 0
    e.db.commit()
    current = send_login(e)
    pin = e.db.query(LoginPin).filter_by(user_id=e.user.id, purpose="mfa", used_at=None).one()
    pin.created_at = mfa.utcnow() - timedelta(minutes=6)
    pin.expires_at = pin.created_at + timedelta(minutes=5)
    e.db.commit()
    assert not sms.verify_sms(e.db, e.user, current, "mfa")
    e.db.commit()


@pytest.mark.parametrize("method", ["pin", "switch"])
def test_pin_switch_reject_sms_but_allow_recovery(env, method):
    e = env
    codes = setup(e)
    current = send_login(e)
    set_session(e.client, sign_session(e.user.id, login_method=method))
    page = e.client.get("/mfa/verify")
    assert "Code per SMS senden" not in page.text and "bitte mit Passwort anmelden" in page.text
    assert post(e, "/mfa/sms/senden").status_code == 403
    assert post(e, "/mfa/sms/verify", code=current).status_code == 403
    response = post(e, "/mfa/verify", code=codes[0], next="/profil")
    assert response.status_code == 303 and e.client.get("/profil").status_code == 200
    assert not mfa.verify(e.db, e.user.id, codes[0])


@pytest.mark.parametrize("window,count", [(10, 3), (1440, 10)])
def test_shared_setup_login_limits_no_sms(env, window, count):
    e = env
    setup(e)
    # Alte Setup-SMS bleibt in der Tageszaehlung, aber nicht im kurzen Fenster.
    pin = e.db.query(LoginPin).filter_by(user_id=e.user.id).one()
    pin.created_at = mfa.utcnow() - timedelta(hours=25)
    for i in range(count):
        e.db.add(LoginPin(user_id=e.user.id, purpose="mfa" if i % 2 else "mfa_setup",
                          pin_hash="0" * 64, created_at=mfa.utcnow() - timedelta(minutes=1 if window == 10 else 20+i),
                          expires_at=mfa.utcnow() + timedelta(minutes=5)))
    e.db.commit()
    sent = len(e.messages)
    with pytest.raises(ValueError, match="limit"):
        sms.send_code(e.db, e.user, "mfa")
    assert len(e.messages) == sent
    response = post(e, "/mfa/sms/senden")
    assert "Zu viele SMS angefordert" in response.text and len(e.messages) == sent


@pytest.mark.parametrize("kind", ["system", "policy", "device"])
def test_sms_forbidden_system_device_policy(env, kind):
    e = env
    if kind == "system":
        set_session(e.client, sign_session(e.system.id))
    elif kind == "policy":
        e.config.mfa_sms_erlaubt = False
    else:
        e.user.is_device = True
    e.db.commit()
    assert e.client.get("/mfa/sms/einrichten", follow_redirects=False).status_code == 403
    assert post(e, "/mfa/sms/einrichten", phone="0664 2222222").status_code == 403
    if kind == "policy":
        assert '>SMS</a>' not in e.client.get("/mfa/einrichten").text
    assert e.messages == []


def test_login_and_mfa_codes_are_mutually_exclusive(env):
    e = env
    setup(e)
    current = send_login(e)
    e.client.cookies.delete("session", domain="testserver.local", path="/")
    assert post(e, "/pin-login/code", phone=e.user.phone, pin=current).status_code == 401
    login_code = "654321" if current != "654321" else "123456"
    e.db.add(LoginPin(user_id=e.user.id, purpose="login", pin_hash=hashlib.sha256(login_code.encode()).hexdigest(),
                      expires_at=mfa.utcnow() + timedelta(minutes=10)))
    e.db.commit()
    set_session(e.client, sign_session(e.user.id))
    assert "Code ungueltig" in post(e, "/mfa/sms/verify", code=login_code).text
    e.db.expire_all()
    assert e.db.query(LoginPin).filter_by(user_id=e.user.id, purpose="login").one().used_at is None
    # Login-PIN-Versand entwertet keine MFA-Codes.
    e.client.cookies.delete("session", domain="testserver.local", path="/")
    assert post(e, "/pin-login", phone=e.user.phone).status_code == 303
    e.db.expire_all()
    assert e.db.query(LoginPin).filter_by(user_id=e.user.id, purpose="mfa", used_at=None).count() == 1
    assert post(e, "/pin-login/code", phone=e.user.phone, pin=current).status_code == 401


def test_number_change_requires_fresh_stepup_and_keeps_recovery(env):
    e = env
    codes = setup(e)
    old_code = send_login(e)
    for path in ("/mfa/sms/einrichten",):
        assert e.client.get(path).status_code == 403
        assert post(e, path, phone="0664 3333333").status_code == 403
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time()) - 301))
    assert post(e, "/mfa/sms/einrichten", phone="0664 3333333").status_code == 403
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time())))
    assert post(e, "/mfa/sms/einrichten", phone="0664 3333333").status_code == 200
    assert post(e, "/mfa/sms/bestaetigen", code=code(e), next="/profil").status_code == 303
    e.db.expire_all()
    assert decrypt_secret(e.db.get(UserMfaSms, e.user.id).phone_enc) == "+436643333333"
    assert not sms.verify_sms(e.db, e.user, old_code, "mfa")
    assert mfa.verify(e.db, e.user.id, codes[0])
    e.db.commit()


def test_profile_admin_phone_changes_do_not_change_factor(env):
    e = env
    setup(e)
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time())))
    assert post(e, "/profil", display_name="P4", phone="0664 9999999", email=e.user.email).status_code == 302
    e.db.expire_all()
    assert e.user.phone == "0664 9999999"
    set_session(e.client, sign_session(e.system.id))
    response = post(e, f"/admin/benutzer/{e.user.id}/edit", display_name="P4", phone="+436648888888",
                    email=e.user.email, role_codes=["readonly"])
    assert response.status_code == 303
    e.db.expire_all()
    assert e.user.phone == "+436648888888"
    send_login(e)
    assert e.messages[-1][1] == "+436642222222"


def test_admin_reset_sms_mail_delete_and_unlock(env):
    e = env
    setup(e)
    send_login(e)
    factor = e.db.get(UserMfaSms, e.user.id)
    factor.failed_count, factor.locked_until = 5, mfa.utcnow() + timedelta(minutes=5)
    e.db.commit()
    set_session(e.client, sign_session(e.system.id))
    assert post(e, f"/admin/benutzer/{e.user.id}/mfa-entsperren").status_code == 303
    e.db.expire_all()
    assert factor.failed_count == 0 and factor.locked_until is None
    response = post(e, f"/admin/benutzer/{e.user.id}/mfa-zuruecksetzen", grund="Telefon verloren")
    assert response.status_code == 303
    e.db.expire_all()
    assert e.db.get(UserMfaSms, e.user.id) is None
    assert e.db.query(LoginPin).filter_by(user_id=e.user.id, used_at=None).count() == 0
    assert e.messages[-1][1:] == ("+436642222222", "Ihre Zwei-Faktor-Authentifizierung wurde zurueckgesetzt.")
    assert "zurueckgesetzt" in e.mails[-1]["Subject"]
    assert e.user.mfa_reset_at is not None


def test_status_badge_reset_button_and_setup_expiry(env):
    e = env
    assert mfa.status(e.db, e.user)["methode"] is None
    setup(e)
    factor = e.db.get(UserMfaSms, e.user.id)
    assert mfa.status(e.db, e.user)["methode"] == "sms"
    set_session(e.client, sign_session(e.system.id))
    page = e.client.get("/admin/benutzer")
    assert '>SMS</span>' in page.text and f'data-mfa-user-id="{e.user.id}"' in page.text
    enroll(e.db, e.user)
    assert mfa.status(e.db, e.user)["methode"] == "totp+sms"
    totp = e.db.get(AdminTotp, e.user.id)
    totp.locked_until = mfa.utcnow() + timedelta(minutes=2)
    factor.locked_until = mfa.utcnow() + timedelta(minutes=4)
    assert mfa.status(e.db, e.user)["gesperrt_bis"] == factor.locked_until
    e.db.delete(factor)
    e.db.commit()
    assert mfa.status(e.db, e.user)["methode"] == "totp"
    e.db.delete(totp)
    e.db.commit()
    setup(e)
    e.user.mfa_reset_at = mfa.utcnow() - timedelta(hours=25)
    e.db.commit()
    assert not mfa.setup_expired(e.db, e.user)


@pytest.mark.parametrize("with_totp", [False, True])
def test_org_mcp_sms_login_and_totp_precedence(env, with_totp):
    e = env
    setup(e)
    if with_totp:
        enroll(e.db, e.user)
    e.config.mcp_modul_aktiv = True
    e.db.add(SystemSettings(key="mcp_module_enabled", value="true"))
    e.db.add(MCPOAuthCode(code_hash=hash_api_key("p4-vorgang"), client_id="p4-client",
                         redirect_uri="https://example.org/callback", code_challenge="challenge", scopes="mcp",
                         expires_at=mfa.utcnow() + timedelta(minutes=5)))
    e.db.commit()
    e.client.cookies.delete("session", domain="testserver.local", path="/")
    data = {"vorgang": "p4-vorgang", "username": e.user.username, "password": "password", "aktion": "sms"}
    before = len(e.messages)
    response = post(e, "/mcp/anmelden", **data)
    if with_totp:
        assert response.status_code == 403 and len(e.messages) == before
    else:
        assert response.status_code == 200 and len(e.messages) == before + 1
        data.update(aktion="", code=code(e))
        assert post(e, "/mcp/anmelden", **data).status_code == 302


@pytest.mark.parametrize("path", ["/mfa/sms/senden", "/mfa/sms/verify", "/mfa/sms/einrichten",
                                  "/mfa/sms/bestaetigen", "/mfa/sms/entfernen"])
def test_new_post_routes_require_csrf(env, path):
    response = env.client.post(path, data={"phone": "0664 2222222", "code": "123456"}, follow_redirects=False)
    assert response.status_code == 403


def test_gateway_unavailable_recovery_and_setup_rollback(env, monkeypatch):
    e = env
    codes = setup(e)
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time())))

    async def failed(*args):
        return SmsDeliveryResult(success=False)

    monkeypatch.setattr(sms, "send_sms", failed)
    response = post(e, "/mfa/sms/einrichten", phone="0664 3333333")
    assert "SMS-Gateway nicht verfuegbar" in response.text
    e.db.expire_all()
    assert decrypt_secret(e.db.get(UserMfaSms, e.user.id).phone_enc) == "+436642222222"
    monkeypatch.setattr(sms, "sms_available", lambda *args: False)
    assert "Recovery-Code oder TOTP" in e.client.get("/mfa/verify").text
    assert post(e, "/mfa/sms/senden").status_code == 403
    assert post(e, "/mfa/verify", code=codes[0]).status_code == 303


def test_sms_removal_requires_remaining_factor_and_stepup(env):
    e = env
    setup(e)
    assert post(e, "/mfa/sms/entfernen").status_code == 403
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time())))
    assert post(e, "/mfa/sms/entfernen").status_code == 400
    enroll(e.db, e.user)
    assert post(e, "/mfa/sms/entfernen").status_code == 303
    e.db.expire_all()
    assert e.db.get(UserMfaSms, e.user.id) is None and mfa.status(e.db, e.user)["methode"] == "totp"


def test_policy_counts_include_sms_and_audits_mask_number(env):
    e = env
    setup(e)
    set_session(e.client, sign_session(e.system.id))
    page = e.client.get(f"/admin/sicherheit?org_id={e.user.org_id}")
    assert page.status_code == 200
    assert page.context["counts"]["alle"] == 0
    for row in e.db.query(AuditLog).filter(AuditLog.action.like("admin.mfa.sms%")):
        payload = json.loads(row.payload_json or "{}")
        assert "+436642222222" not in str(payload)


def test_recovery_survives_pending_totp_and_sms_default_without_settings(env):
    e = env
    codes = setup(e)
    e.db.delete(e.config)
    e.db.commit()
    assert sms.sms_allowed(e.db, e.user)
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time())))
    page = e.client.get("/mfa/einrichten")
    assert page.status_code == 200 and "bestehenden Recovery-Codes" in page.text
    e.db.expire_all()
    assert e.db.get(AdminTotp, e.user.id).confirmed_at is None
    set_session(e.client, sign_session(e.user.id))
    assert post(e, "/mfa/verify", code=codes[0], next="/profil").status_code == 303
    e.db.expire_all()
    assert e.db.query(AdminRecoveryCode).filter_by(user_id=e.user.id, used_at=None).count() == 9


@pytest.mark.parametrize("new_method", ["sms", "totp"])
def test_adding_alternative_factor_requires_stepup(env, new_method):
    e = env
    if new_method == "sms":
        enroll(e.db, e.user)
        path = "/mfa/sms/einrichten"
        data = {"phone": "0664 2222222"}
    else:
        setup(e)
        path = "/mfa/einrichten"
        data = {"code": "123456"}
    assert e.client.get(path).status_code == 403
    assert post(e, "/mfa/sms/einrichten" if new_method == "sms" else "/mfa/bestaetigen", **data).status_code == 403
    set_session(e.client, sign_session(e.user.id, mfa_at=int(time.time())))
    assert e.client.get(path).status_code == 200
