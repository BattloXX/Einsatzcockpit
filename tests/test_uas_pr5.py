"""PR 5: Notfall-/Unfall-Workflow, UTC-Konvertierung, Notfallcheckliste."""
from datetime import date

import pytest

from app.services.uas_ereignis import NOTFALLCHECKLISTE, lokal_zu_utc


# ── Notfallcheckliste (Anh. 8.3) ─────────────────────────────────────────────

def test_notfallcheckliste_7_vorfaelle():
    assert len(NOTFALLCHECKLISTE) == 7


def test_notfallcheckliste_felder():
    for v in NOTFALLCHECKLISTE:
        assert "id" in v
        assert "titel" in v
        assert "verhalten" in v
        assert "massnahmen" in v
        assert isinstance(v["massnahmen"], list)
        assert len(v["massnahmen"]) >= 2


def test_absturz_eintrag_vorhanden():
    titel_liste = [v["titel"] for v in NOTFALLCHECKLISTE]
    assert any("absturz" in t.lower() or "havarie" in t.lower() for t in titel_liste)


# ── UTC-Konvertierung (Anh. 8.4) ─────────────────────────────────────────────

def test_lokal_zu_utc_sommer():
    d, z = lokal_zu_utc(date(2026, 6, 20), "14:30")
    assert z == "12:30"  # UTC+2 im Sommer


def test_lokal_zu_utc_winter():
    d, z = lokal_zu_utc(date(2026, 1, 15), "14:30")
    assert z == "13:30"  # UTC+1 im Winter


def test_lokal_zu_utc_mitternacht_uebergang():
    datum, zeit = lokal_zu_utc(date(2026, 6, 20), "01:00")
    # 01:00 MESZ = 23:00 UTC (Vortag)
    assert zeit == "23:00"
    assert datum == date(2026, 6, 19)


def test_lokal_zu_utc_ungueltige_zeit():
    d, z = lokal_zu_utc(date(2026, 6, 20), "ungueltig")
    assert d == date(2026, 6, 20)
    assert z == "ungueltig"


# ── Model-Import ──────────────────────────────────────────────────────────────

def test_uas_ereignis_importable():
    from app.models.uas import UASEreignis, UASEreignisTyp
    assert UASEreignis.__tablename__ == "uas_ereignis"
    values = {t.value for t in UASEreignisTyp}
    assert "notfall" in values
    assert "unfall" in values
    assert "stoerung" in values


def test_tenant_tables_ereignis():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "uas_ereignis" in _TENANT_TABLE_NAMES


# ── DB-Roundtrip ──────────────────────────────────────────────────────────────

@pytest.fixture
def db_ctx():
    from app.core.tenant import set_tenant_context
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.close()


def test_uas_ereignis_db_create(setup_db, db_ctx):
    import json
    from app.models.uas import UASEreignis, UASEreignisTyp

    e = UASEreignis(
        org_id=1,
        uas_flug_id=None,
        typ=UASEreignisTyp.unfall.value,
        kategorie="Absturz",
        datum_lokal=date(2026, 6, 20),
        zeit_lokal="14:30",
        datum_utc=date(2026, 6, 20),
        zeit_utc="12:30",
        ort_icao="LOWI",
        klassifizierung="Unfall",
        beschreibung="Testbeschreibung",
        gemeldet_an=json.dumps({"stuetzpunktleiter": None}),
    )
    db_ctx.add(e)
    db_ctx.commit()
    db_ctx.refresh(e)

    assert e.id is not None
    assert e.typ == "unfall"
    assert e.ort_icao == "LOWI"
