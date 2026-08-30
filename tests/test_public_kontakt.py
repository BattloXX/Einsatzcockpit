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
