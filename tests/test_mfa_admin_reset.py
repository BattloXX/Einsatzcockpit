"""P3: Admin-Reset, Berechtigungen, Session-Widerruf und Neueinrichtungsfrist."""
import json
import re
import time
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.core.crypto import decrypt_secret
from app.core.security import sign_session
from app.models.admin_security import AdminRecoveryCode, AdminTotp
from app.models.master import FireDept, OrgSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services import mfa_service as service
from tests.test_admin_mfa_gate import csrf, set_session
from tests.test_admin_totp import enroll, mfa_env  # noqa: F401 -- isolierte DB-Fixture


@pytest.fixture
def reset_env(mfa_env, monkeypatch):
    db, system, target, client = mfa_env
    service.clear_policy_cache()
    org = FireDept(id=983101, slug="p3-a", name="P3 A")
    other = FireDept(id=983102, slug="p3-b", name="P3 B")
    role = Role(code="org_admin", label="Org-Admin")
    read = Role(code="readonly", label="Lesen")
    db.add_all([org, other, role, read])
    db.flush()
    config = OrgSettings(org_id=org.id, mfa_policy="aus")
    actor = User(username="p3-actor", display_name="Admin A", password_hash=target.password_hash, org_id=org.id)
    foreign = User(username="p3-foreign", display_name="User B", password_hash=target.password_hash, org_id=other.id)
    target.org_id = system.org_id = org.id
    db.add_all([actor, foreign, config])
    db.flush()
    db.add_all([UserRole(user_id=actor.id, role_id=role.id), UserRole(user_id=target.id, role_id=read.id)])
    db.commit()
    enroll(db, target)
    enroll(db, foreign)
    mails = []
    monkeypatch.setattr(service, "notify_reset", lambda session, user: mails.append((user.id, user.mfa_reset_at)))
    set_session(client, sign_session(actor.id))
    yield db, actor, target, foreign, system, client, config, mails
    service.clear_policy_cache()


def post_reset(env, target=None, **data):
    _, _, default, _, _, client, _, _ = env
    return client.post(f"/admin/benutzer/{(target or default).id}/mfa-zuruecksetzen",
                       data=data, headers=csrf(client), follow_redirects=False)


def test_org_admin_resets_user_audit_mail_and_epoch(reset_env):
    db, actor, target, _, _, _, _, mails = reset_env
    epoch = target.session_epoch
    response = post_reset(reset_env, grund="  Telefon verloren  ")
    assert response.headers["location"] == "/admin/benutzer?saved=mfa_reset"
    db.expire_all()
    assert db.get(AdminTotp, target.id) is None
    assert db.query(AdminRecoveryCode).filter_by(user_id=target.id).count() == 0
    assert target.session_epoch == epoch + 1 and target.mfa_reset_at is not None
    audit = db.query(AuditLog).filter_by(action="admin.mfa.reset", entity_id=target.id).all()
    entry = next(row for row in audit if "grund" in (row.payload_json or ""))
    assert entry.user_id == actor.id and entry.org_id == target.org_id and entry.entity_type == "user"
    assert json.loads(entry.payload_json) == {"grund": "Telefon verloren", "akteur_rolle": "org_admin"}
    assert mails == [(target.id, target.mfa_reset_at)]


def test_org_admin_resets_other_org_admin(reset_env):
    db, actor, target, *_ = reset_env
    db.add(UserRole(user_id=target.id, role_id=actor.roles[0].id))
    db.commit()
    assert post_reset(reset_env, grund="Telefon verloren").headers["location"].endswith("saved=mfa_reset")


def test_org_admin_cannot_reset_system_admin_audited(reset_env):
    db, actor, _, _, system, *_ = reset_env
    enroll(db, system)
    response = post_reset(reset_env, system, grund="Telefon verloren")
    assert response.headers["location"].endswith("error=mfa_verweigert")
    db.expire_all()
    assert db.get(AdminTotp, system.id) is not None
    denied = db.query(AuditLog).filter_by(action="admin.user.mutation_denied").one()
    assert denied.user_id == actor.id and json.loads(denied.payload_json)["reason"] == "target_system_admin"
    with pytest.raises(HTTPException) as error:
        service.admin_reset(db, actor, system, "Telefon verloren", None)
    assert error.value.status_code == 403


def test_org_admin_cannot_reset_foreign_org(reset_env):
    db, actor, _, foreign, *_ = reset_env
    response = post_reset(reset_env, foreign, grund="Telefon verloren")
    assert response.status_code == 404 or "error=" in response.headers["location"]
    db.expire_all()
    assert db.get(AdminTotp, foreign.id) is not None
    with pytest.raises(HTTPException) as error:
        service.admin_reset(db, actor, foreign, "Telefon verloren", None)
    assert error.value.status_code == 403


def test_self_reset_denied(reset_env):
    db, actor, *_ = reset_env
    enroll(db, actor)
    response = post_reset(reset_env, actor, grund="Telefon verloren")
    assert response.headers["location"].endswith("error=mfa_verweigert")
    assert db.get(AdminTotp, actor.id) is not None


@pytest.mark.parametrize("marker", [None, -301, 60])
def test_actor_factor_requires_fresh_step_up(reset_env, marker):
    db, actor, target, _, _, client, *_ = reset_env
    enroll(db, actor)
    set_session(client, sign_session(actor.id, mfa_at=None if marker is None else int(time.time()) + marker))
    response = post_reset(reset_env, grund="Telefon verloren")
    assert response.headers["location"] == "/mfa/verify?next=/admin/benutzer"
    assert db.get(AdminTotp, target.id) is not None


def test_required_actor_without_factor_needs_step_up(reset_env):
    db, actor, target, _, _, _, config, _ = reset_env
    config.mfa_policy = "admins"
    db.commit()
    service.clear_policy_cache()
    with pytest.raises(HTTPException, match="Zuerst 2FA") as error:
        service.admin_reset(db, actor, target, "Telefon verloren", None)
    assert error.value.status_code == 403 and db.get(AdminTotp, target.id) is not None


@pytest.mark.parametrize("grund", [None, "", "ab", "  ab  ", "x" * 501])
def test_invalid_reason_keeps_factor(reset_env, grund):
    db, _, target, *_ = reset_env
    response = post_reset(reset_env, **({} if grund is None else {"grund": grund}))
    assert response.headers["location"].endswith("error=mfa_grund")
    assert db.get(AdminTotp, target.id) is not None


@pytest.mark.parametrize("system_target", [False, True])
def test_system_admin_can_reset_any_org_and_other_system_admin(reset_env, system_target):
    db, actor, _, foreign, system, client, *_ = reset_env
    if system_target:
        db.add(UserRole(user_id=foreign.id, role_id=system.roles[0].id))
        db.commit()
    set_session(client, sign_session(system.id))
    assert post_reset(reset_env, foreign, grund="Telefon verloren").headers["location"].endswith("saved=mfa_reset")
    db.expire_all()
    assert db.get(AdminTotp, foreign.id) is None


@pytest.mark.parametrize("action", ["mfa-zuruecksetzen", "mfa-entsperren"])
def test_non_admin_denied(reset_env, action):
    db, _, target, foreign, _, client, *_ = reset_env
    set_session(client, sign_session(target.id))
    response = client.post(f"/admin/benutzer/{foreign.id}/{action}", data={"grund": "Telefon verloren"},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 403 and db.get(AdminTotp, foreign.id) is not None


def test_target_old_session_revoked(reset_env):
    _, _, target, _, _, client, *_ = reset_env
    old = sign_session(target.id, mfa_at=int(time.time()))
    post_reset(reset_env, grund="Telefon verloren")
    set_session(client, old)
    assert client.get("/mfa/einrichten", follow_redirects=False).status_code in (401, 302)


def test_unlock_clears_lock_without_reset(reset_env):
    db, actor, target, _, _, client, _, mails = reset_env
    row = db.get(AdminTotp, target.id)
    row.failed_count = 5
    row.locked_until = service.utcnow() + timedelta(minutes=5)
    db.commit()
    response = client.post(f"/admin/benutzer/{target.id}/mfa-entsperren", headers=csrf(client), follow_redirects=False)
    assert response.headers["location"].endswith("saved=mfa_unlock")
    db.expire_all()
    assert row.failed_count == 0 and row.locked_until is None and row.confirmed_at is not None
    assert target.session_epoch == 0 and not mails
    entry = db.query(AuditLog).filter_by(action="admin.mfa.unlock").one()
    assert entry.user_id == actor.id and entry.entity_id == target.id and entry.org_id == target.org_id


@pytest.mark.parametrize("kind", ["foreign", "system", "self", "step_up", "missing"])
def test_unlock_same_guards(reset_env, kind):
    db, actor, target, foreign, system, *_ = reset_env
    chosen = {"foreign": foreign, "system": system, "self": actor}.get(kind, target)
    if kind == "step_up":
        enroll(db, actor)
    if kind == "missing":
        service.reset(db, target, actor.id)
        db.commit()
    with pytest.raises(HTTPException) as error:
        service.admin_unlock(db, actor, chosen, None)
    assert error.value.status_code == (400 if kind == "missing" else 403)


@pytest.mark.parametrize("hours,required,expired", [(23, True, False), (25, True, True), (25, False, False)])
def test_reset_setup_deadline_and_confirmation(reset_env, hours, required, expired):
    db, actor, target, _, _, client, config, _ = reset_env
    service.admin_reset(db, actor, target, "Telefon verloren", None)
    target.mfa_reset_at = service.utcnow() - timedelta(hours=hours)
    config.mfa_policy = "alle" if required else "aus"
    db.commit()
    service.clear_policy_cache()
    set_session(client, sign_session(target.id, session_epoch=target.session_epoch))
    response = client.get("/mfa/einrichten")
    if expired:
        assert response.status_code == 403 and "Die Frist zur Neueinrichtung ist abgelaufen" in response.text
        assert db.get(AdminTotp, target.id) is None
        # Auch bereits begonnene Einrichtungen duerfen nach Ablauf nicht bestaetigt werden.
        row = service.start_setup(db, target)
        db.commit()
    else:
        assert response.status_code == 200 and "Manueller Schluessel" in response.text
        db.expire_all()
        row = db.get(AdminTotp, target.id)
    code = service.totp(decrypt_secret(row.secret_enc), int(time.time()) // 30)
    response = client.post("/mfa/bestaetigen", data={"code": code}, headers=csrf(client), follow_redirects=False)
    db.expire_all()
    if expired:
        assert response.status_code == 403 and "Die Frist zur Neueinrichtung ist abgelaufen" in response.text
        assert row.confirmed_at is None and target.mfa_reset_at is not None
    else:
        assert response.status_code == 303 and target.mfa_reset_at is None and row.confirmed_at is not None


def test_first_setup_has_no_deadline(reset_env):
    db, _, target, _, _, client, config, _ = reset_env
    service.reset(db, target, None)
    config.mfa_policy = "alle"
    db.commit()
    service.clear_policy_cache()
    set_session(client, sign_session(target.id, session_epoch=target.session_epoch))
    assert client.get("/mfa/einrichten").status_code == 200


def test_new_reset_renews_deadline(reset_env):
    db, actor, target, *_ = reset_env
    target.mfa_reset_at = service.utcnow() - timedelta(hours=25)
    service.admin_reset(db, actor, target, "Nochmals verloren", None)
    assert target.mfa_reset_at > service.utcnow() - timedelta(seconds=5)


def test_user_list_badges_and_protected_actions(reset_env):
    db, actor, target, _, system, client, *_ = reset_env
    enroll(db, actor)
    enroll(db, system)
    row = db.get(AdminTotp, target.id)
    row.locked_until = service.utcnow() + timedelta(minutes=5)
    db.commit()
    page = client.get("/admin/benutzer")
    assert page.status_code == 200 and 'data-label="2FA"' in page.text and "Gesperrt bis" in page.text
    assert f'data-mfa-user-id="{target.id}"' in page.text and "Sperre aufheben" in page.text
    assert f'data-mfa-user-id="{actor.id}"' not in page.text
    assert f'data-mfa-user-id="{system.id}"' not in page.text
    assert "TOTP" in page.text and "Nur Systemadministrator" in page.text
    service.admin_reset(db, actor, target, "Telefon verloren", int(time.time()))
    db.commit()
    page = client.get("/admin/benutzer")
    assert "Neueinrichtung offen" in page.text and f'data-mfa-user-id="{target.id}"' in page.text
    assert re.search(r'<textarea[^>]*name="grund"[^>]*required', page.text)


@pytest.mark.parametrize("action", ["mfa-zuruecksetzen", "mfa-entsperren"])
def test_admin_mfa_csrf_required(reset_env, action):
    db, _, target, _, _, client, *_ = reset_env
    response = client.post(f"/admin/benutzer/{target.id}/{action}", data={"grund": "Telefon verloren"})
    assert response.status_code == 403 and db.get(AdminTotp, target.id) is not None


def test_legacy_system_route_uses_admin_reset(reset_env):
    db, _, _, foreign, system, client, _, mails = reset_env
    set_session(client, sign_session(system.id))
    response = client.post("/mfa/zuruecksetzen", data={"user_id": foreign.id},
                           headers=csrf(client), follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    assert db.get(AdminTotp, foreign.id) is None and foreign.mfa_reset_at is not None
    assert mails == [(foreign.id, foreign.mfa_reset_at)]
    assert any(json.loads(a.payload_json).get("grund") == "Systemadministration"
               for a in db.query(AuditLog).filter_by(action="admin.mfa.reset"))


def test_device_target_not_found(reset_env):
    db, actor, target, *_ = reset_env
    target.is_device = True
    db.commit()
    with pytest.raises(HTTPException) as error:
        service.admin_reset(db, actor, target, "Telefon verloren", None)
    assert error.value.status_code == 404 and db.get(AdminTotp, target.id) is not None


def test_status_contract(reset_env):
    db, _, target, foreign, *_ = reset_env
    row = db.get(AdminTotp, target.id)
    assert service.status(db, target) == {"aktiv": True, "methode": "totp", "eingerichtet_at": row.confirmed_at,
                                        "gesperrt_bis": None, "reset_offen": False}
    service.reset(db, foreign, None)
    db.flush()
    assert service.status(db, foreign) == {"aktiv": False, "methode": None, "eingerichtet_at": None,
                                         "gesperrt_bis": None, "reset_offen": False}


def test_fresh_actor_factor_allows_reset(reset_env):
    db, actor, target, _, _, client, *_ = reset_env
    enroll(db, actor)
    set_session(client, sign_session(actor.id, mfa_at=int(time.time())))
    assert post_reset(reset_env, grund="Telefon verloren").headers["location"].endswith("saved=mfa_reset")
    db.expire_all()
    assert db.get(AdminTotp, target.id) is None


def test_system_admin_list_all_orgs_and_system_actions(reset_env):
    db, actor, target, foreign, system, client, *_ = reset_env
    db.add(UserRole(user_id=actor.id, role_id=system.roles[0].id))
    enroll(db, actor)
    enroll(db, system)
    set_session(client, sign_session(system.id, mfa_at=int(time.time())))
    page = client.get("/admin/benutzer")
    assert page.status_code == 200
    for user in (actor, target, foreign):
        assert f'data-mfa-user-id="{user.id}"' in page.text
    assert f'data-mfa-user-id="{system.id}"' not in page.text


def test_pending_factor_can_be_reset_and_status_queries_batched(reset_env):
    from sqlalchemy import event
    db, _, target, *_ = reset_env
    from app.routers.ui_admin import _users_mfa_status
    factor = db.get(AdminTotp, target.id)
    factor.confirmed_at = None
    db.commit()
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if "SELECT" in statement and "admin_totp" in statement:
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        users = db.query(User).execution_options(include_all_tenants=True).all()
        statuses = _users_mfa_status(db, users)
        assert len(statements) == 1
        assert statuses[target.id]["faktor_vorhanden"] and not statuses[target.id]["aktiv"]
    finally:
        event.remove(engine, "before_cursor_execute", record)
    client = reset_env[5]
    page = client.get("/admin/benutzer")
    assert f'data-mfa-user-id="{target.id}"' in page.text and ">Aus</span>" in page.text


def test_notify_reset_mail_has_deadline_and_no_secrets(mfa_env, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import mail_service
    db, _, target, _ = mfa_env
    secret, codes = enroll(db, target)
    target.email = "p3-user@example.org"
    db.commit()
    deliver = AsyncMock()
    monkeypatch.setattr(mail_service, "mail_available", lambda *args: True)
    monkeypatch.setattr(mail_service, "deliver", deliver)
    service.notify_reset(db, target)
    deliver.assert_awaited_once()
    message = deliver.call_args.args[2]
    parts = message.walk() if message.is_multipart() else [message]
    text = " ".join(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
                    for part in parts if part.get_content_type() == "text/plain")
    assert "Bitte richten Sie die Zwei-Faktor-Authentifizierung innerhalb von 24 Stunden neu ein." in text
    assert secret not in text and not any(code in text for code in codes)
