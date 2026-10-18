"""M5: Admin-Vorschau für Benutzer-Mails."""
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.password_reset import PasswordResetToken
from app.models.user import Role, User, UserRole
from app.services.benutzer_mail_service import MAIL_VORSCHAU_TYPEN


def _user(*, admin: bool, email: bool = True):
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex
    org = FireDept(slug=f"preview-org-{suffix}", name=f"Vorschau Organisation {suffix}")
    user = User(
        username=f"preview-user-{suffix}", display_name=f"Vorschau Benutzer {suffix}",
        email=f"preview-{suffix}@example.test" if email else None,
        password_hash=hash_password("Test1234!"), org=org,
    )
    db.add_all([org, user])
    db.flush()
    if admin:
        role = db.query(Role).filter(Role.code == "admin").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    result = (user.id, user.username, user.email)
    db.close()
    return result


def _login(client, username):
    client.get("/login")
    client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)


@pytest.mark.parametrize("typ", MAIL_VORSCHAU_TYPEN)
def test_admin_sieht_jeden_mail_typ_in_der_vorschau(client, typ):
    _, username, _ = _user(admin=True)
    _login(client, username)
    response = client.get(f"/admin/benutzer/mail-vorschau?typ={typ}")
    assert response.status_code == 200
    assert "HTML-Mail-Vorschau" in response.text


def test_nicht_admin_erhaelt_keine_mail_vorschau(client):
    _, username, _ = _user(admin=False)
    _login(client, username)
    assert client.get("/admin/benutzer/mail-vorschau", follow_redirects=False).status_code == 403


def test_mail_vorschau_iframe_ist_ohne_scripts_sandboxed(client):
    _, username, _ = _user(admin=True)
    _login(client, username)
    response = client.get("/admin/benutzer/mail-vorschau?typ=einladung")
    assert '<iframe title="HTML-Mail-Vorschau" sandbox ' in response.text
    assert "allow-scripts" not in response.text


def test_mail_vorschau_testversand_geht_an_eigene_adresse(client, monkeypatch):
    _, username, email = _user(admin=True)
    delivered = []

    async def fake_deliver(_db, _org_id, msg, _smtp_cfg):
        delivered.append(msg)

    monkeypatch.setattr("app.services.mail_service.deliver", fake_deliver)
    _login(client, username)
    response = client.post("/admin/benutzer/mail-vorschau/test", data={
        "typ": "passwort_reset", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code == 303 and "sent=1" in response.headers["location"]
    assert str(delivered[0]["To"]) == email
    assert str(delivered[0]["Subject"]).startswith("[Test] Passwort zurücksetzen")


def test_mail_vorschau_erzeugt_keine_echten_tokens(client):
    user_id, username, _ = _user(admin=True)
    db = SessionLocal()
    set_tenant_context(db, None)
    before = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).count()
    db.close()
    _login(client, username)
    for typ in MAIL_VORSCHAU_TYPEN:
        assert client.get(f"/admin/benutzer/mail-vorschau?typ={typ}").status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).count() == before
    finally:
        db.close()
