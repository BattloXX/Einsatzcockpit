"""Tests fuer den Spam-Schutz des oeffentlichen Kontaktformulars."""

from unittest.mock import AsyncMock

from app.config import settings

OK = "/ueber-das-projekt?kontakt=ok#kontakt"
FEHLER = "/ueber-das-projekt?kontakt=fehler#kontakt"
GUELTIGE_DATEN = {
    "name": "Max Mustermann",
    "email": "max@example.at",
    "message": "Ich interessiere mich fuer das Einsatzcockpit.",
}


def _post_kontakt(client, **daten):
    client.get("/ueber-das-projekt")
    formular = {**GUELTIGE_DATEN, **daten, "_csrf": client.cookies.get("ec_csrf")}
    return client.post("/kontakt", data=formular, follow_redirects=False)


def test_kontaktformular_ist_im_testsystem_nicht_verfuegbar(client, monkeypatch):
    monkeypatch.setattr(settings, "TEST_SYSTEM", True)

    response = client.get("/ueber-das-projekt")

    assert response.status_code == 200
    assert 'action="/kontakt"' not in response.text
    assert 'id="kontakt"' not in response.text


def test_kontaktformular_ist_ausserhalb_des_testsystems_verfuegbar(client, monkeypatch):
    monkeypatch.setattr(settings, "TEST_SYSTEM", False)

    response = client.get("/ueber-das-projekt")

    assert response.status_code == 200
    assert 'action="/kontakt"' in response.text
    assert 'id="kontakt"' in response.text


def test_kontakt_submit_wird_im_testsystem_abgelehnt(client, monkeypatch):
    monkeypatch.setattr(settings, "TEST_SYSTEM", True)

    response = _post_kontakt(client)

    assert response.status_code == 404


def test_honeypot_wird_still_verworfen(client, monkeypatch):
    senden = AsyncMock()
    monkeypatch.setattr("app.routers.public.send_contact_message", senden)

    response = _post_kontakt(client, website="https://spam.example")

    assert response.status_code == 303
    assert response.headers["location"] == OK
    senden.assert_not_awaited()


def test_url_im_namen_wird_still_verworfen(client, monkeypatch):
    senden = AsyncMock()
    monkeypatch.setattr("app.routers.public.send_contact_message", senden)

    response = _post_kontakt(client, name="To the http://foo.example Owner")

    assert response.status_code == 303
    assert response.headers["location"] == OK
    senden.assert_not_awaited()


def test_fehlendes_pflichtfeld_liefert_fehler(client, monkeypatch):
    senden = AsyncMock()
    monkeypatch.setattr("app.routers.public.send_contact_message", senden)

    response = _post_kontakt(client, message="")

    assert response.status_code == 303
    assert response.headers["location"] == FEHLER
    senden.assert_not_awaited()


def test_turnstile_ablehnung_liefert_fehler(client, monkeypatch):
    senden = AsyncMock()
    pruefen = AsyncMock(return_value=False)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "test-secret")
    monkeypatch.setattr("app.routers.public.send_contact_message", senden)
    monkeypatch.setattr("app.routers.public._verify_turnstile", pruefen)

    response = _post_kontakt(client, **{"cf-turnstile-response": "ungueltig"})

    assert response.status_code == 303
    assert response.headers["location"] == FEHLER
    pruefen.assert_awaited_once()
    senden.assert_not_awaited()


def test_turnstile_erfolg_versendet_nachricht(client, monkeypatch):
    senden = AsyncMock()
    pruefen = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "test-secret")
    monkeypatch.setattr("app.routers.public.send_contact_message", senden)
    monkeypatch.setattr("app.routers.public._verify_turnstile", pruefen)

    response = _post_kontakt(client, **{"cf-turnstile-response": "gueltig"})

    assert response.status_code == 303
    assert response.headers["location"] == OK
    pruefen.assert_awaited_once()
    senden.assert_awaited_once()


def test_ohne_turnstile_secret_wird_ohne_pruefung_versendet(client, monkeypatch):
    senden = AsyncMock()
    pruefen = AsyncMock()
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")
    monkeypatch.setattr("app.routers.public.send_contact_message", senden)
    monkeypatch.setattr("app.routers.public._verify_turnstile", pruefen)

    response = _post_kontakt(client)

    assert response.status_code == 303
    assert response.headers["location"] == OK
    pruefen.assert_not_awaited()
    senden.assert_awaited_once()


# ── Versandweg der Kontaktanfrage: SMTP, sonst Resend mit noreply ─────────────

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.services import mail_service  # noqa: E402


def _kontakt_senden():
    asyncio.run(mail_service.send_contact_message(
        name="Max", reply_email="max@example.at", message="Hallo", db=None,
    ))


def test_kontakt_ohne_smtp_geht_ueber_resend_mit_noreply(monkeypatch):
    gesendet = []

    async def resend(msg, api_key, from_addr, attachments=None):
        gesendet.append((from_addr, msg["Reply-To"], msg["To"]))

    monkeypatch.setattr(mail_service, "get_smtp_cfg", lambda _db=None: {"host": None})
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: {"api_key": "k", "from_domain": "Einsatzcockpit.com"})
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", resend)
    _kontakt_senden()
    assert gesendet == [("noreply@einsatzcockpit.com", "max@example.at", mail_service.CONTACT_RECIPIENT)]


def test_kontakt_mit_smtp_bleibt_bei_smtp(monkeypatch):
    smtp, resend = AsyncMock(), AsyncMock()
    monkeypatch.setattr(mail_service, "get_smtp_cfg", lambda _db=None: {
        "host": "smtp.example.at", "port": 587, "user": "u", "password": "p", "from_addr": "kontakt@example.at",
        "starttls": True, "timeout": 5,
    })
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: {"api_key": "k", "from_domain": "example.at"})
    monkeypatch.setattr(mail_service, "_send", smtp)
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", resend)
    _kontakt_senden()
    smtp.assert_awaited_once()
    resend.assert_not_awaited()


def test_kontakt_ohne_versandweg_meldet_fehler(monkeypatch, client):
    monkeypatch.setattr(mail_service, "get_smtp_cfg", lambda _db=None: {"host": None})
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: None)
    with pytest.raises(mail_service.MailConfigError):
        _kontakt_senden()
    # Über das Formular: Fehlerhinweis statt stillem Erfolg.
    monkeypatch.setattr(settings, "TEST_SYSTEM", False)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")
    assert _post_kontakt(client).headers["location"] == FEHLER


def test_kontakt_smtp_fehler_faellt_auf_resend_zurueck(monkeypatch):
    resend = AsyncMock()
    monkeypatch.setattr(mail_service, "get_smtp_cfg", lambda _db=None: {"host": "smtp.kaputt.example"})
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: {"api_key": "k", "from_domain": "example.at"})
    monkeypatch.setattr(mail_service, "_send", AsyncMock(side_effect=OSError("connection refused")))
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", resend)
    _kontakt_senden()
    assert resend.await_args.args[2] == "noreply@example.at"
