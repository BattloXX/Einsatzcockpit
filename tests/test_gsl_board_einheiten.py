"""Tests für Einheiten-Chips auf Board und Kräfteübersicht."""
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit
from app.models.master import OrgSettings
from app.services.resource_service import get_einheiten_chips_for_sites
from tests.test_einheit_api import _als, _daten


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def test_einheiten_chips_status_reihenfolge_fertig_und_rueckmeldung():
    x = _daten()
    db = _db()
    try:
        site = db.get(IncidentSite, x["a"])
        d_tlf = db.get(EinheitSiteDispatch, x["d1"])
        d_rlf = db.query(EinheitSiteDispatch).filter(
            EinheitSiteDispatch.site_id == site.id,
            EinheitSiteDispatch.einheit_id != d_tlf.einheit_id,
        ).one()
        retired = LageEinheit(lage_id=x["lage"], label="KDO")
        db.add(retired)
        db.flush()
        now = datetime.now(UTC).replace(tzinfo=None)
        d_tlf.einheit_status = "anfahrt"
        d_tlf.dispatched_at = now - timedelta(minutes=50)
        d_tlf.status_at = now - timedelta(minutes=45)
        d_tlf.letzte_rueckmeldung_at = now - timedelta(minutes=40)
        d_rlf.einheit_status = "abgeschlossen"
        d_rlf.beendet_at = now - timedelta(minutes=5)
        db.add(EinheitSiteDispatch(
            einheit_id=retired.id,
            site_id=site.id,
            dispatched_at=now,
            withdrawn_at=now,
        ))
        settings = OrgSettings(org_id=site.org_id, gsl_lagemeldung_interval_minutes=30)
        db.add(settings)
        db.flush()

        chips = get_einheiten_chips_for_sites(db, [site], org_settings=settings, jetzt=now)[site.id]

        assert [chip["label"] for chip in chips["chips"]] == ["TLF", "RLF"]
        assert chips["chips"][0]["label_status"] == "Anfahrt"
        assert chips["chips"][1]["beendet"] is True
        assert chips["alle_fertig"] is False
        assert chips["ohne_rueckmeldung_min"] == 40

        d_tlf.einheit_status = "abgeschlossen"
        d_tlf.beendet_at = now
        all_done = get_einheiten_chips_for_sites(db, [site], org_settings=settings, jetzt=now)[site.id]
        assert all_done["alle_fertig"] is True
        assert all_done["ohne_rueckmeldung_min"] is None
        assert get_einheiten_chips_for_sites(db, [site], jetzt=now)[site.id]["ohne_rueckmeldung_min"] is None
    finally:
        db.close()


def test_board_und_einzelkarte_zeigen_einheiten_chips(client):
    x = _daten()
    _als(client, sign_session(x["admin"]))

    board = client.get(f"/lage/{x['lage']}")
    card = client.get(f"/lage/{x['lage']}/stellen/{x['a']}/card")

    assert board.status_code == card.status_code == 200
    assert "TLF · Zugewiesen" in board.text
    assert "TLF · Zugewiesen" in card.text

    db = _db()
    try:
        dispatches = db.query(EinheitSiteDispatch).filter(
            EinheitSiteDispatch.site_id == x["a"],
            EinheitSiteDispatch.withdrawn_at.is_(None),
        ).all()
        for dispatch in dispatches:
            dispatch.einheit_status = "abgeschlossen"
            dispatch.beendet_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    finally:
        db.close()

    finished = client.get(f"/lage/{x['lage']}/stellen/{x['a']}/card")
    assert "Einheiten fertig – abschließen?" in finished.text


def test_kraefteuebersicht_status_und_sim_link_nur_admin(client):
    x = _daten()
    _als(client, sign_session(x["admin"]))
    admin = client.get(f"/lage/{x['lage']}/ressourcen/kraefteuebersicht")
    assert admin.status_code == 200
    assert "Zugewiesen" in admin.text
    assert f'/einheit?sim={x["e1"]}' in admin.text

    _als(client, sign_session(x["recorder"]))
    recorder = client.get(f"/lage/{x['lage']}/ressourcen/kraefteuebersicht")
    assert recorder.status_code == 200
    assert f'/einheit?sim={x["e1"]}' not in recorder.text
