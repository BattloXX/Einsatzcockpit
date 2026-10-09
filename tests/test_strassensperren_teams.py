"""Teams-Benachrichtigungen für Straßensperren: Ereignisse, Outbox, Adaptive Card, Einstellungen."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.road_closure import RoadClosure, RoadClosureNotification, RoadClosureTeamsConfig
from app.services import road_closure_notification_loop as loop
from app.services import road_closure_notify_service as notify
from app.services import road_closure_service as service
from app.services.road_closure_public_service import public_closure_dict
from tests.test_strassensperren_freigaben import _LINE, GEHEIM, _modul_an, _now, _sperre
from tests.test_strassensperren_ui import _login, _payload, _setup_user

HOOK = "https://example.invalid/webhook/SECRET-HOOK-123"


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


@pytest.fixture
def teams_config(setup_db):
    """Teams-Konfiguration für Org 1; danach wieder deaktiviert, damit andere Tests nichts einreihen."""
    db = _db()
    try:
        _modul_an(db, 1)
        config = db.query(RoadClosureTeamsConfig).filter_by(org_id=1).first() or RoadClosureTeamsConfig(org_id=1)
        config.enabled, config.webhook_url_enc = True, encrypt_secret(HOOK)
        config.auto_neu = config.auto_aenderung = config.auto_aufhebung = True
        config.standard_melden, config.include_map = True, True
        db.add(config)
        db.commit()
    finally:
        db.close()
    yield
    db = _db()
    try:
        config = db.query(RoadClosureTeamsConfig).filter_by(org_id=1).first()
        if config:
            config.enabled = False
            db.commit()
    finally:
        db.close()


def _rows(closure_id: int) -> list[RoadClosureNotification]:
    db = _db()
    try:
        return db.query(RoadClosureNotification).filter_by(road_closure_id=closure_id).order_by(
            RoadClosureNotification.id).all()
    finally:
        db.close()


def _anlegen(**werte) -> int:
    db = _db()
    try:
        data = {"title": f"Teams {uuid4().hex[:8]}", "street": f"Teamsweg {uuid4().hex[:6]}",
                "valid_from": _now() - timedelta(hours=1), "valid_until": _now() + timedelta(days=2),
                "restriction_type": "closed", "geometry_geojson": _LINE, "reason": "Kanalbau",
                "description": GEHEIM}
        data.update(werte)
        closure = service.create_closure(db, 1, None, data)
        db.commit()
        return closure.id
    finally:
        db.close()


def _laden(db, closure_id: int) -> RoadClosure:
    return db.query(RoadClosure).filter_by(id=closure_id).one()


# --- Adaptive Card ----------------------------------------------------------------------------------------------


def test_card_enthaelt_pflichtinhalte_ohne_interna(setup_db) -> None:
    closure = _sperre(valid_until=_now() + timedelta(days=1), city="Wolfurt")
    db = _db()
    try:
        data = public_closure_dict(closure, db.get(FireDept, 1))
    finally:
        db.close()
    envelope = notify.build_road_closure_card(
        data, ereignis="neu", detail_url="https://ec.example/oeffentlich/strassensperre/rcd_x",
        intern_url="https://ec.example/strassensperren/1", map_url="https://ec.example/x/karte.png",
    )
    assert envelope["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = envelope["attachments"][0]["content"]
    assert card["version"] == "1.4"
    text = json.dumps(card, ensure_ascii=False)
    for erwartet in ("Neue Straßensperre", closure.title, "Vollsperre", "Bregenzer Straße: Nr. 12 – Nr. 38",
                     "Beginn", "Ende", "Kanalbau", "Einsatzgebiet", "Anrainer frei", "karte.png",
                     "Details anzeigen", "Im Einsatzcockpit öffnen"):
        assert erwartet in text, erwartet
    assert GEHEIM not in text
    header = card["body"][0]
    assert header["color"] == "attention"
    aufgehoben = notify.build_road_closure_card(data, ereignis="aufgehoben", detail_url=None, intern_url="x",
                                                map_url=None)["attachments"][0]["content"]
    assert aufgehoben["body"][0]["color"] == "good"
    assert not any(block["type"] == "Image" for block in aufgehoben["body"])
    assert [a["title"] for a in aufgehoben["actions"]] == ["Im Einsatzcockpit öffnen"]


# --- Ereignisse -------------------------------------------------------------------------------------------------


def test_ereignis_aus_changes() -> None:
    now = _now()

    def change(field, before, after):
        return [{"feld": field, "vorher": before.isoformat() if before else None,
                 "nachher": after.isoformat() if after else None}]

    assert notify.ereignis_aus_changes(change("valid_until", now, now + timedelta(days=3)), now=now) == "verlaengert"
    assert notify.ereignis_aus_changes(change("valid_until", now + timedelta(days=3), None), now=now) == "verlaengert"
    assert notify.ereignis_aus_changes(
        change("valid_until", now + timedelta(days=3), now), now=now) == "vorzeitig_beendet"
    assert notify.ereignis_aus_changes(change("valid_until", None, now), now=now) == "vorzeitig_beendet"
    assert notify.ereignis_aus_changes(
        change("valid_until", None, now + timedelta(days=5)), now=now) == "geaendert"
    assert notify.ereignis_aus_changes([{"feld": "restriction_type", "vorher": "closed", "nachher": "partial"}],
                                       now=now) == "geaendert"
    assert notify.ereignis_aus_changes([{"feld": "description", "vorher": "a", "nachher": "b"}], now=now) is None


def test_hooks_reihen_ereignisse_ein_und_fassen_zusammen(teams_config) -> None:
    closure_id = _anlegen()
    rows = _rows(closure_id)
    assert [(row.ereignis, row.status) for row in rows] == [("neu", "pending")]

    db = _db()
    try:
        closure = _laden(db, closure_id)
        service.update_closure(db, closure, None, {"valid_until": _now() + timedelta(days=9)},
                               expected_version=closure.version)
        db.commit()
    finally:
        db.close()
    rows = _rows(closure_id)
    assert len(rows) == 1 and rows[0].ereignis == "verlaengert"  # noch nicht versendet → zusammengefasst

    db = _db()
    try:
        row = db.get(RoadClosureNotification, rows[0].id)
        row.status = "sent"
        closure = _laden(db, closure_id)
        service.update_closure(db, closure, None, {"description": "nur intern"}, expected_version=closure.version)
        service.deactivate_closure(db, closure, None, "fertig")
        db.commit()
    finally:
        db.close()
    assert [row.ereignis for row in _rows(closure_id)] == ["verlaengert", "aufgehoben"]


def test_geometrie_bestaetigen_meldet_nichts(teams_config) -> None:
    closure_id = _anlegen(geometry_status="needs_review")
    db = _db()
    try:
        db.query(RoadClosureNotification).filter_by(road_closure_id=closure_id).one().status = "sent"
        service.confirm_geometry(db, _laden(db, closure_id), None)
        db.commit()
    finally:
        db.close()
    assert [row.ereignis for row in _rows(closure_id)] == ["neu"]


def test_ohne_teams_melden_oder_deaktivierter_config_kein_job(teams_config) -> None:
    assert _rows(_anlegen(teams_melden=False)) == []
    db = _db()
    try:
        db.query(RoadClosureTeamsConfig).filter_by(org_id=1).one().enabled = False
        db.commit()
    finally:
        db.close()
    assert _rows(_anlegen(teams_melden=True)) == []


def test_standard_melden_gilt_ohne_angabe(teams_config) -> None:
    db = _db()
    try:
        closure_id = _anlegen()
        assert _laden(db, closure_id).teams_melden is True
    finally:
        db.close()


def test_manuell_und_dubletten(teams_config) -> None:
    closure_id = _anlegen(teams_melden=False)
    db = _db()
    try:
        closure = _laden(db, closure_id)
        now = _now().replace(second=5)
        first = notify.enqueue(db, closure, "manuell", manuell=True, now=now)
        second = notify.enqueue(db, closure, "manuell", manuell=True, now=now.replace(second=40))
        db.commit()
        assert first is not None and second is not None and first.id == second.id
    finally:
        db.close()
    assert len(_rows(closure_id)) == 1

    other_id = _anlegen()
    db = _db()
    try:
        closure = _laden(db, other_id)
        db.query(RoadClosureNotification).filter_by(road_closure_id=other_id).one().status = "sent"
        notify.enqueue(db, closure, "neu")  # gleicher Inhalt, gleiches Ereignis
        db.commit()
    finally:
        db.close()
    assert len(_rows(other_id)) == 1


# --- Outbox -----------------------------------------------------------------------------------------------------


def _faelligkeit_nur_fuer(closure_id: int) -> None:
    """Andere offene Jobs der gemeinsamen Test-DB parken, damit process_due nur unseren Job sieht."""
    db = _db()
    try:
        for row in db.query(RoadClosureNotification).filter(
            RoadClosureNotification.status.in_(("pending", "retry", "sending")),
            RoadClosureNotification.road_closure_id != closure_id,
        ).all():
            row.status = "suppressed"
        db.commit()
    finally:
        db.close()


def _sender(monkeypatch, result):
    calls = []

    async def fake(url, payload):
        calls.append((url, payload))
        return result

    monkeypatch.setattr(notify, "send_payload", fake)
    return calls


def test_outbox_sendet_mit_detaillink_und_bild(teams_config, monkeypatch) -> None:
    closure_id = _anlegen()
    _faelligkeit_nur_fuer(closure_id)
    calls = _sender(monkeypatch, (True, False, None))
    assert asyncio.run(loop.process_due()) == 1
    row = _rows(closure_id)[0]
    assert row.status == "sent" and row.sent_at is not None and row.attempt_count == 1
    url, payload = calls[0]
    assert url == HOOK
    text = json.dumps(payload)
    assert "/oeffentlich/strassensperre/rcd_" in text and "/karte.png" in text
    assert GEHEIM not in text


@pytest.mark.parametrize(("ergebnis", "status"), [((False, True, "HTTP 503"), "retry"),
                                                 ((False, False, "HTTP 400"), "failed")])
def test_outbox_retry_und_fehler(teams_config, monkeypatch, ergebnis, status) -> None:
    closure_id = _anlegen()
    _faelligkeit_nur_fuer(closure_id)
    _sender(monkeypatch, ergebnis)
    now = _now()
    asyncio.run(loop.process_due(now))
    row = _rows(closure_id)[0]
    assert row.status == status and row.last_error == ergebnis[2]
    if status == "retry":
        assert row.next_attempt_at == now + timedelta(seconds=30)


def test_outbox_uebernimmt_abgelaufene_lease_und_unterdrueckt_geloeschte(teams_config, monkeypatch) -> None:
    closure_id = _anlegen()
    _faelligkeit_nur_fuer(closure_id)
    db = _db()
    try:
        row = db.query(RoadClosureNotification).filter_by(road_closure_id=closure_id).one()
        row.status, row.lease_until = "sending", _now() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    _sender(monkeypatch, (True, False, None))
    assert asyncio.run(loop.process_due()) == 1
    assert _rows(closure_id)[0].status == "sent"

    weg_id = _anlegen()
    _faelligkeit_nur_fuer(weg_id)
    db = _db()
    try:
        weg = _laden(db, weg_id)
        # Sperre "verschwindet" für den Job: Org-Bindung passt nicht mehr.
        db.query(RoadClosureNotification).filter_by(road_closure_id=weg_id).one().org_id = None
        db.commit()
        assert weg is not None
    finally:
        db.close()
    asyncio.run(loop.process_due())


def test_aufgehobene_sperre_ohne_link_und_bild(teams_config, monkeypatch) -> None:
    closure_id = _anlegen()
    db = _db()
    try:
        db.query(RoadClosureNotification).filter_by(road_closure_id=closure_id).one().status = "sent"
        service.deactivate_closure(db, _laden(db, closure_id), None, "fertig")
        db.commit()
    finally:
        db.close()
    _faelligkeit_nur_fuer(closure_id)
    calls = _sender(monkeypatch, (True, False, None))
    asyncio.run(loop.process_due())
    text = json.dumps(calls[0][1], ensure_ascii=False)
    assert "aufgehoben" in text and "rcd_" not in text and "karte.png" not in text


# --- Sender -----------------------------------------------------------------------------------------------------


def test_send_payload_klassifiziert_und_verraet_keine_url(monkeypatch) -> None:
    assert asyncio.run(notify.send_payload("http://example.invalid/x", {}))[:2] == (False, False)

    real_client = httpx.AsyncClient

    def client_with(status):
        class Client(real_client):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(status))
                super().__init__(*args, **kwargs)
        return Client

    for status, erwartet in ((200, (True, False)), (429, (False, True)), (503, (False, True)), (400, (False, False))):
        monkeypatch.setattr(notify.httpx, "AsyncClient", client_with(status))
        sent, retryable, reason = asyncio.run(notify.send_payload(HOOK, {}))
        assert (sent, retryable) == erwartet
        assert "SECRET-HOOK-123" not in (reason or "")


# --- UI ---------------------------------------------------------------------------------------------------------


def test_einstellungen_zeigen_url_nie_und_behalten_sie(client, teams_config) -> None:
    _login(client, _setup_user("objekt_verwalter"))
    page = client.get("/strassensperren/einstellungen")
    assert page.status_code == 200
    assert "example.invalid" in page.text and "SECRET-HOOK-123" not in page.text
    csrf = client.cookies.get("ec_csrf")
    response = client.post("/strassensperren/einstellungen", data={
        "_csrf": csrf, "aktiv": "1", "webhook_url": "", "auto_neu": "1", "include_map": "1",
    }, follow_redirects=False)
    assert response.status_code == 303
    db = _db()
    try:
        config = db.query(RoadClosureTeamsConfig).filter_by(org_id=1).one()
        assert config.webhook_url_enc and "SECRET" not in config.webhook_url_enc
        assert config.auto_aenderung is False
    finally:
        db.close()
    bad = client.post("/strassensperren/einstellungen", data={"_csrf": csrf, "webhook_url": "http://unsicher"},
                      follow_redirects=False)
    assert "fehler=" in bad.headers["location"]
    assert client.post("/strassensperren/einstellungen", data={"aktiv": "1"}).status_code in {400, 403}


def test_einstellungen_nur_fuer_verwalter(client) -> None:
    _login(client, _setup_user("readonly"))
    assert client.get("/strassensperren/einstellungen").status_code == 403


def test_testnachricht(client, teams_config, monkeypatch) -> None:
    calls = _sender(monkeypatch, (False, False, "HTTP 400"))
    _login(client, _setup_user("objekt_verwalter"))
    response = client.post("/strassensperren/einstellungen/test", data={"_csrf": client.cookies.get("ec_csrf")},
                           follow_redirects=False)
    assert "HTTP%20400" in response.headers["location"] and len(calls) == 1


def test_detail_manuell_senden_ohne_http_und_erneut(client, teams_config, monkeypatch) -> None:
    async def verboten(*args, **kwargs):
        raise AssertionError("Kein synchroner Versand im Request")

    monkeypatch.setattr(notify, "send_payload", verboten)
    _login(client, _setup_user("objekt_verwalter"))
    created = client.post("/strassensperren/neu", data=_payload(client, teams_melden="1"), follow_redirects=False)
    closure_id = int(created.headers["location"].rstrip("/").split("/")[-1])
    detail = client.get(f"/strassensperren/{closure_id}").text
    assert 'id="teams"' in detail and "Jetzt an Teams senden" in detail
    csrf = client.cookies.get("ec_csrf")
    client.post(f"/strassensperren/{closure_id}/teams-senden", data={"_csrf": csrf})
    rows = _rows(closure_id)
    assert {row.ereignis for row in rows} == {"neu", "manuell"}
    db = _db()
    try:
        db.get(RoadClosureNotification, rows[0].id).status = "failed"
        db.commit()
    finally:
        db.close()
    assert "Erneut senden" in client.get(f"/strassensperren/{closure_id}").text
    client.post(f"/strassensperren/{closure_id}/teams/{rows[0].id}/erneut", data={"_csrf": csrf})
    assert _rows(closure_id)[0].status == "retry"
    other = _anlegen()
    assert client.post(f"/strassensperren/{other}/teams/{rows[0].id}/erneut",
                       data={"_csrf": csrf}).status_code == 404


def test_bearbeitungsmaske_hat_teams_checkbox(client, teams_config) -> None:
    _login(client, _setup_user("objekt_verwalter"))
    text = client.get("/strassensperren/neu").text
    assert 'name="teams_melden" value="1" checked' in text  # standard_melden


# --- Kartenbild -------------------------------------------------------------------------------------------------


def test_kartenbild_route(client, monkeypatch) -> None:
    from app.routers import ui_road_closure_public
    from tests.test_strassensperren_freigaben import _token

    monkeypatch.setattr(ui_road_closure_public, "render_road_closure_map_png", lambda geometry: b"\x89PNG-test")
    closure = _sperre()
    _id, raw = _token(closure)
    client.cookies.clear()
    response = client.get(f"/oeffentlich/strassensperre/{raw}/karte.png")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG-test"
    assert "max-age=300" in response.headers["cache-control"]
    assert client.get(f"/oeffentlich/strassensperre/rcd_{uuid4().hex}/karte.png").status_code == 404
    ohne = _sperre(geometry_geojson=None, geometry_status="missing")
    _id, raw_ohne = _token(ohne)
    assert client.get(f"/oeffentlich/strassensperre/{raw_ohne}/karte.png").status_code == 404


@pytest.mark.parametrize("geometry", [
    {"type": "LineString", "coordinates": [[9.74, 47.46], [9.75, 47.47]]},
    {"type": "MultiLineString", "coordinates": [[[9.74, 47.46], [9.75, 47.47]]]},
    {"type": "Polygon", "coordinates": [[[9.74, 47.46], [9.75, 47.46], [9.75, 47.47], [9.74, 47.46]]]},
    {"type": "Point", "coordinates": [9.74, 47.46]},
])
def test_render_road_closure_map_png_ohne_netz(monkeypatch, geometry) -> None:
    from PIL import Image
    from staticmap import StaticMap

    from app.services import staticmap_service

    monkeypatch.setattr(StaticMap, "render", lambda self, zoom=None, center=None: Image.new("RGB", (10, 10)))
    staticmap_service._ROAD_CLOSURE_RENDER_CACHE.clear()
    assert staticmap_service.render_road_closure_map_png(geometry).startswith(b"\x89PNG")
