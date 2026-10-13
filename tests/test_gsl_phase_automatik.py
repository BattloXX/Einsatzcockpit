"""Tests for the event-driven GSL site phase automation."""
# ruff: noqa: E701, E702, I001
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    EinheitSiteDispatch, IncidentSite, LageEinheit, LageJournalEntry, MajorIncident,
    MajorIncidentStatus, SitePhase,
)
from app.models.master import OrgSettings
from app.services import resource_service as rs
from app.services.einheit_service import (
    EinheitKonflikt, einheit_status_label, kontext_fuer_einheit, setze_einheit_status,
)
from app.services.gsl_phase_automatik import aktualisiere_site_phase, einsatz_abschliessen, einsatz_wiedereroeffnen
from tests.conftest import TestingSession
from tests.test_einheit_api import _als, _daten as _api_daten, _device, _h
from tests.test_gsl_fuehrung_einheiten import _mit_rolle, _post


@contextmanager
def _session():
    db = TestingSession(); set_tenant_context(db, 1)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _lage(db):
    x = MajorIncident(
        name=f"P-{uuid4().hex}", org_id=1, status=MajorIncidentStatus.active, started_at=datetime.now(UTC),
    )
    db.add(x); db.flush(); return x


def _site(db, lage, phase=SitePhase.eingegangen):
    x = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung=f"S-{uuid4().hex}", phase=phase)
    db.add(x); db.flush(); return x


def _unit(db, lage):
    x = LageEinheit(
        lage_id=lage.id, label=f"E-{uuid4().hex}", resource_type="fahrzeug", status=rs.STATUS_BEREITGESTELLT,
    )
    db.add(x); db.flush(); return x


def _enable_lagemeldung_timer(db):
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    if settings is None:
        settings = OrgSettings(org_id=1)
        db.add(settings)
        db.flush()
    settings.gsl_lagemeldung_interval_minutes = 60
    db.flush()


def _status(db, unit, dispatch, status, quelle="funk", grund=None):
    from app.models.user import User
    lage = db.get(MajorIncident, unit.lage_id)
    user = User(username=f"p-{uuid4().hex}", display_name="P", org_id=lage.org_id, active=True)
    db.add(user); db.flush()
    ctx = kontext_fuer_einheit(db, user, unit.id, quelle=quelle)
    return setze_einheit_status(db, ctx, dispatch, status, user_id=1, author_name="P", grund=grund)


def _entries(db, site, typ):
    db.flush()
    return db.query(LageJournalEntry).filter_by(site_id=site.id, ereignis_typ=typ).all()


def test_erkundung_bewertung_und_abarbeitung_phasen():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage)
        a, b, c = _unit(db, lage), _unit(db, lage), _unit(db, lage)
        da = rs.dispatch_to_site(db, a.id, lage.id, site.id, auftragsart="erkundung")
        db_ = rs.dispatch_to_site(db, b.id, lage.id, site.id, auftragsart="erkundung")
        _status(db, a, da, "abgeschlossen"); assert site.phase == SitePhase.erkundung
        _status(db, b, db_, "abgeschlossen"); assert site.phase == SitePhase.bewertet
        rs.dispatch_to_site(db, c.id, lage.id, site.id); assert site.phase == SitePhase.disponiert


def test_tablet_abarbeitung_nach_erkundung_wird_fertig_gemeldet():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage)
        erkunder, abarbeitung = _unit(db, lage), _unit(db, lage)
        erkundung = rs.dispatch_to_site(db, erkunder.id, lage.id, site.id, auftragsart="erkundung")
        _status(db, erkunder, erkundung, "abgeschlossen", quelle="tablet")
        assert site.phase == SitePhase.bewertet
        dispatch = rs.dispatch_to_site(db, abarbeitung.id, lage.id, site.id, auftragsart="abarbeitung")
        _status(db, abarbeitung, dispatch, "in_arbeit", quelle="tablet")
        _status(db, abarbeitung, dispatch, "abgeschlossen", quelle="tablet")
        assert site.phase == SitePhase.fertig_gemeldet


def test_new_site_stays_eingegangen_without_dispatches():
    with _session() as db:
        site = _site(db, _lage(db)); assert not aktualisiere_site_phase(db, site, ausloeser="T", quelle="manuell")
        assert site.phase == SitePhase.eingegangen


def test_erkundung_dispatch_sets_phase_and_journal():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        rs.dispatch_to_site(db, unit.id, lage.id, site.id, auftragsart="erkundung")
        assert site.phase == SitePhase.erkundung and _entries(db, site, "erkundung_zugewiesen")[0].einheit_id == unit.id


def test_two_erkundungen_finish_to_bewertet_with_journal():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); a, b = _unit(db, lage), _unit(db, lage)
        da = rs.dispatch_to_site(db, a.id, lage.id, site.id, auftragsart="erkundung")
        db_ = rs.dispatch_to_site(db, b.id, lage.id, site.id, auftragsart="erkundung")
        _status(db, a, da, "abgeschlossen"); assert site.phase == SitePhase.erkundung
        _status(db, b, db_, "abgeschlossen")
        assert site.phase == SitePhase.bewertet and len(_entries(db, site, "erkundet")) == 2
        assert len(_entries(db, site, "bewertet")) == 1
        assert einheit_status_label("erkundung", "abgeschlossen") == "Erkundet"


def test_finished_and_withdrawn_erkundung_becomes_bewertet():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); a, b = _unit(db, lage), _unit(db, lage)
        da = rs.dispatch_to_site(db, a.id, lage.id, site.id, auftragsart="erkundung")
        rs.dispatch_to_site(db, b.id, lage.id, site.id, auftragsart="erkundung")
        _status(db, a, da, "abgeschlossen"); rs.withdraw_from_site(db, b.id, lage.id, site.id, auftragsart="erkundung")
        assert site.phase == SitePhase.bewertet


def test_abarbeitung_statuses_progress_to_in_arbeit():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id); assert site.phase == SitePhase.disponiert
        _status(db, unit, dispatch, "bestaetigt"); _status(db, unit, dispatch, "anfahrt")
        assert site.phase == SitePhase.disponiert; _status(db, unit, dispatch, "vor_ort")
        assert site.phase == SitePhase.in_arbeit


def test_timer_starts_on_confirmation_and_stops_after_last_dispatch():
    with _session() as db:
        _enable_lagemeldung_timer(db)
        lage, site = _lage(db), None; site = _site(db, lage)
        unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id)
        assert site.phase == SitePhase.disponiert
        assert site.naechste_lagemeldung_at is None
        _status(db, unit, dispatch, "bestaetigt")
        assert site.naechste_lagemeldung_at is not None
        _status(db, unit, dispatch, "vor_ort")
        _status(db, unit, dispatch, "abgeschlossen")
        assert site.naechste_lagemeldung_at is None


def test_abarbeitung_direct_in_arbeit_is_allowed():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id); _status(db, unit, dispatch, "in_arbeit")
        assert site.phase == SitePhase.in_arbeit


def test_erkundung_cannot_move_to_in_arbeit():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id, auftragsart="erkundung")
        with pytest.raises(EinheitKonflikt, match="ungueltiger_uebergang"):
            _status(db, unit, dispatch, "in_arbeit")


def test_two_abarbeitungen_finish_once():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); a, b = _unit(db, lage), _unit(db, lage)
        da, db_ = rs.dispatch_to_site(db, a.id, lage.id, site.id), rs.dispatch_to_site(db, b.id, lage.id, site.id)
        _status(db, a, da, "vor_ort"); _status(db, a, da, "abgeschlossen"); assert site.phase == SitePhase.in_arbeit
        _status(db, b, db_, "nicht_durchfuehrbar", grund="X")
        assert site.phase == SitePhase.fertig_gemeldet and len(_entries(db, site, "alle_fertig")) == 1


def test_finished_site_new_or_reopened_auftrag_is_disponiert():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); a, b = _unit(db, lage), _unit(db, lage)
        da = rs.dispatch_to_site(db, a.id, lage.id, site.id); _status(db, a, da, "nicht_durchfuehrbar", grund="X")
        rs.dispatch_to_site(db, b.id, lage.id, site.id); assert site.phase == SitePhase.disponiert
        rs.withdraw_from_site(db, b.id, lage.id, site.id); rs.oeffne_auftrag_wieder(db, da, author_name="P", user_id=1)
        assert site.phase == SitePhase.disponiert


def test_withdrawn_abarbeitungen_without_erkundung_leave_phase():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        rs.dispatch_to_site(db, unit.id, lage.id, site.id); rs.withdraw_from_site(db, unit.id, lage.id, site.id)
        assert site.phase == SitePhase.disponiert
        assert not aktualisiere_site_phase(db, site, ausloeser="T", quelle="manuell")


def test_withdrawn_abarbeitung_after_erkundung_becomes_bewertet():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); a, b = _unit(db, lage), _unit(db, lage)
        da = rs.dispatch_to_site(db, a.id, lage.id, site.id, auftragsart="erkundung")
        _status(db, a, da, "abgeschlossen")
        rs.dispatch_to_site(db, b.id, lage.id, site.id); rs.withdraw_from_site(db, b.id, lage.id, site.id)
        assert site.phase == SitePhase.bewertet


def test_same_unit_can_have_both_kinds_but_not_duplicate():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        erk = rs.dispatch_to_site(db, unit.id, lage.id, site.id, auftragsart="erkundung")
        ab = rs.dispatch_to_site(db, unit.id, lage.id, site.id)
        with pytest.raises(ValueError): rs.dispatch_to_site(db, unit.id, lage.id, site.id)
        rs.withdraw_from_site(db, unit.id, lage.id, site.id, auftragsart="erkundung")
        assert erk.withdrawn_at is not None and ab.withdrawn_at is None


@pytest.mark.parametrize("phase", [SitePhase.erledigt, SitePhase.abgebrochen])
def test_closed_or_cancelled_site_locks_dispatches(phase):
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id); site.phase = phase
        _status(db, unit, dispatch, "vor_ort"); _status(db, unit, dispatch, "abgeschlossen")
        assert site.phase == phase
        for art in ("abarbeitung", "erkundung"):
            with pytest.raises(ValueError, match="Einsatz zuerst wieder öffnen"):
                rs.dispatch_to_site(db, _unit(db, lage).id, lage.id, site.id, auftragsart=art)


def test_einsatz_abschliessen_withdraws_active_dispatches():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id)
        with pytest.raises(ValueError):
            einsatz_abschliessen(db, site, user=(1, "P"), quelle="manuell", aktive_zurueckziehen=False)
        einsatz_abschliessen(db, site, user=(1, "P"), quelle="manuell", aktive_zurueckziehen=True)
        assert dispatch.withdrawn_grund == "Einsatz abgeschlossen" and site.phase == SitePhase.erledigt
        assert len(_entries(db, site, "site_abgeschlossen")) == 1


def test_einsatz_wiedereroeffnen_calculates_phase_and_journal():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id)
        _status(db, unit, dispatch, "nicht_durchfuehrbar", grund="X")
        site.phase = SitePhase.erledigt; einsatz_wiedereroeffnen(db, site, user=(1, "P"), quelle="manuell")
        assert site.phase == SitePhase.fertig_gemeldet and len(_entries(db, site, "site_wiedereroeffnet")) == 1
        empty = _site(db, lage, SitePhase.erledigt); einsatz_wiedereroeffnen(db, empty, user=(1, "P"), quelle="manuell")
        assert empty.phase == SitePhase.bewertet
        with pytest.raises(ValueError): einsatz_wiedereroeffnen(db, empty, user=(1, "P"), quelle="manuell")


def test_tablet_alle_fertig_journal_has_tablet_quelle():
    with _session() as db:
        lage, site = _lage(db), None; site = _site(db, lage); unit = _unit(db, lage)
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id)
        _status(db, unit, dispatch, "nicht_durchfuehrbar", quelle="tablet", grund="X")
        assert _entries(db, site, "alle_fertig")[0].quelle == "tablet"


def test_route_disponieren_erkundung_and_invalid_art(client):
    x = _api_daten(); _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    url = f"/lage/{x['lage']}/stellen/{x['a']}/einheit-disponieren"
    db = SessionLocal(); set_tenant_context(db, None)
    db.query(EinheitSiteDispatch).filter_by(site_id=x["a"]).update({"withdrawn_at": datetime.now(UTC)})
    db.commit(); db.close()
    assert _post(client, url, {"einheit_id": x["e1"], "auftragsart": "erkundung"}).status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        d = db.query(EinheitSiteDispatch).filter_by(einheit_id=x["e1"], site_id=x["a"])
        d = d.order_by(EinheitSiteDispatch.id.desc()).first()
        assert d.auftragsart == "erkundung" and d.site.phase == SitePhase.erkundung
    finally: db.close()
    assert _post(client, url, {"einheit_id": x["e1"], "auftragsart": "invalid"}).status_code == 400


def test_route_disponieren_closed_site_is_400(client):
    x = _api_daten(); db = SessionLocal(); set_tenant_context(db, None)
    db.get(IncidentSite, x["a"]).phase = SitePhase.erledigt; db.commit(); db.close()
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    r = _post(client, f"/lage/{x['lage']}/stellen/{x['a']}/einheit-disponieren", {"einheit_id": x["e1"]})
    assert r.status_code == 400 and "Einsatz zuerst wieder öffnen" in r.text


def test_route_close_reopen_permissions_and_active_dispatch(client):
    x = _api_daten(); close = f"/lage/{x['lage']}/stellen/{x['a']}/abschliessen"
    _als(client, sign_session(x["recorder"]))
    assert _post(client, close).status_code == 403
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    assert _post(client, close).status_code == 400
    assert _post(client, close, {"aktive_zurueckziehen": "1"}).status_code == 204
    assert _post(client, f"/lage/{x['lage']}/stellen/{x['a']}/wiedereroeffnen").status_code == 204


def test_route_vor_ort_sets_in_arbeit(client):
    x = _api_daten(); _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    url = f"/lage/{x['lage']}/stellen/{x['a']}/einheit-vor-ort"
    assert _post(client, url, {"einheit_id": x["e1"]}).status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try: assert db.get(IncidentSite, x["a"]).phase == SitePhase.in_arbeit
    finally: db.close()


def test_route_withdraw_after_finished_erkundung_becomes_bewertet(client):
    x = _api_daten(); db = SessionLocal(); set_tenant_context(db, None)
    try:
        site, lage = db.get(IncidentSite, x["a"]), db.get(MajorIncident, x["lage"]); unit = _unit(db, lage)
        query = db.query(EinheitSiteDispatch).filter_by(site_id=site.id)
        query.filter(EinheitSiteDispatch.einheit_id != x["e1"]).update({"withdrawn_at": datetime.now(UTC)})
        dispatch = rs.dispatch_to_site(db, unit.id, lage.id, site.id, auftragsart="erkundung")
        _status(db, unit, dispatch, "abgeschlossen")
        db.commit()
    finally: db.close()
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    url = f"/lage/{x['lage']}/stellen/{x['a']}/einheit-abziehen"
    assert _post(client, url, {"einheit_id": x["e1"]}).status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try: assert db.get(IncidentSite, x["a"]).phase == SitePhase.bewertet
    finally: db.close()


def test_tablet_api_finished_erkundung_becomes_bewertet(client):
    x = _api_daten(); db = SessionLocal(); set_tenant_context(db, None)
    db.get(EinheitSiteDispatch, x["d1"]).auftragsart = "erkundung"
    query = db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.site_id == x["a"])
    query.filter(EinheitSiteDispatch.id != x["d1"]).update({"withdrawn_at": datetime.now(UTC)})
    db.commit(); db.close(); _device(client, x)
    r = client.post(
        f"/einheit/api/auftrag/{x['d1']}/status",
        json={"status": "abgeschlossen", "client_uuid": str(uuid4())}, headers=_h(client),
    )
    assert r.status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try: assert db.get(IncidentSite, x["a"]).phase == SitePhase.bewertet
    finally: db.close()


@pytest.mark.parametrize("phase, art, ziel", [
    (SitePhase.eingegangen, "erkundung", SitePhase.erkundung),
    (SitePhase.erkundung, "erkundung", SitePhase.erkundung),
    (SitePhase.bewertet, "abarbeitung", SitePhase.disponiert),
])
def test_route_disponieren_ohne_auftragsart_folgt_der_phase(client, phase, art, ziel):
    x = _api_daten(); _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    db = SessionLocal(); set_tenant_context(db, None)
    for d in db.query(EinheitSiteDispatch).filter_by(site_id=x["a"]).all():
        d.withdrawn_at = datetime.now(UTC)
    db.get(IncidentSite, x["a"]).phase = phase
    db.commit(); db.close()
    url = f"/lage/{x['lage']}/stellen/{x['a']}/einheit-disponieren"
    assert _post(client, url, {"einheit_id": x["e1"], "auftragsart": ""}).status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        d = db.query(EinheitSiteDispatch).filter_by(einheit_id=x["e1"], site_id=x["a"])
        d = d.order_by(EinheitSiteDispatch.id.desc()).first()
        assert d.auftragsart == art and d.site.phase == ziel
    finally: db.close()
