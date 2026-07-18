"""PR 5: Förderstrecken-Persistenz — Modelle, Status-Flow, Versionierung, Relais→Wasserstelle."""
import pytest

from app.core.tenant import set_tenant_context
from app.models.foerderstrecke import (
    STATION_QUELLPUMPE,
    STATION_PUFFER,
    STRECKE_STATUS_ARCHIVIERT,
    STRECKE_STATUS_ENTWURF,
    STRECKE_STATUS_FREIGEGEBEN,
    FoerderErgebnis,
    FoerderStation,
    Foerderstrecke,
)
from app.models.wasserstelle import Wasserstelle
from app.services.foerderstrecke_persist_service import (
    ergebnis_anhaengen,
    relais_als_wasserstelle,
    setze_status,
    status_wechsel_erlaubt,
)
from tests.conftest import TestingSession


@pytest.fixture
def db_ctx():
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    set_tenant_context(db, None)
    db.close()


def _strecke(db, org_id=990501, name="Teststrecke"):
    s = Foerderstrecke(org_id=org_id, name=name)
    db.add(s)
    db.flush()
    return s


# ── Modell-Roundtrip + Relationships ────────────────────────────────────────────

def test_strecke_mit_stationen_und_ergebnis(db_ctx):
    db = db_ctx
    s = _strecke(db)
    db.add(FoerderStation(org_id=s.org_id, strecke_id=s.id, sort=0, typ=STATION_QUELLPUMPE))
    db.add(FoerderStation(org_id=s.org_id, strecke_id=s.id, sort=1, typ=STATION_PUFFER,
                          behaelter_volumen_l=2000))
    db.flush()
    db.refresh(s)
    assert len(s.stationen) == 2
    assert s.stationen[0].typ == STATION_QUELLPUMPE
    assert s.status == STRECKE_STATUS_ENTWURF
    assert s.status_label == "Entwurf"


# ── Status-Flow ─────────────────────────────────────────────────────────────────

def test_status_uebergaenge():
    assert status_wechsel_erlaubt(STRECKE_STATUS_ENTWURF, STRECKE_STATUS_FREIGEGEBEN)
    assert not status_wechsel_erlaubt(STRECKE_STATUS_ARCHIVIERT, STRECKE_STATUS_FREIGEGEBEN)


def test_setze_status(db_ctx):
    s = _strecke(db_ctx)
    assert setze_status(s, STRECKE_STATUS_FREIGEGEBEN) is True
    assert s.status == STRECKE_STATUS_FREIGEGEBEN
    # unerlaubter Direktsprung archiviert→freigegeben
    s.status = STRECKE_STATUS_ARCHIVIERT
    assert setze_status(s, STRECKE_STATUS_FREIGEGEBEN) is False
    assert s.status == STRECKE_STATUS_ARCHIVIERT


# ── Versionierung ────────────────────────────────────────────────────────────────

def test_ergebnis_versioniert(db_ctx):
    db = db_ctx
    s = _strecke(db)
    ergebnis_anhaengen(db, s, {"q_max_l_min": 4000, "stationswerte": [{"p_aus_bar": 5}],
                               "warnungen": ["x"]}, modus="A")
    ergebnis_anhaengen(db, s, {"q_max_l_min": 3500, "stationswerte": [], "warnungen": []}, modus="A")
    db.flush()
    rows = (db.query(FoerderErgebnis)
            .filter(FoerderErgebnis.strecke_id == s.id)
            .order_by(FoerderErgebnis.berechnet_am).all())
    assert len(rows) == 2                         # nie überschrieben
    assert rows[0].q_max_l_min == 4000
    assert rows[0].stationswerte == [{"p_aus_bar": 5}]
    assert rows[0].warnungen == ["x"]


# ── Relais → Wasserstelle ────────────────────────────────────────────────────────

def test_relais_als_wasserstelle_anlegen_und_idempotent(db_ctx):
    db = db_ctx
    s = _strecke(db, org_id=990502)
    station = FoerderStation(org_id=s.org_id, strecke_id=s.id, sort=1, typ=STATION_PUFFER,
                             lat=47.47, lng=9.75)
    db.add(station)
    db.flush()

    ws = relais_als_wasserstelle(db, station, org_id=s.org_id, user_id=None)
    assert ws is not None
    assert ws.typ == "relais"
    assert station.wasserstelle_id == ws.id
    anzahl = db.query(Wasserstelle).filter(Wasserstelle.org_id == 990502).count()
    assert anzahl == 1

    # Zweiter Aufruf → aktualisiert, keine zweite Zeile
    station.lat = 47.48
    ws2 = relais_als_wasserstelle(db, station, org_id=s.org_id, user_id=None)
    assert ws2.id == ws.id
    assert abs(ws2.lat - 47.48) < 1e-9
    assert db.query(Wasserstelle).filter(Wasserstelle.org_id == 990502).count() == 1


def test_relais_ohne_koordinaten_gibt_none(db_ctx):
    db = db_ctx
    s = _strecke(db, org_id=990503)
    station = FoerderStation(org_id=s.org_id, strecke_id=s.id, sort=1, typ=STATION_PUFFER)
    db.add(station)
    db.flush()
    assert relais_als_wasserstelle(db, station, org_id=s.org_id) is None


# ── Tenant-Isolation ─────────────────────────────────────────────────────────────

def test_strecke_tenant_isolation(db_ctx):
    db = db_ctx
    _strecke(db, org_id=991001, name="A")
    _strecke(db, org_id=991002, name="B")
    db.flush()
    set_tenant_context(db, 991001)
    sichtbar = db.query(Foerderstrecke).all()
    assert sichtbar and all(x.org_id == 991001 for x in sichtbar)
