"""Smoke tests for the public Gruppenkommandanten redemption page."""

from app.models.major_incident import LageEinheitZugang
from tests.test_gk_zugang_service import _ausgestellt, _session


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
