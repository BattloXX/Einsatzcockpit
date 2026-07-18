"""PR 3: Höhen-Service — Resampling, Cache (Mem/DB), Batch-Split, Fallback."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.hoehen_cache import HoehenCache, hoehen_key
from app.services import hoehen_service as hs


@pytest.fixture(autouse=True)
def _clear_cache():
    hs.cache_leeren()
    yield
    hs.cache_leeren()


@pytest.fixture
def db(setup_db):
    eng = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    s = Session()
    set_tenant_context(s, None)
    try:
        s.query(HoehenCache).delete()
        s.commit()
        yield s
    finally:
        s.rollback()
        s.close()


# ── Geometrie (rein) ─────────────────────────────────────────────────────────────

def test_resample_polyline_stuetzpunkte():
    # ~100 m nach Norden (0.0009° lat ≈ 100 m)
    pkt = [(47.0, 9.0), (47.0009, 9.0)]
    stz = hs.resample_polyline(pkt, segment_m=25.0)
    # 0,25,50,75,100 + echter Endpunkt (~100,1 m)
    assert 5 <= len(stz) <= 6
    assert stz[0]["s_m"] == 0.0
    assert stz[-1]["s_m"] > 95
    # regelmäßige 25-m-Schritte bis zum letzten regulären Stützpunkt
    assert stz[1]["s_m"] == 25.0 and stz[2]["s_m"] == 50.0
    # Stützpunkte liegen aufsteigend nach s
    assert [p["s_m"] for p in stz] == sorted(p["s_m"] for p in stz)


def test_resample_polyline_einzelpunkt_und_leer():
    assert hs.resample_polyline([]) == []
    einer = hs.resample_polyline([(47.0, 9.0)])
    assert len(einer) == 1 and einer[0]["s_m"] == 0.0


def test_hoehen_key_rundet():
    assert hoehen_key(47.123456, 9.654321) == (4712346, 965432)


# ── Cache + Batch + Fallback (HTTP gemockt) ──────────────────────────────────────

def _stub_openmeteo(calls):
    async def _f(punkte):
        calls.append(len(punkte))
        return [400.0 + i for i in range(len(punkte))]
    return _f


@pytest.mark.asyncio
async def test_openmeteo_fallback_und_grob(monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_fetch_openmeteo", _stub_openmeteo(calls))
    # AT liefert nichts (URL leer per Default) → Open-Meteo, grob=True
    res = await hs.hoehen_fuer_punkte([(47.0, 9.0), (47.1, 9.1)])
    assert res["hoehen"] == [400.0, 401.0]
    assert res["quelle"] == "openmeteo"
    assert res["grob"] is True
    assert calls == [2]


@pytest.mark.asyncio
async def test_mem_cache_verhindert_zweite_abfrage(monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_fetch_openmeteo", _stub_openmeteo(calls))
    pts = [(47.0, 9.0)]
    await hs.hoehen_fuer_punkte(pts)
    await hs.hoehen_fuer_punkte(pts)   # zweiter Aufruf aus Cache
    assert calls == [1]                # nur einmal HTTP


@pytest.mark.asyncio
async def test_batch_split_ueber_100(monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_fetch_openmeteo", _stub_openmeteo(calls))
    pts = [(47.0 + i * 0.001, 9.0) for i in range(150)]
    res = await hs.hoehen_fuer_punkte(pts)
    assert calls == [100, 50]          # in zwei Batches aufgeteilt
    assert len(res["hoehen"]) == 150
    assert all(h is not None for h in res["hoehen"])


@pytest.mark.asyncio
async def test_at_primaer_wenn_konfiguriert(monkeypatch):
    om_calls = []
    monkeypatch.setattr(hs, "_fetch_openmeteo", _stub_openmeteo(om_calls))

    async def _at(punkte):
        return [1000.0 for _ in punkte]
    monkeypatch.setattr(hs, "_fetch_at", _at)

    res = await hs.hoehen_fuer_punkte([(47.0, 9.0)])
    assert res["hoehen"] == [1000.0]
    assert res["quelle"] == "at"
    assert res["grob"] is False
    assert om_calls == []              # Open-Meteo nicht nötig


@pytest.mark.asyncio
async def test_db_cache_persistiert(monkeypatch, db):
    calls = []
    monkeypatch.setattr(hs, "_fetch_openmeteo", _stub_openmeteo(calls))
    pts = [(47.5, 9.7)]
    await hs.hoehen_fuer_punkte(pts, db=db)
    assert db.query(HoehenCache).count() == 1
    # Mem-Cache leeren → aus DB bedient, kein erneuter HTTP-Call
    hs.cache_leeren()
    res = await hs.hoehen_fuer_punkte(pts, db=db)
    assert res["hoehen"] == [400.0]
    assert calls == [1]                # nur die erste Abfrage


@pytest.mark.asyncio
async def test_hoehenprofil_ergaenzt_hoehen(monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_fetch_openmeteo", _stub_openmeteo(calls))
    profil = await hs.hoehenprofil([(47.0, 9.0), (47.0009, 9.0)], segment_m=25.0)
    assert profil["stuetzpunkte"]
    assert all("hoehe_m" in p for p in profil["stuetzpunkte"])
    assert profil["stuetzpunkte"][0]["hoehe_m"] is not None
