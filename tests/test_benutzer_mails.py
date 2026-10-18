"""Benutzer-Mails: Branding, Escaping und kompatible Weiterleitungen."""
import asyncio
from uuid import uuid4

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.master import FireDept, OrgSettings
from app.services import benutzer_mail_service, mail_service
from tests.conftest import TestingSession


@pytest.fixture
def org():
    db = TestingSession()
    set_tenant_context(db, None)
    org = FireDept(slug=f"mail-test-{uuid4().hex}", name="Mail Test", color="#112233", logo_path="/static/img/org.png")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, primary_color="#445566"))
    db.commit()
    try:
        yield db, org.id
    finally:
        db.close()


@pytest.mark.parametrize(
    ("name", "daten", "button_url"),
    [
        ("willkommen", {"user_display_name": "Anna", "username": "anna", "password": "secret"}, "http://localhost:8092"),
        ("willkommen_sso", {"user_display_name": "Anna", "sso_url": "https://login.example/sso"}, "https://login.example/sso"),
        ("passwort_reset", {"user_display_name": "Anna", "reset_url": "https://login.example/reset", "gueltigkeit": "30 Minuten"}, "https://login.example/reset"),
    ],
)
def test_benutzer_mails_rendern_html_und_text_mit_branding(org, name, daten, button_url):
    db, org_id = org
    context = benutzer_mail_service.mail_kontext(db, org_id)
    html, text = benutzer_mail_service.render(name, **context, **daten)

    assert "Mail Test" in html and "Mail Test" in text
    assert "http://localhost:8092/static/img/org.png" in html
    assert button_url in html and button_url in text


def test_anzeigename_wird_in_html_escaped_auch_beim_reset(org):
    db, org_id = org
    context = benutzer_mail_service.mail_kontext(db, org_id)
    for name, daten in (
        ("willkommen", {"username": "anna", "password": "secret"}),
        ("willkommen_sso", {"sso_url": "https://example.test/sso"}),
        ("passwort_reset", {"reset_url": "https://example.test/reset", "gueltigkeit": "30 Minuten"}),
    ):
        html, _ = benutzer_mail_service.render(name, **context, user_display_name="<script>x</script>", **daten)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x&lt;/script&gt;" in html


def test_testsystem_banner_nur_bei_testsystem(org, monkeypatch):
    db, org_id = org
    monkeypatch.setattr(settings, "TEST_SYSTEM", False)
    context = benutzer_mail_service.mail_kontext(db, org_id)
    html, _ = benutzer_mail_service.render("passwort_reset", **context, user_display_name="Anna",
                                            reset_url="https://example.test/reset", gueltigkeit="30 Minuten")
    assert "TESTSYSTEM" not in html
    monkeypatch.setattr(settings, "TEST_SYSTEM", True)
    context = benutzer_mail_service.mail_kontext(db, org_id)
    html, _ = benutzer_mail_service.render("passwort_reset", **context, user_display_name="Anna",
                                            reset_url="https://example.test/reset", gueltigkeit="30 Minuten")
    assert "TESTSYSTEM" in html


def test_ungueltige_brand_color_faellt_zurueck(org):
    db, org_id = org
    settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).one()
    settings_row.primary_color = "red"
    db.commit()
    assert benutzer_mail_service.mail_kontext(db, org_id)["brand_color"] == "#c8102e"


@pytest.mark.parametrize(
    ("funktion", "kwargs", "betreff"),
    [
        (mail_service.send_welcome_mail, {"username": "anna", "password": "secret", "user_display_name": "Anna"}, "Willkommen bei Einsatzcockpit – Mail Test"),
        (mail_service.send_sso_welcome_mail, {"user_display_name": "Anna", "app_url": "https://ignored.example", "org_slug": "mail-test"}, "Willkommen bei Einsatzcockpit – Anmeldung mit Microsoft"),
        (mail_service.send_password_reset, {"reset_url": "https://example.test/reset", "user_display_name": "Anna"}, "Passwort zurücksetzen – Einsatzcockpit Mail Test"),
    ],
)
def test_mail_service_weiterleitungen_liefern_korrekt_an_empfaenger(org, monkeypatch, funktion, kwargs, betreff):
    db, org_id = org
    gesendet = []

    async def fake_deliver(_db, _org_id, msg, _smtp_cfg):
        gesendet.append(msg)

    monkeypatch.setattr(mail_service, "deliver", fake_deliver)
    asyncio.run(funktion(to="anna@example.test", db=db, org_id=org_id, **kwargs))
    assert str(gesendet[0]["To"]) == "anna@example.test"
    assert str(gesendet[0]["Subject"]) == betreff
