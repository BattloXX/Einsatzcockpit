"""Gezielte Tests fuer den Einladungsablauf."""
import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.core.security import hash_password
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.services import benutzer_mail_service, benutzer_service
from tests.test_benutzer_mitglied import _login
from tests.conftest import TestingSession


@pytest.fixture
def org():
    db = TestingSession()
    set_tenant_context(db, None)
    org = FireDept(slug=f"einladung-{uuid4().hex}", name="Einladung Test")
    db.add(org)
    db.commit()
    try:
        yield org
    finally:
        db.close()


def _token_aus_link(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def test_einladung_entwertet_altes_token_und_hat_lange_gueltigkeit(org):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        user = User(username=f"einladung-alt-{uuid4().hex}", display_name="Einladung", email=f"e-{uuid4().hex}@example.test",
                    password_hash="x", org_id=org.id)
        db.add(user)
        db.flush()
        alt = PasswordResetToken(user_id=user.id, token_hash="a" * 64,
                                 expires_at=datetime.now(UTC) + timedelta(hours=1), zweck="einladung")
        db.add(alt)
        db.commit()

        link = benutzer_service.einladung_erstellen(db, user)
        neu = db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.token_hash != alt.token_hash,
        ).one()
        assert _token_aus_link(link)
        assert alt.used_at is not None
        assert neu.zweck == "einladung"
        assert neu.expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(days=6)
    finally:
        db.close()


def test_einladungsmail_enthaelt_kein_passwort_und_app_links(org, monkeypatch):
    db = TestingSession()
    set_tenant_context(db, None)
    delivered = []

    async def fake_deliver(_db, _org_id, msg, _smtp_cfg):
        delivered.append(msg)

    monkeypatch.setattr("app.services.mail_service.deliver", fake_deliver)
    try:
        asyncio.run(benutzer_mail_service.send_einladung(
            to="e@example.test", username="einladung-mail", einladung_url="https://example.test/token",
            user_display_name="Einladung", db=db, org_id=org.id,
        ))
        message = delivered[0]
        content = message.as_string()
        assert "Passwort festlegen" in content
        assert "https://example.test/token" in content
        assert "/app/android" in content
        assert "Passwort\n" not in content
    finally:
        db.close()


def _admin_und_org(suffix: str):
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=f"invite-{suffix}-{uuid4().hex}", name="Einladung Test")
    admin = User(username=f"invite-admin-{suffix}-{uuid4().hex}", display_name="Admin",
                 password_hash=hash_password("Test1234!"), org=org)
    from app.models.user import Role, UserRole
    role = db.query(Role).filter(Role.code == "admin").one()
    db.add_all([org, admin])
    db.flush()
    db.add(UserRole(user_id=admin.id, role_id=role.id))
    db.commit()
    result = (admin.username, org.id)
    db.close()
    return result


def test_admin_anlage_mit_einladung_zeigt_kein_passwort(client, monkeypatch):
    admin, _ = _admin_und_org("mail")
    sent = []

    async def fake_send(db, user, requesting_ip=None):
        sent.append(user)

    monkeypatch.setattr("app.routers.ui_admin.benutzer_service.einladung_senden", fake_send)
    _login(client, admin)
    response = client.post("/admin/benutzer/neu", data={
        "username": f"invite-mail-{uuid4().hex}", "display_name": "Neu",
        "email": f"invite-{uuid4().hex}@example.test", "send_welcome": "true",
        "_csrf": client.cookies.get("ec_csrf"),
    })
    assert response.status_code == 200
    assert len(sent) == 1
    assert "Neues Passwort für" not in response.text


def test_admin_anlage_ohne_einladung_zeigt_generiertes_oder_manuelles_passwort(client, monkeypatch):
    admin, _ = _admin_und_org("password")
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append(1)

    monkeypatch.setattr("app.routers.ui_admin.benutzer_service.einladung_senden", fake_send)
    _login(client, admin)
    manual = "ManuellTest123!"
    response = client.post("/admin/benutzer/neu", data={
        "username": f"invite-manual-{uuid4().hex}", "display_name": "Neu", "password": manual,
        "email": f"manual-{uuid4().hex}@example.test", "send_welcome": "true",
        "_csrf": client.cookies.get("ec_csrf"),
    })
    assert response.status_code == 200 and manual in response.text and not sent
    response = client.post("/admin/benutzer/neu", data={
        "username": f"invite-generated-{uuid4().hex}", "display_name": "Neu",
        "_csrf": client.cookies.get("ec_csrf"),
    })
    assert response.status_code == 200 and "Neues Passwort für" in response.text and not sent


def test_einladung_reset_seite_einloesbar_und_abgelaufen(client):
    admin, org_id = _admin_und_org("reset")
    db = SessionLocal()
    set_tenant_context(db, None)
    username = f"invite-reset-{uuid4().hex}"
    user = User(username=username, display_name="Neu", password_hash="x", org_id=org_id)
    db.add(user)
    db.commit()
    user_id = user.id
    link = benutzer_service.einladung_erstellen(db, user)
    token = _token_aus_link(link)
    db.close()
    response = client.get(f"/passwort-zuruecksetzen?token={token}")
    assert "Willkommen – Passwort festlegen" in response.text
    response = client.post("/passwort-zuruecksetzen", data={"token": token, "password": "NeuesPasswort123!", "password_repeat": "NeuesPasswort123!", "_csrf": client.cookies.get("ec_csrf")})
    assert "Jetzt anmelden" in response.text
    client.cookies.clear()
    client.get("/login")
    assert client.post("/login", data={
        "username": username, "password": "NeuesPasswort123!",
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False).status_code == 302
    db = SessionLocal()
    expired_raw = "abgelaufene-einladung"
    expired = PasswordResetToken(user_id=user_id, token_hash=hashlib.sha256(expired_raw.encode()).hexdigest(),
                                 zweck="einladung", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    db.add(expired)
    db.commit()
    db.close()
    assert "Diese Einladung ist abgelaufen" in client.get(f"/passwort-zuruecksetzen?token={expired_raw}").text


def _benutzer(org_id: int, *, email: bool = True, eingeloggt: bool = False) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    user = User(username=f"invite-ziel-{uuid4().hex}", display_name="Ziel", password_hash="x", org_id=org_id,
                email=f"ziel-{uuid4().hex}@example.test" if email else None,
                last_login_at=datetime.now(UTC) if eingeloggt else None)
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return user_id


def _tokens(user_id: int) -> list[PasswordResetToken]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user_id).order_by(
            PasswordResetToken.id).all()
    finally:
        db.close()


def test_einladung_erneut_senden_entwertet_alten_token_nur_ohne_login(client, monkeypatch):
    admin, org_id = _admin_und_org("resend")
    monkeypatch.setattr("app.services.benutzer_mail_service.send_einladung", _async_noop)
    ziel = _benutzer(org_id)
    schon_angemeldet = _benutzer(org_id, eingeloggt=True)
    _login(client, admin)
    csrf = client.cookies.get("ec_csrf")
    for _ in range(2):
        response = client.post(f"/admin/benutzer/{ziel}/einladung", data={"_csrf": csrf}, follow_redirects=False)
        assert response.status_code == 303 and "invite=1" in response.headers["location"]
    erster, zweiter = _tokens(ziel)
    assert erster.used_at is not None and zweiter.used_at is None and zweiter.zweck == "einladung"
    client.post(f"/admin/benutzer/{schon_angemeldet}/einladung", data={"_csrf": csrf}, follow_redirects=False)
    assert _tokens(schon_angemeldet) == []
    # Ohne CSRF-Token wird nichts versendet.
    client.post(f"/admin/benutzer/{ziel}/einladung", data={}, follow_redirects=False)
    assert len(_tokens(ziel)) == 2


async def _async_noop(*args, **kwargs):
    return None


def test_benutzerliste_zeigt_einladungsstatus(client):
    admin, org_id = _admin_und_org("status")
    offen = _benutzer(org_id)
    abgelaufen = _benutzer(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    db.add_all([
        PasswordResetToken(user_id=offen, token_hash=uuid4().hex * 2, zweck="einladung",
                           expires_at=datetime.now(UTC) + timedelta(days=1)),
        PasswordResetToken(user_id=abgelaufen, token_hash=uuid4().hex * 2, zweck="einladung",
                           expires_at=datetime.now(UTC) - timedelta(days=1)),
    ])
    db.commit()
    db.close()
    _login(client, admin)
    text = client.get("/admin/benutzer").text
    assert "Einladung offen" in text and "Einladung abgelaufen" in text


def test_mailfehler_bei_anlage_zeigt_hinweis_und_benutzer_existiert(client, monkeypatch):
    admin, _ = _admin_und_org("fehler")

    async def kaputt(*args, **kwargs):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr("app.services.benutzer_mail_service.send_einladung", kaputt)
    _login(client, admin)
    username = f"invite-fehler-{uuid4().hex}"
    response = client.post("/admin/benutzer/neu", data={
        "username": username, "display_name": "Neu", "email": f"fehler-{uuid4().hex}@example.test",
        "send_welcome": "true", "_csrf": client.cookies.get("ec_csrf"),
    })
    assert response.status_code == 200
    assert "Einladung konnte nicht gesendet werden" in response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    assert db.query(User).filter(User.username == username).one()
    db.close()


def test_org_admin_anlage_nutzt_einladung_mit_langer_gueltigkeit(monkeypatch):
    from types import SimpleNamespace

    from app.config import settings
    from app.routers.ui_settings import _invite_org_admin

    monkeypatch.setattr("app.services.benutzer_mail_service.send_einladung", _async_noop)
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=f"orgadmin-{uuid4().hex}", name="Org-Admin Test")
    db.add(org)
    db.commit()
    email = f"orgadmin-{uuid4().hex}@example.test"
    asyncio.run(_invite_org_admin(SimpleNamespace(client=None), db, org, email, "Org Admin"))
    db.commit()
    user = db.query(User).filter(User.email == email).one()
    [token] = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).all()
    assert token.zweck == "einladung"
    restzeit = token.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)
    assert restzeit > timedelta(hours=settings.INVITE_TTL_HOURS - 1)
    db.close()


def test_mcp_benutzer_anlegen_liefert_einladung_und_nie_passwort(client, monkeypatch):
    from tests.test_mcp_benutzer import _rufe, _seed, _token

    monkeypatch.setattr("app.services.benutzer_mail_service.send_einladung", _async_noop)
    seed = _seed(f"invite-mcp-{uuid4().hex[:8]}", {"admin": "admin"})
    token = _token(client, seed["usernames"]["admin"])
    ergebnis = _rufe(client, token, "benutzer_anlegen", username=f"mcp-neu-{uuid4().hex}",
                     display_name="MCP Neu", email=f"mcp-{uuid4().hex}@example.test")
    assert ergebnis["einladung_gesendet"] is True
    assert "passwort" not in str(ergebnis).lower() and "password" not in str(ergebnis).lower()
