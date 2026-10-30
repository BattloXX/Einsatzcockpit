"""Gezielte Tests fuer Gebiet und Ortswahl im Planspiel-Adresspool."""
import random
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.master import FireDept, OrgSettings
from app.models.planspiel import PlanspielAdresse
from app.services.planspiel_ort_service import (
    GebietFehlt,
    gebiet,
    im_gebiet,
    ort_waehlen,
    ortschaften_ergaenzen,
    standard_ortschaften,
)
from tests.conftest import TestingSession


def _org(db, lat=47.47, lng=9.75):
    org = FireDept(slug=f"planspiel-ort-{uuid4().hex}", name="Planspiel Ort", fallback_lat=lat, fallback_lng=lng)
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, planspiel_gebiet_radius_m=5000))
    db.commit()
    set_tenant_context(db, org.id)
    return org


def test_gebiet_radius_und_fehlende_koordinaten():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        area = gebiet(db, org.id)
        assert im_gebiet(area, 47.479, 9.75)
        assert not im_gebiet(area, 47.56, 9.75)
        missing = _org(db, None, None)
        with pytest.raises(GebietFehlt):
            gebiet(db, missing.id)
    finally:
        db.close()


def test_ortwahl_respektiert_tenant_und_orttyp():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        db.add(PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id="one", strasse="Hauptstraße",
               lat=47.47, lng=9.75, qualitaet="A", ort_typen=["wohngebaeude"], aktiv=True))
        db.commit()
        selected = ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(1))
        assert selected.strasse == "Hauptstraße"
    finally:
        db.close()


def test_ortschaften_filter_und_ergaenzen():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        org.city = "Wolfurt"
        db.add_all([
            PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id="wolfurt", strasse="A",
                             ort="Wolfurt", lat=47.470, lng=9.750, qualitaet="A",
                             ort_typen=["wohngebaeude"], aktiv=True),
            PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id="bregenz", strasse="B",
                             ort="Bregenz", lat=47.480, lng=9.760, qualitaet="A",
                             ort_typen=["wohngebaeude"], aktiv=True),
            PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id="near", strasse="N",
                             lat=47.471, lng=9.751, qualitaet="C", ort_typen=["strasse"], aktiv=True),
            PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id="far", strasse="F",
                             lat=47.500, lng=9.750, qualitaet="C", ort_typen=["strasse"], aktiv=True),
        ])
        db.flush()
        assert ortschaften_ergaenzen(db, org.id) == 1
        assert db.query(PlanspielAdresse).filter_by(org_id=org.id, quell_id="near").one().ort == "Wolfurt"
        assert db.query(PlanspielAdresse).filter_by(org_id=org.id, quell_id="far").one().ort is None
        assert standard_ortschaften(db, org.id) == ["Wolfurt"]
        selected = ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(1),
                               ortschaften=["wolfurt"])
        assert selected.ort == "Wolfurt"
        org.city = ""
        assert standard_ortschaften(db, org.id) is None
    finally:
        db.close()


# ── Poolaufbau ──────────────────────────────────────────────────────────────

def _objekt(db, org_id, name, lat, lng, status="freigegeben", bma=None):
    from app.models.objekt import Objekt, ObjektBMA

    objekt = Objekt(org_id=org_id, name=name, status=status, strasse="Objektweg", hausnummer="1",
                    ort="Testort", lat=lat, lng=lng)
    db.add(objekt)
    db.flush()
    if bma:
        db.add(ObjektBMA(org_id=org_id, objekt_id=objekt.id, bma_nummer=bma))
    db.flush()
    return objekt


def test_pool_aufbauen_quellen_bma_und_idempotenz():
    from app.models.incident import Incident
    from app.services.planspiel_ort_service import pool_aufbauen

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        set_tenant_context(db, None)
        mit_bma = _objekt(db, org.id, "Firma BMA", 47.471, 9.751, bma="BMA-7")
        _objekt(db, org.id, "Entwurf", 47.472, 9.752, status="entwurf")
        _objekt(db, org.id, "Weit weg", 47.60, 9.75)
        db.add(Incident(primary_org_id=org.id, is_exercise=False, alarm_type_code="F1",
                        address_street="Lindenweg", address_no="3", address_city="Testort", lat=47.473, lng=9.753))
        db.add(Incident(primary_org_id=org.id, is_exercise=False, alarm_type_code="T1",
                        address_street="Lindenweg", address_no="3", lat=47.473, lng=9.753))
        db.add(Incident(primary_org_id=org.id, is_exercise=True, alarm_type_code="F1",
                        address_street="Uebungsweg", address_no="9", lat=47.474, lng=9.754))
        db.commit()

        ergebnis = pool_aufbauen(db, org.id)
        db.commit()
        assert ergebnis.neu == 2
        rows = db.query(PlanspielAdresse).filter_by(org_id=org.id, aktiv=True).all()
        objekt_row = next(r for r in rows if r.quelle == "objekt")
        assert objekt_row.objekt_id == mit_bma.id
        assert objekt_row.qualitaet == "A"
        assert "objekt_bma" in objekt_row.ort_typen
        assert objekt_row.merkmale == {"bma": True}
        historie = next(r for r in rows if r.quelle == "einsatzhistorie")
        assert (historie.strasse, historie.hausnr, historie.qualitaet) == ("Lindenweg", "3", "B")

        zweiter = pool_aufbauen(db, org.id)
        db.commit()
        assert zweiter.neu == 0
        assert db.query(PlanspielAdresse).filter_by(org_id=org.id).count() == 2

        db.delete(mit_bma)
        db.commit()
        dritter = pool_aufbauen(db, org.id)
        db.commit()
        assert dritter.deaktiviert == 1
        assert db.query(PlanspielAdresse).filter_by(org_id=org.id, quelle="objekt", aktiv=True).count() == 0
    finally:
        db.close()


# ── OSM-Import ──────────────────────────────────────────────────────────────

_OSM = {"elements": [
    {"type": "way", "id": 1, "center": {"lat": 47.471, "lon": 9.751},
     "tags": {"addr:street": "Dorfstraße", "addr:housenumber": "5", "building": "apartments"}},
    {"type": "node", "id": 2, "lat": 47.70, "lon": 9.75,
     "tags": {"addr:street": "Fernweg", "addr:housenumber": "1", "building": "house"}},
    {"type": "way", "id": 3, "center": {"lat": 47.472, "lon": 9.752},
     "tags": {"highway": "secondary", "name": "Landesstraße"}},
    {"type": "way", "id": 4, "center": {"lat": 47.473, "lon": 9.753},
     "tags": {"waterway": "stream", "name": "Achbach"}},
    {"type": "way", "id": 5, "center": {"lat": 47.474, "lon": 9.754}, "tags": {"railway": "rail"}},
    {"type": "way", "id": 6, "center": {"lat": 47.475, "lon": 9.755},
     "tags": {"addr:street": "Parkweg", "addr:housenumber": "2", "amenity": "parking"}},
]}


class _Antwort:
    def __init__(self, status, daten):
        self.status_code, self._daten = status, daten

    def raise_for_status(self):
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fehler", request=httpx.Request("POST", "http://x"), response=None)

    def json(self):
        return self._daten


def _mock_overpass(monkeypatch, status, daten):
    import httpx

    async def post(self, url, data=None):
        return _Antwort(status, daten)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)


def test_osm_import_mapping_idempotent_und_fehler(monkeypatch):
    import asyncio

    from app.services.planspiel_ort_service import OsmImportFehler, osm_importieren

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        org_id = org.id
    finally:
        db.close()

    monkeypatch.setattr("app.services.planspiel_ort_service._OSM_WARTEN_S", (0.0, 0.0))
    _mock_overpass(monkeypatch, 504, {})
    with pytest.raises(OsmImportFehler):
        asyncio.run(osm_importieren(TestingSession, org_id))

    _mock_overpass(monkeypatch, 200, _OSM)
    ergebnis = asyncio.run(osm_importieren(TestingSession, org_id))
    assert ergebnis.neu == 5
    zweiter = asyncio.run(osm_importieren(TestingSession, org_id))
    assert zweiter.neu == 0

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        rows = {r.quell_id: r for r in db.query(PlanspielAdresse).filter_by(org_id=org_id).all()}
        assert "node/2" not in rows
        assert {"mehrfamilienhaus", "wohngebaeude"} <= set(rows["way/1"].ort_typen)
        assert rows["way/1"].qualitaet == "B"
        assert "strasse_geeignet" in rows["way/3"].ort_typen
        assert rows["way/3"].qualitaet == "C"
        assert "gewaessernaehe" in rows["way/4"].ort_typen
        assert rows["way/5"].ort_typen == ["gleisbereich"]
        assert "parkplatz" in rows["way/6"].ort_typen
    finally:
        db.close()


# ── Ortswahl ────────────────────────────────────────────────────────────────

def _punkt(db, org_id, ident, typen, qualitaet="B", merkmale=None, objekt_id=None, lat=47.471):
    row = PlanspielAdresse(org_id=org_id, quelle="manuell", quell_id=ident, strasse=f"Weg {ident}", hausnr="1",
                           lat=lat, lng=9.751, qualitaet=qualitaet, ort_typen=typen, merkmale=merkmale,
                           objekt_id=objekt_id, aktiv=True)
    db.add(row)
    db.flush()
    return row


def test_ortwahl_regeln_seed_und_sperrfrist():
    from datetime import datetime, timedelta

    from app.services.planspiel_ort_service import OrtNichtGefunden, als_verwendet_markieren

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        set_tenant_context(db, None)
        with pytest.raises(OrtNichtGefunden):
            ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(1))

        a = _punkt(db, org.id, "a", ["wohngebaeude"])
        b = _punkt(db, org.id, "b", ["wohngebaeude"])
        _punkt(db, org.id, "see", ["gewaesser_boot"], merkmale={"gewaesser": "bodensee"})
        _punkt(db, org.id, "teich", ["gewaesser_boot"], merkmale={"gewaesser": "teich"})
        db.commit()

        with pytest.raises(OrtNichtGefunden, match="BMA"):
            ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"], "requires_verified_bma": True},
                        random.Random(1))
        boot = {"ort_typen": ["gewaesser_boot"], "geografie_bedingung": {"gewaesser": "bodensee"}}
        for seed in range(10):
            assert ort_waehlen(db, org.id, boot, random.Random(seed)).strasse == "Weg see"

        erster = ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(42))
        zweiter = ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(42))
        assert erster == zweiter

        jetzt = datetime(2026, 10, 14, 12, 0)
        als_verwendet_markieren(db, a.id, jetzt)
        db.flush()
        for seed in range(10):
            wahl = ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(seed), jetzt=jetzt)
            assert wahl.adresse_id == b.id
        als_verwendet_markieren(db, b.id, jetzt)
        db.flush()
        # Alle gesperrt -> trotzdem ein Ort statt Fehler
        assert ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(1), jetzt=jetzt)
        spaeter = jetzt + timedelta(hours=25)
        assert ort_waehlen(db, org.id, {"ort_typen": ["wohngebaeude"]}, random.Random(1), jetzt=spaeter)
    finally:
        db.close()


def test_ortwahl_ignoriert_fremde_org():
    from app.services.planspiel_ort_service import OrtNichtGefunden

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        eigene = _org(db)
        fremde = _org(db)
        set_tenant_context(db, None)
        _punkt(db, fremde.id, f"fremd-{uuid4().hex}", ["wohngebaeude"])
        db.commit()
        with pytest.raises(OrtNichtGefunden):
            ort_waehlen(db, eigene.id, {"ort_typen": ["wohngebaeude"]}, random.Random(1))
    finally:
        db.close()


# ── Zufallsszenario ─────────────────────────────────────────────────────────

def test_szenario_zufall_beachtet_pool_und_zufallsregeln():
    from app.services.planspiel_katalog_service import katalog_importieren
    from app.services.planspiel_ort_service import szenario_zufall

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        set_tenant_context(db, None)
        katalog_importieren(db, org.id)
        _punkt(db, org.id, "haus", ["wohngebaeude", "gebaeude", "beliebig", "beliebig_geeignet"])
        _punkt(db, org.id, "ort", ["ortsgebiet"])
        db.commit()

        gezogen = set()
        for seed in range(150):
            szenario, inhalt = szenario_zufall(db, org.id, random.Random(seed), art="einzel")
            gezogen.add(szenario.code)
            assert inhalt["kategorie"] != "gsl"
        assert not gezogen & {"T20", "B18", "B20", "B21", "B25", "T26", "T03"}
        assert not gezogen & {"F30", "B23", "B24", "T25"}
        assert {"B01", "B09"} & gezogen

        for seed in range(20):
            szenario, inhalt = szenario_zufall(db, org.id, random.Random(seed), art="gsl")
            assert szenario.code in {"G01", "G02", "G03", "G04", "G05"}
            assert inhalt["kategorie"] == "gsl"

        a = szenario_zufall(db, org.id, random.Random(7), art="einzel")[0].code
        assert a == szenario_zufall(db, org.id, random.Random(7), art="einzel")[0].code
    finally:
        db.close()


async def test_overpass_502_wechselt_endpunkt(monkeypatch):
    from unittest.mock import AsyncMock

    import httpx

    from app.config import settings
    from app.services import planspiel_ort_service as service

    fallback = "https://fallback.test/api/interpreter"
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", fallback)
    monkeypatch.setattr(service, "_OSM_WARTEN_S", (0.0, 0.0))
    post = AsyncMock(side_effect=[
        httpx.Response(502, request=httpx.Request("POST", settings.STRASSEN_OVERPASS_URL)),
        httpx.Response(200, json={"elements": [{"id": 1}]}, request=httpx.Request("POST", fallback)),
    ])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    assert await service._overpass_abrufen("query") == [{"id": 1}]
    assert [aufruf.args[0] for aufruf in post.call_args_list] == [settings.STRASSEN_OVERPASS_URL, fallback]
