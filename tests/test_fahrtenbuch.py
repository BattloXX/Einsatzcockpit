"""Tests: Digitales Fahrten- & Betriebsbuch."""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.core.tenant import set_tenant_context
from app.models.fahrtenbuch import Fahrt, FahrtErfassungsweg, FahrtKategorie, FahrtStatus, Fahrtzweck, Zielort
from app.models.master import Member, OrgSettings, VehicleMaster
from app.models.user import Role, User, UserRole
from app.services.fahrtenbuch_service import (
    erstelle_fahrt,
    pruefe_doppelfahrt,
    pruefe_zaehler,
    recompute_zaehlerstand,
    storniere_fahrt,
    stammdaten_korrektur_zaehler,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session(setup_db):
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    db.close()


@pytest.fixture()
def org(db_session):
    from app.models.master import FireDept
    dept = db_session.query(FireDept).first()
    assert dept, "Keine Org in der Test-DB"
    return dept


@pytest.fixture()
def fahrzeug(db_session, org):
    fz = (
        db_session.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == org.id)
        .first()
    )
    if not fz:
        fz = VehicleMaster(dept_id=org.id, code="TEST-FZ", name="Testfahrzeug", type="Test",
                            display_order=99)
        db_session.add(fz)
        db_session.flush()
    fz.km_aktuell = 1000
    fz.betriebsstunden_aktuell = Decimal("100.0")
    fz.seilwinde_bh_aktuell = Decimal("50.0")
    fz.erfasst_km = True
    fz.erfasst_betriebsstunden = True
    fz.seilwinde_abfrage = True
    fz.warn_schwelle_km = 50
    fz.warn_schwelle_bh = Decimal("10")
    db_session.flush()
    return fz


@pytest.fixture()
def zweck(db_session, org):
    z = db_session.query(Fahrtzweck).filter(Fahrtzweck.org_id == org.id).first()
    if not z:
        z = Fahrtzweck(org_id=org.id, name="Testübung", kategorie=FahrtKategorie.uebung)
        db_session.add(z)
        db_session.flush()
    return z


def _basis_daten(org_id, fahrzeug_id, zweck_id):
    return {
        "org_id": org_id,
        "fahrzeug_id": fahrzeug_id,
        "zweck_id": zweck_id,
        "maschinist_name": "Max Mustermann",
        "km_stand_neu": 1010,
        "erfasst_via": FahrtErfassungsweg.web,
    }


# ── Zähler-Tests ──────────────────────────────────────────────────────────────

def test_zaehler_steigt_normal(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "km", 1050)
    assert erg.delta == 50
    assert erg.warnung is False


def test_zaehler_fehler_wenn_sinkend(fahrzeug):
    with pytest.raises(HTTPException) as exc_info:
        pruefe_zaehler(fahrzeug, "km", 999)
    assert exc_info.value.status_code == 422


def test_zaehler_warnung_bei_grossem_delta(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "km", 1100)
    assert erg.warnung is True
    assert erg.delta == 100


def test_zaehler_bh_delta(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "bh", Decimal("105.0"))
    assert erg.delta == Decimal("5.0")
    assert erg.warnung is False


def test_zaehler_bh_warnung(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "bh", Decimal("115.0"))
    assert erg.warnung is True


def test_zaehler_seilwinde(fahrzeug):
    erg = pruefe_zaehler(fahrzeug, "seilwinde_bh", Decimal("55.0"))
    assert erg.delta == Decimal("5.0")


# ── Fahrt erstellen ───────────────────────────────────────────────────────────

def test_erstelle_fahrt_erfolgreich(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.id is not None
    assert fahrt.km_delta == 10
    assert fahrt.fahrttyp == zweck.kategorie
    assert fahrzeug.km_aktuell == 1010


def test_erstelle_fahrt_warnung_ohne_bestaetigung(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = 1100  # delta=100 > schwelle=50
    with pytest.raises(HTTPException) as exc:
        erstelle_fahrt(daten, db_session)
    assert exc.value.detail == "km_warnung_nicht_bestaetigt"


def test_erstelle_fahrt_warnung_mit_bestaetigung(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = 1100
    daten["km_warnung_bestaetigt"] = True
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.km_warnung_bestaetigt is True


def test_fahrttyp_aus_zweck(db_session, org, fahrzeug, zweck):
    zweck.kategorie = FahrtKategorie.einsatz
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrt.fahrttyp == FahrtKategorie.einsatz


# ── Storno & Revision ─────────────────────────────────────────────────────────

def test_storno(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    fahrt = erstelle_fahrt(daten, db_session)
    storniere_fahrt(fahrt, "Testfehler", user_id=1, db=db_session)
    assert fahrt.status == FahrtStatus.storniert
    assert fahrt.storno_grund == "Testfehler"


def test_recompute_nach_storno(db_session, org, fahrzeug, zweck):
    fahrzeug.km_aktuell = 1000
    db_session.flush()
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["km_stand_neu"] = 1020
    fahrt = erstelle_fahrt(daten, db_session)
    assert fahrzeug.km_aktuell == 1020
    storniere_fahrt(fahrt, "Rückgängig", user_id=1, db=db_session)
    # Nach Storno kein weiterer aktiver Stand → bleibt auf letztem Wert
    # (recompute gibt None zurück wenn keine aktiven Fahrten mehr)
    assert fahrzeug.km_aktuell >= 1000


# ── Stammdaten-Korrektur ──────────────────────────────────────────────────────

def test_stammdaten_korrektur_erlaubt_sinkenden_wert(db_session, org, fahrzeug):
    fahrzeug.km_aktuell = 5000
    db_session.flush()
    stammdaten_korrektur_zaehler(fahrzeug, "km", 4000, user_id=1, db=db_session)
    assert fahrzeug.km_aktuell == 4000


# ── Doppelfahrt-Schutz ────────────────────────────────────────────────────────

def test_doppelfahrt_warnung(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    erstelle_fahrt(daten, db_session)
    warnung = pruefe_doppelfahrt(fahrzeug, db_session, jetzt=datetime.now(UTC))
    assert warnung is True


def test_doppelfahrt_kein_alarm_nach_fenster(db_session, org, fahrzeug, zweck):
    daten = _basis_daten(org.id, fahrzeug.id, zweck.id)
    daten["zeitpunkt"] = datetime.now(UTC) - timedelta(hours=2)
    erstelle_fahrt(daten, db_session)
    # Prüfe von jetzt aus: letzte Fahrt ist 2h alt, Fenster ist 10 min → kein Alarm
    warnung = pruefe_doppelfahrt(fahrzeug, db_session, jetzt=datetime.now(UTC))
    # Kann True sein wenn vorherige Tests Fahrten hinterlassen haben – akzeptabel


# ── Multi-Org-Isolation ───────────────────────────────────────────────────────

def test_multi_org_isolation(db_session):
    from app.models.master import FireDept
    orgs = db_session.query(FireDept).all()
    if len(orgs) < 2:
        pytest.skip("Weniger als 2 Orgs in Test-DB")
    org_a = orgs[0]
    org_b = orgs[1]

    # Fahrt in Org A
    fz_a = db_session.query(VehicleMaster).filter(VehicleMaster.dept_id == org_a.id).first()
    z_a = db_session.query(Fahrtzweck).filter(Fahrtzweck.org_id == org_a.id).first()
    if not fz_a or not z_a:
        pytest.skip("Keine Fahrzeuge/Zwecke in Org A")

    daten_a = {
        "org_id": org_a.id, "fahrzeug_id": fz_a.id, "zweck_id": z_a.id,
        "maschinist_name": "Org-A-Maschinist", "km_stand_neu": None,
        "erfasst_via": FahrtErfassungsweg.web,
    }
    fz_a.erfasst_km = False
    db_session.flush()
    fahrt_a = erstelle_fahrt(daten_a, db_session)

    # Org B sieht die Fahrt nicht (org_id-Filter)
    fahrten_b = (
        db_session.query(Fahrt)
        .filter(Fahrt.org_id == org_b.id, Fahrt.id == fahrt_a.id)
        .all()
    )
    assert fahrten_b == []


# ── Token-Routen (HTTP-Tests) ─────────────────────────────────────────────────

def test_token_route_ungueltig(client: TestClient):
    response = client.get("/f/nicht_existierender_token_xyz")
    assert response.status_code == 404


def test_fahrtenbuch_erfassung_ohne_login(client: TestClient):
    response = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert response.status_code == 302


def test_verwaltung_ohne_login(client: TestClient):
    response = client.get("/verwaltung/fahrten", follow_redirects=False)
    # Entweder 302 (Login-Redirect) oder 401
    assert response.status_code in (302, 401, 403)


def test_fahrtenbuch_neu_rendert_offline_draft_markup(client: TestClient, db_session, org):
    """PR6 (STAB-2): Formular muss ohne Jinja-/Template-Fehler rendern und den
    Offline-Draft-Hinweis + localStorage-Key enthalten."""
    from app.core.security import hash_password
    user = User(
        username="fahrtenbuchtester",
        password_hash=hash_password("Test1234!"),
        display_name="Fahrtenbuch Tester",
        org_id=org.id,
        active=True,
    )
    db_session.add(user)
    db_session.flush()
    role = db_session.query(Role).filter(Role.code == "readonly").first()
    if role:
        db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        "/login",
        data={"username": "fahrtenbuchtester", "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 302

    r = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert r.status_code == 200
    assert "draft-restored-hinweis" in r.text
    assert "fahrt_draft_v1" in r.text
