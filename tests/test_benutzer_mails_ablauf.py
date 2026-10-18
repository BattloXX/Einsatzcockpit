"""M4: Passwort-Reset-Ablauf und Bestätigungs-Mails."""
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.security import hash_password, verify_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.password_reset import PasswordResetToken
from app.models.user import Role, User, UserRole
from app.services import benutzer_mail_service, benutzer_service


def _org_user(*, provider: str = "local", email: bool = True, timezone: str = "Europe/Vienna"):
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex
    org = FireDept(slug=f"m4-org-{suffix}", name=f"M4 Organisation {suffix}", timezone=timezone)
    user = User(
        username=f"m4-user-{suffix}", display_name=f"M4 Benutzer {suffix}",
        email=f"m4-{suffix}@example.test" if email else None,
        password_hash=hash_password("Test1234!"), org=org, auth_provider=provider,
    )
    db.add_all([org, user])
    db.commit()
    result = (user.id, org.id, user.username)
    db.close()
    return result


def _admin_and_user():
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex
    org = FireDept(slug=f"m4-admin-org-{suffix}", name=f"M4 Admin Organisation {suffix}")
    admin = User(username=f"m4-admin-{suffix}", display_name=f"M4 Admin {suffix}", password_hash=hash_password("Test1234!"), org=org)
    target = User(username=f"m4-target-{suffix}", display_name=f"M4 Ziel {suffix}", email=f"m4-target-{suffix}@example.test", password_hash=hash_password("Test1234!"), org=org)
    role = db.query(Role).filter(Role.code == "admin").one()
    db.add_all([org, admin, target])
    db.flush()
    db.add(UserRole(user_id=admin.id, role_id=role.id))
    db.commit()
    result = (admin.username, target.id)
    db.close()
    return result


def _login(client, username):
    client.get("/login")
    client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")})


def test_sso_passwort_vergessen_sendet_hinweis_ohne_token_und_bleibt_neutral(client, monkeypatch):
    user_id, _, _ = _org_user(provider="entra")
    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("app.routers.ui_password_reset.send_sso_hinweis", fake_send)
    client.get("/passwort-vergessen")
    unknown = client.post("/passwort-vergessen", data={"email": f"unknown-{uuid4().hex}@example.test", "_csrf": client.cookies.get("ec_csrf")})
    response = client.post("/passwort-vergessen", data={"email": _user_email(user_id), "_csrf": client.cookies.get("ec_csrf")})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert response.text == unknown.text
        assert len(sent) == 1 and sent[0]["org_slug"].startswith("m4-org-")
        assert db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).count() == 0
    finally:
        db.close()


def _user_email(user_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.get(User, user_id).email
    finally:
        db.close()


def test_bestaetigung_nach_reset_enthaelt_reset_variante_und_org_zeit(client, monkeypatch):
    user_id, org_id, _ = _org_user(timezone="America/New_York")
    raw = uuid4().hex
    db = SessionLocal()
    set_tenant_context(db, None)
    db.add(PasswordResetToken(user_id=user_id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=datetime.now(UTC) + timedelta(hours=1), zweck="reset"))
    db.commit()
    db.close()
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("app.routers.ui_password_reset.send_passwort_geaendert", fake_send)
    client.get(f"/passwort-zuruecksetzen?token={raw}")
    response = client.post("/passwort-zuruecksetzen", data={"token": raw, "password": "NeuesPasswort123!", "password_repeat": "NeuesPasswort123!", "_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200 and sent[0]["ausloeser"] == "reset"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.get(User, user_id)
        html, text = benutzer_mail_service.render("passwort_geaendert", **benutzer_mail_service.mail_kontext(db, org_id), user_display_name=user.display_name, zeitpunkt="01.01.2026 12:00", ausloeser="reset", ip=None)
        assert "Warst du das nicht?" in html and "Art der Änderung: Passwort zurücksetzen" in text and "01.01.2026 12:00" in html
    finally:
        db.close()


def test_bestaetigung_nach_einladung_hat_einrichtungs_text(client, monkeypatch):
    user_id, _, _ = _org_user()
    raw = uuid4().hex
    db = SessionLocal(); set_tenant_context(db, None)
    db.add(PasswordResetToken(user_id=user_id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=datetime.now(UTC) + timedelta(hours=1), zweck="einladung")); db.commit(); db.close()
    sent = []
    async def fake_send(*args, **kwargs): sent.append(kwargs)
    monkeypatch.setattr("app.routers.ui_password_reset.send_passwort_geaendert", fake_send)
    client.get(f"/passwort-zuruecksetzen?token={raw}")
    client.post("/passwort-zuruecksetzen", data={"token": raw, "password": "NeuesPasswort123!", "password_repeat": "NeuesPasswort123!", "_csrf": client.cookies.get("ec_csrf")})
    assert sent[0]["ausloeser"] == "einladung"
    html, text = benutzer_mail_service.render("passwort_geaendert", **benutzer_mail_service.mail_kontext(None, None), user_display_name="Neu", zeitpunkt="", ausloeser="einladung", ip=None)
    assert "Dein Konto ist jetzt eingerichtet" in html and "Warst du das nicht?" not in text


def test_bestaetigung_nach_profil_passwortaenderung(client, monkeypatch):
    _, _, username = _org_user()
    sent = []
    async def fake_send(*args, **kwargs): sent.append(kwargs)
    monkeypatch.setattr("app.routers.ui_profile.send_passwort_geaendert", fake_send)
    _login(client, username)
    response = client.post("/profil/passwort", data={"current_password": "Test1234!", "new_password": "ProfilPasswort123!", "_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert response.status_code == 302 and sent[0]["ausloeser"] == "profil"
    _, text = benutzer_mail_service.render("passwort_geaendert", **benutzer_mail_service.mail_kontext(None, None), user_display_name="Neu", zeitpunkt="01.01.2026 12:00", ausloeser="profil", ip=None)
    assert "Art der Änderung: Änderung im Profil" in text


def test_bestaetigung_nach_admin_passwort_setzen(client, monkeypatch):
    admin, target_id = _admin_and_user()
    sent = []
    async def fake_send(*args, **kwargs): sent.append(kwargs)
    monkeypatch.setattr("app.routers.ui_admin.send_passwort_geaendert", fake_send)
    _login(client, admin)
    response = client.post(f"/admin/benutzer/{target_id}/passwort", data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert response.status_code == 200 and sent[0]["ausloeser"] == "admin"
    _, text = benutzer_mail_service.render("passwort_geaendert", **benutzer_mail_service.mail_kontext(None, None), user_display_name="Neu", zeitpunkt="01.01.2026 12:00", ausloeser="admin", ip=None)
    assert "Art der Änderung: durch deinen Administrator" in text


def test_versandfehler_rollt_passwortaenderung_nicht_zurueck(client, monkeypatch):
    user_id, _, username = _org_user()
    async def failed_send(*args, **kwargs): raise RuntimeError("SMTP kaputt")
    monkeypatch.setattr("app.routers.ui_profile.send_passwort_geaendert", failed_send)
    _login(client, username)
    response = client.post("/profil/passwort", data={"current_password": "Test1234!", "new_password": "AnderesPasswort123!", "_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        assert response.status_code == 302
        assert verify_password("AnderesPasswort123!", db.get(User, user_id).password_hash)
    finally:
        db.close()


def test_admin_reset_mail_enthält_adminsatz_und_selfservice_nicht(monkeypatch):
    user_id, org_id, _ = _org_user()
    db = SessionLocal(); set_tenant_context(db, None)
    user = db.get(User, user_id)
    delivered = []
    async def fake_deliver(_db, _org_id, msg, _smtp_cfg): delivered.append(msg)
    monkeypatch.setattr("app.services.mail_service.deliver", fake_deliver)
    try:
        import asyncio
        asyncio.run(benutzer_service.send_password_reset_mail(user, db, durch_admin=True))
        asyncio.run(benutzer_mail_service.send_password_reset(to=user.email, reset_url="https://example.test/reset", user_display_name=user.display_name, durch_admin=False, db=db, org_id=org_id))
        admin_text = delivered[0].get_body(preferencelist=("plain",)).get_content()
        selfservice_text = delivered[1].get_body(preferencelist=("plain",)).get_content()
        assert "Dein Administrator hat das Zurücksetzen deines Passworts ausgelöst." in admin_text
        assert "Dein Administrator hat das Zurücksetzen deines Passworts ausgelöst." not in selfservice_text
    finally:
        db.close()
