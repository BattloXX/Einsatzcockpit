"""HTTP-Tests des Zugang-Tabs der Ressourcenkarte."""

from uuid import uuid4

import pytest

import app.routers.ui_ressourcenkarte as router
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, LageEinheitLeader, LageEinheitZugang, MajorIncident
from app.models.master import FireDept, OrgSettings
from app.models.user import Role, User, UserRole
from app.services import gk_zugang_service as service
from tests.test_ressourcenkarte_routes import _login


class _Ergebnis:
    success = True
    provider = "gateway"


@pytest.fixture
def sms(monkeypatch):
    gesendet = []

    async def fake_send(org_id, to, text, timeout=15.0, **kwargs):
        gesendet.append(text)
        return _Ergebnis()

    monkeypatch.setattr(service, "send_sms", fake_send)
    monkeypatch.setattr(service, "sms_available", lambda *a, **k: True)
    monkeypatch.setattr(service, "darf_extern", lambda *a, **k: True)
    return gesendet


def _daten(role_code="recorder", org=None):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        if org is None:
            org = FireDept(slug="zr-" + suffix, name="ZR", color="#f00", bos="FW")
            db.add(org)
            db.flush()
            db.add(OrgSettings(org_id=org.id, gk_zugang_aktiv=True))
        user = User(
            username="zr-" + suffix, password_hash=hash_password("Test1234!"), display_name="Funker",
            org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        lage = MajorIncident(org_id=org.id, name="Lage", status="active")
        db.add(lage)
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="RLF", status="bereitgestellt")
        db.add(einheit)
        db.flush()
        leader = LageEinheitLeader(
            einheit_id=einheit.id, person_name="Max Muster", phone="0664 1234567", phone_e164="+436641234567",
            start_at=service._now(), rolle="fuehrer",
        )
        db.add(leader)
        db.flush()
        einheit.leader_assignment_id = leader.id
        db.commit()
        return user.username, lage.id, einheit.id, org.id
    finally:
        db.close()


def _basis(lage_id, einheit_id):
    return f"/lage/{lage_id}/einheiten/{einheit_id}"


def test_rahmen_zeigt_zugang_tab_nur_fuer_berechtigte(client, setup_db):
    username, lage_id, einheit_id, _ = _daten("recorder")
    _login(client, username)
    assert 'data-karte-tab="zugang"' in client.get(_basis(lage_id, einheit_id) + "/karte").text


def test_readonly_hat_weder_tab_noch_zugriff(client, setup_db):
    username, lage_id, einheit_id, _ = _daten("readonly")
    _login(client, username)
    assert 'data-karte-tab="zugang"' not in client.get(_basis(lage_id, einheit_id) + "/karte").text
    assert client.get(_basis(lage_id, einheit_id) + "/karte/zugang").status_code == 403
    token = client.cookies.get("ec_csrf")
    assert client.post(_basis(lage_id, einheit_id) + "/zugang/link", data={"modus": "link", "_csrf": token}).status_code == 403


def test_tab_zeigt_zustaende_und_enthaelt_keinen_token(client, setup_db):
    username, lage_id, einheit_id, _ = _daten()
    _login(client, username)
    html = client.get(_basis(lage_id, einheit_id) + "/karte/zugang").text
    assert 'data-zugang-status="kein_zugang"' in html and "+43 664" in html
    assert "gkz_" not in html


def test_link_json_ist_no_store_und_zweiter_aufruf_mit_link_rotiert_nicht(client, setup_db):
    username, lage_id, einheit_id, _ = _daten()
    _login(client, username)
    token = client.cookies.get("ec_csrf")
    antwort = client.post(_basis(lage_id, einheit_id) + "/zugang/link", data={"modus": "nachricht", "_csrf": token})
    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == "no-store"
    daten = antwort.json()
    assert daten["link"] in daten["text"] and "/gk#gkz_" in daten["link"]
    zweite = client.post(
        _basis(lage_id, einheit_id) + "/zugang/link",
        data={"modus": "link", "bestehender_link": daten["link"], "_csrf": token},
    ).json()
    assert zweite["link"] == daten["link"] and zweite["generation"] == daten["generation"]
    html = client.get(_basis(lage_id, einheit_id) + "/karte/zugang").text
    assert "gkz_" not in html and 'data-zugang-status="aktiv"' in html


def test_aktive_sitzung_liefert_409(client, setup_db):
    username, lage_id, einheit_id, _ = _daten()
    _login(client, username)
    token = client.cookies.get("ec_csrf")
    link = client.post(_basis(lage_id, einheit_id) + "/zugang/link", data={"modus": "link", "_csrf": token}).json()["link"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="personal").one()
        service.sitzung_anlegen(db, zugang, user_agent="UA", ip="1.2.3.4", verifiziert=True)
        db.commit()
    finally:
        db.close()
    antwort = client.post(_basis(lage_id, einheit_id) + "/zugang/link", data={"modus": "link", "_csrf": token})
    assert antwort.status_code == 409 and antwort.json()["code"] == "sitzung_aktiv"
    assert client.post(
        _basis(lage_id, einheit_id) + "/zugang/link", data={"modus": "link", "bestaetigt": "1", "_csrf": token}
    ).status_code == 200
    assert link


def test_senden_widerrufen_und_broadcast(client, setup_db, sms, monkeypatch):
    events = []

    async def fake_broadcast(lage, event):
        events.append(event)

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    username, lage_id, einheit_id, _ = _daten()
    _login(client, username)
    token = client.cookies.get("ec_csrf")
    antwort = client.post(_basis(lage_id, einheit_id) + "/zugang/senden", data={"_csrf": token})
    assert antwort.status_code == 200 and 'data-versand-status="gesendet"' in antwort.text
    assert len(sms) == 1 and "/gk#gkz_" in sms[0]
    assert "gkz_" not in antwort.text
    assert {"type": "ressource:changed", "einheit_id": einheit_id} in events
    antwort = client.post(_basis(lage_id, einheit_id) + "/zugang/widerrufen", data={"_csrf": token})
    assert antwort.status_code == 200 and 'data-zugang-status="widerrufen"' in antwort.text


def test_sitzung_einer_anderen_einheit_nicht_beendbar_und_fremde_org_404(client, setup_db):
    username, lage_id, einheit_id, _ = _daten()
    _, _, andere_einheit, _ = _daten()
    _login(client, username)
    token = client.cookies.get("ec_csrf")
    assert client.post(_basis(lage_id, einheit_id) + "/zugang/sitzung/999999/beenden", data={"_csrf": token}).status_code == 404
    assert client.post(_basis(lage_id, andere_einheit) + "/zugang/widerrufen", data={"_csrf": token}).status_code in (403, 404)
    assert client.get(_basis(lage_id, andere_einheit) + "/karte/zugang").status_code in (403, 404)


def test_zugang_verwaltung_ist_fuer_geraete_und_qr_sitzungen_gesperrt():
    from types import SimpleNamespace

    from app.routers.ui_ressourcenkarte import _zugang_erlaubt

    user = SimpleNamespace(roles=[SimpleNamespace(code="recorder")], gsl_nur_lesen=False)
    normal = SimpleNamespace(state=SimpleNamespace(is_device=False, qr_lage_id=None, qr_incident_id=None))
    assert _zugang_erlaubt(normal, user) is True
    for feld, wert in (("is_device", True), ("qr_lage_id", 5), ("qr_incident_id", 7)):
        request = SimpleNamespace(state=SimpleNamespace(is_device=False, qr_lage_id=None, qr_incident_id=None))
        setattr(request.state, feld, wert)
        assert _zugang_erlaubt(request, user) is False
    user.gsl_nur_lesen = True
    assert _zugang_erlaubt(normal, user) is False
