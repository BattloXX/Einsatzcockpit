"""PR 7: Kalibrierung — Least-Squares-Fit gegen synthetische Messreihe + Review-Queue."""
import pytest

from app.core.tenant import set_tenant_context
from app.models.foerderstrecke import (
    KALIBRIER_OFFEN,
    KALIBRIER_UEBERNOMMEN,
    KALIBRIER_VERWORFEN,
    FoerderKalibrierVorschlag,
    FoerderMessung,
    FoerderSchlauchTyp,
)
from app.services.foerderstrecke_kalibrier_service import (
    erzeuge_vorschlaege,
    fit_k,
    vorschlag_uebernehmen,
    vorschlag_verwerfen,
)
from app.services.foerderstrecke_service import reibungsverlust_bar
from tests.conftest import TestingSession


@pytest.fixture
def db_ctx():
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    set_tenant_context(db, None)
    db.close()


def _messung(k_wahr, q, laenge, n=1, delta_h=0.0, schlauch_id=None, org_id=992001):
    """Synthetische Messung aus wahrem k: p_aus - p_ein = reibung + hoehe."""
    reib = reibungsverlust_bar(k_wahr, q, laenge, n_parallel=n)
    p_aus = 8.0
    p_ein = p_aus - reib - delta_h / 10.0
    return FoerderMessung(
        org_id=org_id, schlauch_typ_id=schlauch_id, q_gemessen_l_min=q,
        laenge_m=laenge, n_parallel=n, delta_hoehe_m=delta_h,
        p_aus_bar=p_aus, p_ein_folge_bar=p_ein,
    )


# ── Fit ─────────────────────────────────────────────────────────────────────────

def test_fit_recovers_wahren_k():
    k_wahr = 1.56
    proben = [
        _messung(k_wahr, 800, 100),
        _messung(k_wahr, 400, 200),
        _messung(k_wahr, 1000, 150, delta_h=20),
        _messung(k_wahr, 600, 300, n=2),
    ]
    k, n = fit_k(proben)
    assert n == 4
    assert abs(k - k_wahr) < 0.02


def test_fit_ignoriert_unvollstaendige():
    gut = _messung(1.0, 800, 100)
    schlecht = FoerderMessung(org_id=1, q_gemessen_l_min=None, laenge_m=100,
                              p_aus_bar=8, p_ein_folge_bar=7)
    res = fit_k([gut, schlecht])
    assert res is not None and res[1] == 1


def test_fit_leer_gibt_none():
    assert fit_k([]) is None


# ── Vorschlags-Erzeugung + Review ────────────────────────────────────────────────

def _schlauch(db, k, org_id=992001):
    s = FoerderSchlauchTyp(org_id=org_id, kuerzel="B-75", durchmesser_mm=75, k_verlust=k)
    db.add(s)
    db.flush()
    return s


def test_erzeuge_vorschlag_bei_abweichung(db_ctx):
    db = db_ctx
    org = 992010
    schlauch = _schlauch(db, k=2.0, org_id=org)   # bewusst falscher k
    for m in [_messung(1.56, 800, 100, schlauch_id=schlauch.id, org_id=org),
              _messung(1.56, 400, 200, schlauch_id=schlauch.id, org_id=org),
              _messung(1.56, 1000, 150, schlauch_id=schlauch.id, org_id=org)]:
        db.add(m)
    db.flush()
    neue = erzeuge_vorschlaege(db, org)
    db.flush()
    assert len(neue) == 1
    v = neue[0]
    assert v.status == KALIBRIER_OFFEN
    assert abs(v.k_neu - 1.56) < 0.03
    assert v.k_alt == 2.0


def test_kein_vorschlag_bei_kleiner_abweichung(db_ctx):
    db = db_ctx
    org = 992011
    schlauch = _schlauch(db, k=1.56, org_id=org)   # bereits korrekt
    for m in [_messung(1.56, 800, 100, schlauch_id=schlauch.id, org_id=org),
              _messung(1.56, 600, 200, schlauch_id=schlauch.id, org_id=org)]:
        db.add(m)
    db.flush()
    neue = erzeuge_vorschlaege(db, org)
    assert neue == []


def test_uebernehmen_setzt_k_und_status(db_ctx):
    db = db_ctx
    org = 992012
    schlauch = _schlauch(db, k=2.0, org_id=org)
    v = FoerderKalibrierVorschlag(org_id=org, schlauch_typ_id=schlauch.id,
                                  k_alt=2.0, k_neu=1.56, n_messungen=3, status=KALIBRIER_OFFEN)
    db.add(v)
    db.flush()
    assert vorschlag_uebernehmen(db, v, user_id=None) is True
    assert schlauch.k_verlust == 1.56
    assert v.status == KALIBRIER_UEBERNOMMEN
    # zweiter Aufruf → schon entschieden
    assert vorschlag_uebernehmen(db, v, user_id=None) is False


def test_verwerfen_laesst_k_unveraendert(db_ctx):
    db = db_ctx
    org = 992013
    schlauch = _schlauch(db, k=2.0, org_id=org)
    v = FoerderKalibrierVorschlag(org_id=org, schlauch_typ_id=schlauch.id,
                                  k_alt=2.0, k_neu=1.56, n_messungen=3, status=KALIBRIER_OFFEN)
    db.add(v)
    db.flush()
    assert vorschlag_verwerfen(db, v, user_id=None) is True
    assert schlauch.k_verlust == 2.0            # unverändert
    assert v.status == KALIBRIER_VERWORFEN
