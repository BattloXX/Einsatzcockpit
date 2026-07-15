"""Objekt↔Einsatz-Verknüpfung: BMA-Normalisierung (führende Nullen),
Verknüpfung über Einsatzgrund (reason) und OSM-Adresssuche.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektBMA,
)
from app.services.objekt_matching_service import _norm_bma, match_incident


# ── _norm_bma ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("roh,erwartet", [
    ("1044", "1044"),
    ("01044", "1044"),
    ("0001044", "1044"),
    ("BMA-1044", "1044"),
    ("RFL 1044", "1044"),
    ("0", "0"),
    ("000", "0"),
    ("", None),
    (None, None),
    ("keine-ziffern", None),
])
def test_norm_bma(roh, erwartet):
    assert _norm_bma(roh) == erwartet


# ── Matching-Fixture (in-memory) ──────────────────────────────────────────────

@pytest.fixture()
def match_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    set_tenant_context(db, None)

    org = FireDept(slug="v-org", name="Verkn Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, objekt_geo_match_radius_m=75))

    # Objekt mit führender Null in der BMA-Nummer
    obj = Objekt(org_id=org.id, nummer=1, name="Werk Nord",
                 status=OBJEKT_STATUS_FREIGEGEBEN,
                 strasse="Dammstraße", hausnummer="64", ort="Wolfurt",
                 lat=47.46520, lng=9.75030)
    db.add(obj)
    db.flush()
    db.add(ObjektBMA(org_id=org.id, objekt_id=obj.id, bma_nummer="01044"))
    db.commit()

    yield db, org.id, obj
    db.close()
    Base.metadata.drop_all(bind=engine)


def _incident(db, org_id, **kw):
    inc = Incident(
        primary_org_id=org_id,
        alarm_type_code=kw.get("alarm", "F14"),
        report_text=kw.get("report_text"),
        reason=kw.get("reason"),
        address_street=kw.get("street"),
        address_no=kw.get("no"),
        address_city=kw.get("city"),
    )
    db.add(inc)
    db.commit()
    return inc


def test_bma_match_ignoriert_fuehrende_nullen(match_db):
    """Alarm nennt '1044', Objekt gespeichert als '01044' → Treffer."""
    db, org_id, obj = match_db
    inc = _incident(db, org_id, report_text="bmz 1044 ausgelöst")
    neu = match_incident(db, inc)
    db.commit()
    assert len(neu) == 1
    assert neu[0].objekt_id == obj.id
    assert neu[0].quelle == "bma"
    assert neu[0].status == "bestaetigt"


def test_bma_match_ueber_einsatzgrund(match_db):
    """BMA-Nummer steht im Einsatzgrund (reason), nicht im report_text."""
    db, org_id, obj = match_db
    inc = _incident(db, org_id, reason="BMA-Nr. 01044 Rauchmelder")
    neu = match_incident(db, inc)
    db.commit()
    assert len(neu) == 1
    assert neu[0].objekt_id == obj.id
    assert neu[0].quelle == "bma"


def test_kein_bma_match_bei_falscher_nummer(match_db):
    db, org_id, _ = match_db
    inc = _incident(db, org_id, reason="BMA 9999")
    neu = match_incident(db, inc)
    db.commit()
    assert neu == []


# ── OSM-Adresssuche ───────────────────────────────────────────────────────────

def test_search_addresses_leere_eingabe():
    from app.services.geocoding import search_addresses
    assert asyncio.run(search_addresses("")) == []
    assert asyncio.run(search_addresses("ab")) == []  # < 3 Zeichen


def test_search_addresses_parst_nominatim(monkeypatch):
    """search_addresses extrahiert strukturierte Felder aus der Nominatim-Antwort."""
    from app.services import geocoding

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{
                "lat": "47.465", "lon": "9.750",
                "display_name": "Hofsteigstraße 30, 6922 Wolfurt",
                "address": {"road": "Hofsteigstraße", "house_number": "30",
                            "postcode": "6922", "town": "Wolfurt"},
            }]

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _FakeResp()

    async def _no_throttle():
        return None

    monkeypatch.setattr(geocoding.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(geocoding, "_throttle", _no_throttle)

    treffer = asyncio.run(geocoding.search_addresses("Hofsteigstraße 30 Wolfurt"))
    assert len(treffer) == 1
    t = treffer[0]
    assert t["strasse"] == "Hofsteigstraße"
    assert t["hausnummer"] == "30"
    assert t["plz"] == "6922"
    assert t["ort"] == "Wolfurt"
    assert t["lat"] == pytest.approx(47.465)
    assert t["lng"] == pytest.approx(9.750)


def test_adress_suche_route_registriert():
    from app.main import app
    from tests.conftest import all_app_paths
    pfade = all_app_paths(app)
    assert "/objekte/adress-suche" in pfade
