"""Regressionstests für die Enable-Gates der Mail-Test-Endpunkte."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import settings
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
    ("env_enabled", "rows", "expected"),
    [
        (False, [], "RESEND_ENABLED ist false"),
        (True, [SimpleNamespace(key="resend_enabled", value="false")], "resend_enabled=false"),
        (True, [SimpleNamespace(key="resend_enabled", value="true")], "API-Key, Domain"),
    ],
)
async def test_global_resend_test_reports_exact_disabled_reason(
    monkeypatch, env_enabled, rows, expected
):
    monkeypatch.setattr(settings, "RESEND_ENABLED", env_enabled)
    request, user = _request()

    response = await ui_admin.test_resend_mail(
        request=request, db=_Db(rows=rows), _=user,
        test_resend_mail_to="recipient@example.at",
    )

    assert response.status_code == 303
    assert expected in _redirect_error(response)


def _json(response):
    return json.loads(response.body)


@pytest.mark.parametrize(
    ("endpoint", "global_setting", "expected"),
    [
        (ui_org_mail.o365_test, "O365_MAIL_ENABLED", "O365_MAIL_ENABLED=false"),
        (ui_org_mail.resend_test, "RESEND_ENABLED", "RESEND_ENABLED=false"),
    ],
)
async def test_org_provider_test_respects_global_gate(
    monkeypatch, endpoint, global_setting, expected
):
    monkeypatch.setattr(settings, global_setting, False)
    request, user = _request()
    cfg = SimpleNamespace(enabled=True, is_fully_configured=True)

    response = await endpoint(request=request, db=_Db(cfg=cfg), user=user, recipient="")

    assert _json(response)["ok"] is False
    assert expected in _json(response)["message"]


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
    monkeypatch.setattr(settings, "RESEND_ENABLED", True)
    request, user = _request()
    cfg = SimpleNamespace(enabled=False, is_fully_configured=True)

    response = await endpoint(request=request, db=_Db(cfg=cfg), user=user, recipient="")

    assert _json(response)["ok"] is False
    assert provider_name in _json(response)["message"]
    assert "Organisation deaktiviert" in _json(response)["message"]
