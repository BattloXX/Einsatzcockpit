"""Sicherheitsregressionen für den GK-Zugang im Einheitenmodus."""

from uuid import uuid4

import pytest

from app.models.major_incident import (
    EinheitAktion,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheitZugang,
    SiteLogEntry,
    SitePhase,
)
from app.services import gk_zugang_service
from tests.test_gk_zugang_service import _ausgestellt, _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _zugang_mit_auftrag():
    with _session() as db:
        _, lage, einheit, _, neu = _ausgestellt(db)
        site = IncidentSite(
            major_incident_id=lage.id, org_id=lage.org_id, bezeichnung="Eigene Stelle", phase=SitePhase.disponiert
        )
        db.add(site)
        db.flush()
        dispatch = EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=lage.started_at)
        db.add(dispatch)
        cookie, _ = gk_zugang_service.sitzung_anlegen(
            db, db.get(LageEinheitZugang, neu.zugang_id), user_agent=None, ip=None, verifiziert=True
        )
        db.commit()
        return cookie, dispatch.id, einheit.id, neu.zugang_id


def test_zugang_ist_auf_eigene_einheit_beschraenkt_und_protokolliert(client):
    cookie, dispatch_id, einheit_id, zugang_id = _zugang_mit_auftrag()
    client.cookies.set("ec_gk", cookie)
    assert client.get("/einheit/api/zustand").status_code == 200
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        f"/einheit/api/auftrag/{dispatch_id}/status",
        json={"status": "vor_ort", "client_uuid": str(uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    with _session() as db:
        # The database row is tied to the GK principal, never to a device.
        action = db.query(EinheitAktion).filter_by(zugang_id=zugang_id, einheit_id=einheit_id).one()
        assert action.device_token_id is None
        assert db.query(SiteLogEntry).filter_by(author_name="Max Muster (GK RLF)").first()


def test_widerruf_wird_vor_statuspost_geprueft(client):
    cookie, dispatch_id, _, zugang_id = _zugang_mit_auftrag()
    with _session() as db:
        zugang = db.get(gk_zugang_service.LageEinheitZugang, zugang_id)
        zugang.status = "widerrufen"
        db.commit()
    client.cookies.set("ec_gk", cookie)
    response = client.get("/einheit/api/zustand")
    assert response.status_code == 401
    assert response.json() == {"code": "zugang_widerrufen"}
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        f"/einheit/api/auftrag/{dispatch_id}/status",
        json={"status": "vor_ort", "client_uuid": str(uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 401
