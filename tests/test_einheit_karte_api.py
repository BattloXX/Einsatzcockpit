"""Regressionen für Reihenfolge, Navigation und Karten-Zugang der Einheitenkarte."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.security import sign_session
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, SitePriority
from app.models.user import DeviceToken, User
from app.routers import ui_map_tiles
from app.services import gk_zugang_service
from app.services.einheit_service import auftraege_fuer_einheit, kontext_fuer_geraet
from tests.test_einheit_api import _als, _daten, _db, _device, _h
from tests.test_einheit_zugang import _zugang_mit_auftrag


def test_zustand_liefert_rang_naechsten_und_navigation(client, setup_db):
    x = _daten()
    db = _db()
    einheit = db.get(LageEinheit, x["e1"])
    a = db.get(IncidentSite, x["a"])
    a.priority = SitePriority.normal
    einheit.incident_site_id = a.id
    db.get(EinheitSiteDispatch, x["d1"]).einheit_status = "in_arbeit"
    b = IncidentSite(major_incident_id=x["lage"], org_id=a.org_id, bezeichnung="Ohne Position", strasse="Hauptstraße 1")
    c = IncidentSite(major_incident_id=x["lage"], org_id=a.org_id, bezeichnung="Sofort", lat=46, lng=12,
                     priority=SitePriority.sofort)
    db.add_all([b, c])
    db.flush()
    d2 = EinheitSiteDispatch(einheit_id=x["e1"], site_id=b.id, dispatched_at=datetime.now(UTC) - timedelta(minutes=3))
    d3 = EinheitSiteDispatch(einheit_id=x["e1"], site_id=c.id, dispatched_at=datetime.now(UTC) - timedelta(minutes=2))
    fertig = EinheitSiteDispatch(
        einheit_id=x["e1"], site_id=b.id, dispatched_at=datetime.now(UTC), beendet_at=datetime.now(UTC)
    )
    zurueckgezogen = EinheitSiteDispatch(
        einheit_id=x["e1"], site_id=c.id, dispatched_at=datetime.now(UTC), withdrawn_at=datetime.now(UTC)
    )
    db.add_all([d2, d3, fertig, zurueckgezogen])
    db.commit()
    ctx = kontext_fuer_geraet(db, db.get(User, x["u1"]), db.get(DeviceToken, x["t1"]))
    daten = auftraege_fuer_einheit(db, ctx)
    weitere = daten["kategorien"]["weitere"]
    assert daten["kategorien"]["aktuell"]["dispatch_id"] == x["d1"]
    assert [item["dispatch_id"] for item in weitere] == [d3.id, d2.id]
    assert daten["naechster_dispatch_id"] == d3.id
    assert daten["kategorien"]["aktuell"]["rang"] == 1
    assert weitere[0]["rang"] == 2 and weitere[0]["naechster"] is True
    assert weitere[0]["hat_koordinaten"] is True and "destination=46.0,12.0" in weitere[0]["nav_url"]
    ohne_position = next(item for item in weitere if item["dispatch_id"] == d2.id)
    assert ohne_position["hat_koordinaten"] is False and "Hauptstra%C3%9Fe" in ohne_position["nav_url"]
    assert daten["kategorien"]["abgeschlossen"][0]["rang"] is None
    assert daten["kategorien"]["zurueckgezogen"][0]["rang"] is None
    db.close()
    _device(client, x)
    assert client.get("/einheit/api/zustand").json()["naechster_dispatch_id"] == d3.id


def test_einheit_karte_template_ist_versioniert_und_ohne_smart_quotes(client, setup_db):
    x = _daten()
    _device(client, x)
    html = client.get("/einheit").text
    assert '"/static/js/einheit_karte.js": "/static/js/einheit_karte.js?v=' in html
    for text in ("Mein Standort", "Fertige einblenden", "Linie = Luftlinie, keine Route"):
        assert text in html
    assert not any(quote in html for quote in "“”„‘’")


def test_tile_proxy_liefert_kacheln_auch_fuer_gk_sitzungen(client, setup_db):
    # Der Tile-Proxy ist bewusst öffentlich (Infoscreen per Token, Bürgermeldung, Alarm-Zusammenfassung);
    # die Einheitenkarte einer GK-Sitzung muss ihn ebenfalls nutzen können.
    cookie, _, _, _ = _zugang_mit_auftrag()
    ui_map_tiles._tile_cache.clear()
    client.cookies.set(gk_zugang_service.COOKIE, cookie)
    response = MagicMock(content=b"png", headers={})
    response.raise_for_status.return_value = None
    upstream = MagicMock(get=AsyncMock(return_value=response))
    context = MagicMock(__aenter__=AsyncMock(return_value=upstream), __aexit__=AsyncMock(return_value=None))
    with patch("app.routers.ui_map_tiles.httpx.AsyncClient", return_value=context):
        assert client.get("/karten/osm-tile/1/1/0.png").status_code == 200


def test_einheit_freigeben_broadcastet_einheit_aenderung(client, setup_db, monkeypatch):
    x = _daten()
    _als(client, sign_session(x["recorder"]))
    client.get(f"/lage/{x['lage']}")
    events = []

    async def senden(lage_id, event):
        events.append((lage_id, event))

    monkeypatch.setattr("app.routers.ui_major_incident.broadcast_lage", senden)
    response = client.post(
        f"/lage/{x['lage']}/stellen/{x['a']}/einheit-freigeben",
        data={"einheit_id": x["e1"]}, headers=_h(client),
    )
    assert response.status_code == 204
    assert (x["lage"], {"type": "einheit:changed", "einheit_id": x["e1"], "site_id": x["a"]}) in events


def test_reihenfolge_der_leitstelle_schlaegt_prioritaet_und_ohne_adresse_kein_navlink(client, setup_db):
    x = _daten()
    db = _db()
    a = db.get(IncidentSite, x["a"])
    for dispatch in db.query(EinheitSiteDispatch).filter_by(einheit_id=x["e1"]).all():
        dispatch.withdrawn_at = datetime.now(UTC)
    sofort = IncidentSite(major_incident_id=x["lage"], org_id=a.org_id, bezeichnung="Sofort", lat=46, lng=12,
                          priority=SitePriority.sofort)
    normal = IncidentSite(major_incident_id=x["lage"], org_id=a.org_id, bezeichnung="Normal vorgegeben",
                          priority=SitePriority.normal)
    db.add_all([sofort, normal])
    db.flush()
    d_sofort = EinheitSiteDispatch(einheit_id=x["e1"], site_id=sofort.id, dispatched_at=datetime.now(UTC))
    d_normal = EinheitSiteDispatch(einheit_id=x["e1"], site_id=normal.id, reihenfolge=1,
                                   dispatched_at=datetime.now(UTC))
    db.add_all([d_sofort, d_normal])
    db.commit()
    ctx = kontext_fuer_geraet(db, db.get(User, x["u1"]), db.get(DeviceToken, x["t1"]))
    daten = auftraege_fuer_einheit(db, ctx)
    weitere = daten["kategorien"]["weitere"]
    # Vorgabe der Leitstelle (reihenfolge) hat Vorrang vor der Priorität.
    assert [item["dispatch_id"] for item in weitere] == [d_normal.id, d_sofort.id]
    assert weitere[0]["naechster"] is True and weitere[0]["rang"] == 1
    assert weitere[0]["hat_koordinaten"] is False and weitere[0]["nav_url"] is None
    db.close()


def test_permissions_policy_erlaubt_standort_nur_eigener_origin(client, setup_db):
    # „Mein Standort“ (Einheitenkarte, GSL-Karte, Bürgermeldung) braucht geolocation=(self);
    # vorher war Geolocation per geolocation=() auf allen Seiten gesperrt.
    policy = client.get("/login").headers["Permissions-Policy"]
    assert "geolocation=(self)" in policy and "payment=()" in policy


def test_geojson_liefert_bestehende_auftragsart_ohne_zweite_klassifikation(client, setup_db):
    x = _daten()
    with _db() as db:
        db.get(EinheitSiteDispatch, x["d1"]).auftragsart = "erkundung"
        bestehender = EinheitSiteDispatch(
            einheit_id=x["e1"], site_id=x["a"], dispatched_at=datetime.now(UTC),
        )
        db.add(bestehender)
        db.commit()
        default_id = bestehender.id
    _device(client, x)
    features = client.get("/einheit/api/karte").json()["features"]
    assert {f["properties"]["dispatch_id"]: f["properties"]["auftragsart"] for f in features} == {
        x["d1"]: "erkundung", default_id: "abarbeitung",
    }
    assert client.get(f"/einheit/api/auftrag/{x['d2']}").status_code == 404
