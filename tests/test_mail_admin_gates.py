"""Regressionstests für die Enable-Gates der Mail-Test-Endpunkte."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.master import SystemSettings
from app.models.user import Role, User, UserRole
from app.routers import ui_admin, ui_org_mail


class _Query:
    def __init__(self, *, rows=None, first=None):
        self._rows = rows or []
        self._first = first

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._first


class _Db:
    def __init__(self, *, rows=None, cfg=None):
        self.rows = rows or []
        self.cfg = cfg

    def query(self, model):
        return _Query(rows=self.rows, first=self.cfg)


def _request(email="admin@example.at"):
    user = SimpleNamespace(id=1, org_id=7, email=email, roles=[])
    return SimpleNamespace(state=SimpleNamespace(user=user), client=None), user


def _redirect_error(response):
    return parse_qs(urlparse(response.headers["location"]).query)["resend_error"][0]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([SimpleNamespace(key="resend_enabled", value="false")], "resend_enabled=false"),
        ([SimpleNamespace(key="resend_enabled", value="true")], "API-Key, Domain"),
    ],
)
async def test_global_resend_test_reports_exact_disabled_reason(monkeypatch, rows, expected):
    request, user = _request()

    response = await ui_admin.test_resend_mail(
        request=request, db=_Db(rows=rows), _=user,
        test_resend_mail_to="recipient@example.at",
    )

    assert response.status_code == 303
    assert expected in _redirect_error(response)


def _json(response):
    return json.loads(response.body)


async def test_org_o365_test_respects_global_gate(monkeypatch):
    """Resend hat bewusst KEIN Env-Kill-Switch (mehr) -- nur O365 hat noch einen
    globalen Gate über settings.O365_MAIL_ENABLED."""
    monkeypatch.setattr(settings, "O365_MAIL_ENABLED", False)
    request, user = _request()
    cfg = SimpleNamespace(enabled=True, is_fully_configured=True)

    response = await ui_org_mail.o365_test(request=request, db=_Db(cfg=cfg), user=user, recipient="")

    assert _json(response)["ok"] is False
    assert "O365_MAIL_ENABLED=false" in _json(response)["message"]


@pytest.mark.parametrize(
    ("endpoint", "provider_name"),
    [
        (ui_org_mail.smtp_test, "SMTP"),
        (ui_org_mail.o365_test, "Office 365"),
        (ui_org_mail.resend_test, "Resend"),
    ],
)
async def test_org_provider_test_respects_org_enabled_flag(
    monkeypatch, endpoint, provider_name
):
    monkeypatch.setattr(settings, "O365_MAIL_ENABLED", True)
    request, user = _request()
    cfg = SimpleNamespace(enabled=False, is_fully_configured=True)

    response = await endpoint(request=request, db=_Db(cfg=cfg), user=user, recipient="")

    assert _json(response)["ok"] is False
    assert provider_name in _json(response)["message"]
    assert "Organisation deaktiviert" in _json(response)["message"]


def _setup_system_admin(username: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "system_admin").first()
        if role is None:
            role = Role(code="system_admin", name="system_admin")
            db.add(role)
            db.flush()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="System Settings Test Admin",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _login(client, username):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )


def test_invalid_resend_domain_does_not_block_saving_resend_enabled(request):
    """Regression: Eine ungültige `resend_from_domain` brach bisher den GESAMTEN
    Save-Request per Early-Return VOR der known_keys-Schleife ab -- dadurch blieb
    `resend_enabled` unverändert in der DB, obwohl der Admin es auf "Aktiv" gestellt
    und gespeichert hatte (das Dropdown "sprang" nach dem Reload scheinbar zurück)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for key in ("resend_enabled", "resend_from_domain"):
            row = db.get(SystemSettings, key)
            if row is not None:
                db.delete(row)
        db.commit()
    finally:
        db.close()

    def cleanup():
        cleanup_db = SessionLocal()
        set_tenant_context(cleanup_db, None)
        try:
            for key in ("resend_enabled", "resend_from_domain"):
                row = cleanup_db.get(SystemSettings, key)
                if row is not None:
                    cleanup_db.delete(row)
            cleanup_db.commit()
        finally:
            cleanup_db.close()

    request.addfinalizer(cleanup)

    _setup_system_admin("resend_domain_sysadmin")
    client = TestClient(app)
    assert _login(client, "resend_domain_sysadmin").status_code == 302

    response = client.post(
        "/admin/system-einstellungen",
        data={
            "_csrf": client.cookies.get("ec_csrf"),
            "k_resend_enabled": "true",
            "k_resend_from_domain": "not a valid domain",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "settings_error" in response.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        resend_enabled = db.get(SystemSettings, "resend_enabled")
        assert resend_enabled is not None and resend_enabled.value == "true"
        resend_domain = db.get(SystemSettings, "resend_from_domain")
        assert resend_domain is None
    finally:
        db.close()
