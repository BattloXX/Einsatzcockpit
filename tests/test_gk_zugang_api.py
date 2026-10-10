"""Integration tests for the public Gruppenkommandanten redemption page."""

from datetime import timedelta
from types import SimpleNamespace

from app.core.security import hash_api_key
from app.models.major_incident import LageEinheitZugang, LageEinheitZugangSession, LageEinheitZugangVersand
from app.models.sms import SmsLog
from app.models.user import AuditLog
from app.services import gk_zugang_service as service
from tests.test_gk_zugang_service import _ausgestellt, _session


def _headers(client):
    return {"X-CSRF-Token": client.get("/gk").cookies.get("ec_csrf")}


def _token(neu):
    return neu.link.rsplit("#", 1)[1]


def test_gk_start_is_static_and_sets_security_headers(client):
    with _session() as db:
        before = db.query(LageEinheitZugang).count()
    response = client.get("/gk")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    with _session() as db:
        assert db.query(LageEinheitZugang).count() == before


def test_gk_pruefen_unbekannt_und_einloesen_setzt_sitzung_cookie(client):
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db)
        db.commit()
        token = neu.link.rsplit("#", 1)[1]
    csrf = client.get("/gk").cookies.get("ec_csrf")
    headers = {"X-CSRF-Token": csrf}
    unknown = client.post("/gk/pruefen", json={"token": "gkz_unbekannt"}, headers=headers)
    assert unknown.json()["meldung"] == "Link ungültig"
    response = client.post("/gk/einloesen", json={"token": token}, headers=headers)
    assert response.status_code == 200
    assert "ec_gk=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]


def test_gk_pruefen_ist_read_only_und_meldet_rotation_und_ablauf(client):
    with _session() as db:
        _, lage, einheit, _, neu = _ausgestellt(db)
        token = _token(neu)
        lage_id, einheit_id = lage.id, einheit.id
        db.commit()
    headers = _headers(client)
    with _session() as db:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        assert zugang is not None
        before = (
            zugang.einloesungen,
            db.query(AuditLog).filter_by(org_id=zugang.org_id, entity_id=zugang.einheit_id).count(),
        )
    assert client.post("/gk/pruefen", json={"token": token}, headers=headers).json()["ok"] is True
    unbekannt = client.post("/gk/pruefen", json={"token": "gkz_unbekannt"}, headers=headers)
    assert unbekannt.json()["meldung"] == "Link ungültig"
    with _session() as db:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        assert zugang is not None
        assert (
            zugang.einloesungen,
            db.query(AuditLog).filter_by(org_id=zugang.org_id, entity_id=zugang.einheit_id).count(),
        ) == before
        lage, einheit = db.get(type(lage), lage_id), db.get(type(einheit), einheit_id)
        replacement = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="rotation")
        db.commit()
    beendet = client.post("/gk/pruefen", json={"token": token}, headers=headers)
    assert "beendet oder ersetzt" in beendet.json()["meldung"]
    with _session() as db:
        zugang = db.get(LageEinheitZugang, replacement.zugang_id)
        zugang.laeuft_ab_at = service._now() - timedelta(seconds=1)
        db.commit()
    abgelaufen = client.post("/gk/pruefen", json={"token": _token(replacement)}, headers=headers)
    assert "beendet oder ersetzt" in abgelaufen.json()["meldung"]


def test_gk_einloesen_validiert_sitzung_rotiert_und_beendet_aelteste(client):
    with _session() as db:
        _, lage, einheit, _, neu = _ausgestellt(db, maximum=2)
        token = _token(neu)
        lage_id, einheit_id = lage.id, einheit.id
        db.commit()
    headers, cookies = _headers(client), []
    for _ in range(3):
        response = client.post("/gk/einloesen", json={"token": token}, headers=headers)
        assert response.status_code == 200
        assert "Path=/" in response.headers["set-cookie"]
        cookies.append(response.cookies.get("ec_gk"))
    with _session() as db:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        sessions = db.query(LageEinheitZugangSession).filter_by(zugang_id=zugang.id, org_id=zugang.org_id).all()
        assert zugang.einloesungen == 3
        assert service.sitzung_pruefen(db, cookies[0]) is None
        assert service.sitzung_pruefen(db, cookies[-1]) is not None
        assert sum(row.revoked_at is None for row in sessions) == 2
        lage, einheit = db.get(type(lage), lage_id), db.get(type(einheit), einheit_id)
        service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="rotation")
        db.commit()
    beendet = client.post("/gk/pruefen", json={"token": token}, headers=headers)
    assert "beendet oder ersetzt" in beendet.json()["meldung"]


def test_gk_pin_sperre_anfrage_und_verifikation(client, monkeypatch):
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db, pin=True)
        token = _token(neu)
        db.commit()
    headers = _headers(client)
    assert client.post("/gk/einloesen", json={"token": token}, headers=headers).status_code == 403
    for _ in range(4):
        assert client.post("/gk/einloesen", json={"token": token, "pin": "000000"}, headers=headers).status_code == 403
    with _session() as db:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        assert zugang.pin_versuche == 5 and zugang.pin_gesperrt_bis > service._now()
        zugang.pin_hash, zugang.pin_gueltig_bis = hash_api_key("123456"), service._now() + timedelta(minutes=1)
        db.commit()
    assert client.post("/gk/einloesen", json={"token": token, "pin": "123456"}, headers=headers).status_code == 403
    assert client.post("/gk/pin", json={"token": token}, headers=headers).status_code == 429
    with _session() as db:
        db.get(LageEinheitZugang, neu.zugang_id).pin_gesperrt_bis = None
        db.commit()
    sent = []

    async def send_sms(_org_id, _phone, text):
        sent.append(text)
        return SimpleNamespace(success=True)

    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *_: True)
    monkeypatch.setattr("app.services.sms_service.send_sms", send_sms)
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *_a, **_kw: True)
    assert client.post("/gk/pin", json={"token": token}, headers=headers).status_code == 200
    pin = sent[0].rsplit(": ", 1)[1]
    assert len(pin) == 6 and pin.isdigit()
    with _session() as db:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        versand = db.query(LageEinheitZugangVersand).filter_by(zugang_id=zugang.id, org_id=zugang.org_id).one()
        assert versand.kanal == "pin_sms" and versand.status == "gesendet"
        assert pin not in str(vars(versand))
        assert pin not in " ".join(row.text for row in db.query(SmsLog).filter_by(org_id=zugang.org_id).all())
        audits = db.query(AuditLog).filter_by(org_id=zugang.org_id, entity_id=zugang.einheit_id).all()
        assert pin not in " ".join(row.payload_json or "" for row in audits)
    response = client.post("/gk/einloesen", json={"token": token, "pin": pin}, headers=headers)
    cookie = response.cookies.get("ec_gk")
    assert response.status_code == 200
    with _session() as db:
        assert service.sitzung_pruefen(db, cookie).session.verifiziert_at is not None
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        zugang.pin_hash, zugang.pin_gueltig_bis = hash_api_key("654321"), service._now() - timedelta(seconds=1)
        db.commit()
    assert client.post("/gk/einloesen", json={"token": token, "pin": "654321"}, headers=headers).status_code == 403


def test_gk_pin_limit_provider_fehler_abmelden_csrf_und_logs(client, monkeypatch, caplog):
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db, pin=True)
        token = _token(neu)
        db.commit()
    headers = _headers(client)
    assert client.post("/gk/pruefen", json={"token": token}).status_code == 403
    sent = []

    async def send_sms(_org_id, _phone, text):
        sent.append(text)
        return SimpleNamespace(success=True)

    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *_: True)
    monkeypatch.setattr("app.services.sms_service.send_sms", send_sms)
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *_a, **_kw: True)
    caplog.set_level("DEBUG")
    for _ in range(3):
        assert client.post("/gk/pin", json={"token": token}, headers=headers).status_code == 200
    assert client.post("/gk/pin", json={"token": token}, headers=headers).status_code == 429
    pin = sent[-1].rsplit(": ", 1)[1]
    first_cookie = client.post("/gk/einloesen", json={"token": token, "pin": pin}, headers=headers).cookies.get("ec_gk")
    with _session() as db:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        second_cookie, _ = service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
        db.commit()
    client.cookies.set("ec_gk", first_cookie)
    assert client.post("/gk/abmelden", headers=headers).status_code == 200
    with _session() as db:
        assert service.sitzung_pruefen(db, first_cookie) is None
        assert service.sitzung_pruefen(db, second_cookie) is not None
        db.get(LageEinheitZugang, neu.zugang_id).pin_gesperrt_bis = None
        db.commit()
    with _session() as db:
        _, _, _, _, unavailable = _ausgestellt(db, pin=True)
        unavailable_token = _token(unavailable)
        db.commit()
    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *_: False)
    assert client.post("/gk/pin", json={"token": unavailable_token}, headers=headers).status_code == 503
    with _session() as db:
        versand = db.query(LageEinheitZugangVersand).filter_by(
            zugang_id=unavailable.zugang_id, status="uebersprungen"
        )
        assert versand.count() == 1
    assert token not in caplog.text and first_cookie not in caplog.text and pin not in caplog.text
