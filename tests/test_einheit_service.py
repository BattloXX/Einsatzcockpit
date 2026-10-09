"""Tests für die Geschäftslogik des GSL-Einheitenmodus."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import (
    IncidentSite,
    LageEinheit,
    MajorIncident,
    MajorIncidentStatus,
    SiteLogEntry,
    SitePhase,
)
from app.models.master import VehicleMaster
from app.models.user import DeviceToken, User
from app.services import resource_service as rs
from app.services.einheit_service import (
    EinheitKonflikt,
    auftraege_fuer_einheit,
    auftrag_laden,
    kontext_fuer_einheit,
    kontext_fuer_geraet,
    setze_einheit_status,
)
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _lage(db, *, status=MajorIncidentStatus.active, started_at=None, org_id=1):
    wert = MajorIncident(name="Test-Lage", org_id=org_id, status=status, started_at=started_at or datetime.now(UTC))
    db.add(wert)
    db.flush()
    return wert


def _site(db, lage, name="Stelle A", phase=SitePhase.disponiert, priority=None):
    wert = IncidentSite(major_incident_id=lage.id, org_id=lage.org_id, bezeichnung=name, phase=phase, priority=priority)
    db.add(wert)
    db.flush()
    return wert


def _einheit(db, lage, vehicle_id=None, label="TLF Wolfurt", status=rs.STATUS_BEREITGESTELLT):
    wert = LageEinheit(lage_id=lage.id, vehicle_id=vehicle_id, label=label, status=status, resource_type="fahrzeug")
    db.add(wert)
    db.flush()
    return wert


def _geraet(db, *, org_id=1, vehicle=True, profil="einheit", revoked=False):
    suffix = str(datetime.now(UTC).timestamp()).replace(".", "")
    user = User(username=f"einheit-{suffix}", display_name="Gerät", org_id=org_id, is_device=True)
    db.add(user)
    db.flush()
    fahrzeug = None
    if vehicle:
        fahrzeug = VehicleMaster(dept_id=org_id, code="TLF", name="Tanklöschfahrzeug", type="TLF")
        db.add(fahrzeug)
        db.flush()
    token = DeviceToken(
        user_id=user.id, label="Tablet", token_hash=f"hash-{suffix}",
        vehicle_master_id=fahrzeug.id if fahrzeug else None, gsl_profil=profil,
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    db.add(token)
    db.flush()
    return user, token, fahrzeug


def _ctx(db, einheit):
    user, _, _ = _geraet(db)
    return kontext_fuer_einheit(db, user, einheit.id, quelle="funk")


@pytest.mark.parametrize("vehicle,profil,revoked,status,lage_status,erwartet", [
    (False, "einheit", False, rs.STATUS_BEREITGESTELLT, MajorIncidentStatus.active, False),
    (True, None, False, rs.STATUS_BEREITGESTELLT, MajorIncidentStatus.active, False),
    (True, "fuehrung", False, rs.STATUS_BEREITGESTELLT, MajorIncidentStatus.active, False),
    (True, "einheit", True, rs.STATUS_BEREITGESTELLT, MajorIncidentStatus.active, False),
    (True, "einheit", False, rs.STATUS_ABGERUECKT, MajorIncidentStatus.active, False),
    (True, "einheit", False, rs.STATUS_BEREITGESTELLT, MajorIncidentStatus.closed, False),
    (True, "einheit", False, rs.STATUS_BEREITGESTELLT, MajorIncidentStatus.active, True),
])
def test_kontext_fuer_geraet_bedingungen(vehicle, profil, revoked, status, lage_status, erwartet):
    with _session() as db:
        user, token, fahrzeug = _geraet(db, vehicle=vehicle, profil=profil, revoked=revoked)
        lage = _lage(db, status=lage_status)
        if fahrzeug:
            _einheit(db, lage, fahrzeug.id, status=status)
        ctx = kontext_fuer_geraet(db, user, token)
        assert (ctx is not None) is erwartet
        db.rollback()


def test_kontext_fuer_geraet_nimmt_neueste_lage_und_lageauswahl():
    with _session() as db:
        user, token, fahrzeug = _geraet(db)
        alt = _lage(db, started_at=datetime.now(UTC) - timedelta(hours=1))
        neu = _lage(db, started_at=datetime.now(UTC))
        _einheit(db, alt, fahrzeug.id)
        e_neu = _einheit(db, neu, fahrzeug.id)
        assert kontext_fuer_geraet(db, user, token).einheit is e_neu
        assert kontext_fuer_geraet(db, user, token, alt.id).lage is alt
        db.rollback()


def test_kontext_fremde_org_und_fremder_auftrag_sind_nicht_sichtbar():
    with _session() as db:
        user, _, _ = _geraet(db)
        fremde_lage = _lage(db, org_id=2)
        fremde_einheit = _einheit(db, fremde_lage)
        with pytest.raises(LookupError):
            kontext_fuer_einheit(db, user, fremde_einheit.id, quelle="funk")
        lage = _lage(db)
        e = _einheit(db, lage)
        site = _site(db, lage)
        fremde = _einheit(db, lage, label="Andere")
        dispatch = rs.dispatch_to_site(db, fremde.id, lage.id, site.id)
        with pytest.raises(LookupError):
            auftrag_laden(db, _ctx(db, e), dispatch.id)
        db.rollback()


@pytest.mark.parametrize("von,nach,erlaubt", [
    ("zugewiesen", "vor_ort", True), ("zugewiesen", "in_arbeit", False),
    ("bestaetigt", "anfahrt", True), ("anfahrt", "bestaetigt", True),
    ("vor_ort", "in_arbeit", True), ("in_arbeit", "vor_ort", True),
    ("vor_ort", "anfahrt", False), ("abgeschlossen", "bestaetigt", False),
])
def test_statusmatrix(von, nach, erlaubt):
    with _session() as db:
        lage, site = _lage(db), None
        site = _site(db, lage)
        e = _einheit(db, lage)
        d = rs.dispatch_to_site(db, e.id, lage.id, site.id)
        d.einheit_status = von
        ctx = _ctx(db, e)
        if erlaubt:
            assert setze_einheit_status(db, ctx, d, nach, user_id=1, author_name="Funk")["geaendert"]
        else:
            with pytest.raises(EinheitKonflikt, match="ungueltiger_uebergang"):
                setze_einheit_status(db, ctx, d, nach, user_id=1, author_name="Funk")
        db.rollback()


def test_status_noop_grund_rueckzug_und_unterbrechen():
    with _session() as db:
        lage = _lage(db)
        a, b = _site(db, lage, "A"), _site(db, lage, "B")
        e = _einheit(db, lage)
        d_a, d_b = rs.dispatch_to_site(db, e.id, lage.id, a.id), rs.dispatch_to_site(db, e.id, lage.id, b.id)
        ctx = _ctx(db, e)
        assert not setze_einheit_status(db, ctx, d_a, "zugewiesen", user_id=1, author_name="Funk")["geaendert"]
        with pytest.raises(ValueError):
            setze_einheit_status(db, ctx, d_a, "nicht_durchfuehrbar", user_id=1, author_name="Funk")
        d_a.withdrawn_at = datetime.now(UTC)
        with pytest.raises(EinheitKonflikt, match="auftrag_zurueckgezogen"):
            setze_einheit_status(db, ctx, d_a, "bestaetigt", user_id=1, author_name="Funk")
        d_a.withdrawn_at = None
        setze_einheit_status(db, ctx, d_a, "vor_ort", user_id=1, author_name="Funk")
        with pytest.raises(EinheitKonflikt, match="aktiver_auftrag"):
            setze_einheit_status(db, ctx, d_b, "anfahrt", user_id=1, author_name="Funk")
        result = setze_einheit_status(db, ctx, d_b, "anfahrt", user_id=1, author_name="Funk", unterbrechen=True)
        assert result["unterbrochen_dispatch_id"] == d_a.id
        assert d_a.einheit_status == "bestaetigt"
        db.rollback()


def test_e3_chronik_und_alle_einheiten_fertig():
    with _session() as db:
        lage = _lage(db)
        site = _site(db, lage, phase=SitePhase.disponiert)
        e1, e2 = _einheit(db, lage, label="TLF Wolfurt"), _einheit(db, lage, label="RLF")
        d1, d2 = rs.dispatch_to_site(db, e1.id, lage.id, site.id), rs.dispatch_to_site(db, e2.id, lage.id, site.id)
        r1 = setze_einheit_status(db, _ctx(db, e1), d1, "vor_ort", user_id=1, author_name="Funk")
        assert r1["phase_geaendert"] and site.phase == SitePhase.in_arbeit
        db.flush()
        status_log = db.query(SiteLogEntry).filter_by(
            incident_site_id=site.id, kind="status",
        ).one()
        assert "TLF Wolfurt vor Ort" in status_log.text
        zweiter = setze_einheit_status(
            db, _ctx(db, e2), d2, "vor_ort", user_id=1, author_name="Funk",
        )
        assert not zweiter["phase_geaendert"]
        setze_einheit_status(db, _ctx(db, e1), d1, "abgeschlossen", user_id=1, author_name="Funk")
        letzter = setze_einheit_status(
            db, _ctx(db, e2), d2, "abgeschlossen", user_id=1, author_name="Funk",
        )
        assert letzter["alle_einheiten_fertig"] is True
        assert site.phase == SitePhase.in_arbeit
        db.rollback()


def test_auftraege_sortierung_und_funk_chronik():
    with _session() as db:
        lage = _lage(db)
        a, b, c = _site(db, lage, "A"), _site(db, lage, "B"), _site(db, lage, "C")
        e = _einheit(db, lage)
        d_a, d_b, d_c = (rs.dispatch_to_site(db, e.id, lage.id, s.id) for s in (a, b, c))
        d_a.reihenfolge, d_b.reihenfolge, d_c.reihenfolge = 2, 1, None
        zustand = auftraege_fuer_einheit(db, _ctx(db, e))
        assert zustand["kategorien"]["aktuell"] is None
        assert zustand["naechster_dispatch_id"] == d_b.id
        assert [d["dispatch_id"] for d in zustand["kategorien"]["weitere"]] == [d_b.id, d_a.id, d_c.id]
        setze_einheit_status(db, _ctx(db, e), d_b, "bestaetigt", user_id=1, author_name="Funk")
        db.flush()
        assert db.query(SiteLogEntry).filter_by(incident_site_id=b.id, kind="einheit").one().text.endswith("(per Funk)")
        db.rollback()


def test_einheitenstatus_erhoeht_auftragsversion_nicht():
    """version/geaendert_at zählen nur Führungsänderungen (Konflikterkennung der Outbox)."""
    with _session() as db:
        lage = _lage(db)
        site_a, site_b = _site(db, lage, name="A"), _site(db, lage, name="B")
        einheit = _einheit(db, lage)
        da = rs.dispatch_to_site(db, einheit.id, lage.id, site_a.id)
        db_ = rs.dispatch_to_site(db, einheit.id, lage.id, site_b.id)
        db.flush()
        setze_einheit_status(db, _ctx(db, einheit), da, "anfahrt", user_id=1, author_name="Funk")
        setze_einheit_status(
            db, _ctx(db, einheit), db_, "anfahrt", user_id=1, author_name="Funk", unterbrechen=True,
        )
        assert da.einheit_status == "bestaetigt"
        assert (da.version, db_.version) == (1, 1)
        assert da.geaendert_at is None and db_.geaendert_at is None
        db.rollback()
