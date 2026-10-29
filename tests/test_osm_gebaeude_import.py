"""Lokaler Import mit synthetischen PBF-Dateien; saemtliche HTTP-Aufrufe sind lokal gemockt."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import osmium
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.osm import OsmGebaeude, OsmImportLauf
from app.services import objekt_gebaeude_service as abfrage
from app.services import osm_gebaeude_import as osm

URL = "https://osm.test/region.osm.pbf"


@pytest.fixture
def osm_umgebung(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'osm.db'}", connect_args={"check_same_thread": False})
    OsmImportLauf.__table__.create(engine)
    OsmGebaeude.__table__.create(engine)
    sitzung = sessionmaker(bind=engine)
    monkeypatch.setattr(osm, "SessionLocal", sitzung)
    monkeypatch.setattr(abfrage, "SessionLocal", sitzung)
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_DATA_DIR", str(tmp_path / "daten"))
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", f"region={URL}")
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_IMPORT_ENABLED", True)
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_MAX_AENDERUNG_ANTEIL", 0.2)
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_MAX_ALTER_TAGE", 30)
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_AKTUALISIERUNG_TAGE", 7)
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_MAX_DOWNLOAD_MB", 1200)
    zustand = {"inhalt": b"", "status": 200, "anfragen": [], "headers": {}, "fehler": None}
    original_client = httpx.Client

    def antwort(request):
        zustand["anfragen"].append(request)
        if zustand["fehler"]:
            raise zustand["fehler"]
        return httpx.Response(zustand["status"], content=zustand["inhalt"], headers={
            "last-modified": datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT"), **zustand["headers"],
        })

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(antwort), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    zaehler = 0

    def pbf(gebaeude=None, *, ungueltige=False, datenstand=None):
        nonlocal zaehler
        zaehler += 1
        pfad = tmp_path / f"synthetisch{zaehler}.osm.pbf"
        header = osmium.io.Header()
        if datenstand:
            header.set("osmosis_replication_timestamp", datenstand)
        with osmium.SimpleWriter(str(pfad), header=header) as writer:
            node_id = 1
            for way_id, ecken in (gebaeude or {1: [[47, 9], [47.001, 9], [47, 9.001]]}).items():
                ids = []
                for lat, lng in ecken:
                    writer.add_node(osmium.osm.mutable.Node(id=node_id, location=(lng, lat)))
                    ids.append(node_id)
                    node_id += 1
                writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=ids + ids[:1], tags={"building": "yes"}))
            if ungueltige:
                for way_id, nodes, tags in (
                    (800, [1, 2, 3], {"building": "yes"}),
                    (801, [1, 999999, 3, 1], {"building": "yes"}),
                    (802, [1, 2, 3, 1], {"highway": "service"}),
                    (803, [1, 2, 1], {"building": "yes"}),
                ):
                    writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=nodes, tags=tags))
        zustand["inhalt"] = pfad.read_bytes()
        return pfad

    yield sitzung, pbf, zustand
    engine.dispose()


def _bestand(anzahl=20):
    return {i + 1: [[47 + i * .001, 9], [47.0002 + i * .001, 9], [47 + i * .001, 9.0002]]
            for i in range(anzahl)}


def test_erstimport_ringfilter_huelle_header(osm_umgebung):
    sitzung, pbf, _ = osm_umgebung
    pbf(ungueltige=True, datenstand="2026-10-01T12:30:00Z")
    lauf = osm.importieren("region", URL)
    assert lauf.status == "aktiv", lauf.fehler_code
    assert lauf.anzahl == lauf.neu == 1
    assert lauf.datenstand == datetime(2026, 10, 1, 12, 30)  # noqa: DTZ001 -- DB naive UTC
    assert (lauf.min_lat, lauf.max_lat, lauf.min_lng, lauf.max_lng) == (47, 47.001, 9, 9.001)
    with sitzung() as db:
        zeile = db.scalar(select(OsmGebaeude))
        assert zeile.osm_way_id == 1 and len(zeile.ecken_hash) == 64
        assert zeile.ecken_json == "[[47.0,9.0],[47.001,9.0],[47.0,9.001]]"


def test_kleine_aenderung_sql_zaehler_und_aufraeumen(osm_umgebung, monkeypatch):
    sitzung, pbf, _ = osm_umgebung
    monkeypatch.setattr(osm, "_BATCH", 2)
    daten = _bestand()
    pbf(daten)
    alt = osm.importieren("region", URL)
    del daten[1]
    daten[2][0][0] += .00001
    daten[21] = [[47.1, 9], [47.1002, 9], [47.1, 9.0002]]
    pbf(daten)
    neu = osm.importieren("region", URL, erzwingen=True)
    assert (neu.status, neu.neu, neu.geaendert, neu.entfernt) == ("aktiv", 1, 1, 1)
    with sitzung() as db:
        assert db.get(OsmImportLauf, alt.id).status == "ersetzt"
        assert db.scalar(select(func.count()).select_from(OsmGebaeude).where(OsmGebaeude.lauf_id == alt.id)) == 0
    dritter = osm.importieren("region", URL, erzwingen=True)
    assert dritter.status == "aktiv"
    with sitzung() as db:
        assert db.get(OsmImportLauf, alt.id) is None
        assert db.scalar(select(func.count()).select_from(OsmImportLauf).where(OsmImportLauf.status == "ersetzt")) == 1


def test_grosse_aenderung_braucht_aktivierung(osm_umgebung):
    sitzung, pbf, _ = osm_umgebung
    pbf(_bestand())
    alt = osm.importieren("region", URL)
    pbf()
    neu = osm.importieren("region", URL, erzwingen=True)
    assert neu.status == "pruefung" and neu.entfernt == 19
    assert osm.status()[0]["aktiv"]["id"] == alt.id
    assert osm.status()[0]["pruefung"][0]["id"] == neu.id
    assert osm.aktivieren(neu.id).status == "aktiv"
    with sitzung() as db:
        assert db.get(OsmImportLauf, alt.id).status == "ersetzt"
        assert db.scalar(select(func.count()).select_from(OsmGebaeude)) == 1
    with pytest.raises(ValueError, match="Pruefung"):
        osm.aktivieren(neu.id)


def test_intervall_erzwungen_und_304(osm_umgebung):
    sitzung, pbf, zustand = osm_umgebung
    pbf()
    alt = osm.importieren("region", URL)
    assert osm.importieren("region", URL).ergebnis == "uebersprungen"
    assert len(zustand["anfragen"]) == 1
    zustand["status"] = 304
    unveraendert = osm.importieren("region", URL, erzwingen=True)
    assert unveraendert.ergebnis == "unveraendert"
    assert unveraendert.id == alt.id and unveraendert.beendet_am == alt.beendet_am
    assert "if-modified-since" in zustand["anfragen"][-1].headers
    with sitzung() as db:
        assert db.scalar(select(func.count()).select_from(OsmImportLauf)) == 1
    zustand["status"] = 200
    assert osm.importieren("region", URL, erzwingen=True).id != alt.id


@pytest.mark.parametrize("fall,code", [
    ("groesse", "OSM_DOWNLOAD_ZU_GROSS"), ("stream_groesse", "OSM_DOWNLOAD_ZU_GROSS"),
    ("http", "OSM_DOWNLOAD_HTTP"), ("timeout", "OSM_DOWNLOAD_TIMEOUT"),
    ("pbf", "OSM_PARSE_FEHLER"), ("db", "OSM_DB_FEHLER"), ("redirect", "OSM_DOWNLOAD_HTTP"),
])
def test_fehler_lassen_aktiven_bestand_unberuehrt(osm_umgebung, monkeypatch, fall, code):
    from sqlalchemy.exc import SQLAlchemyError

    sitzung, pbf, zustand = osm_umgebung
    pbf()
    alt = osm.importieren("region", URL)
    if fall in ("groesse", "stream_groesse"):
        monkeypatch.setattr(settings, "OSM_GEBAEUDE_MAX_DOWNLOAD_MB", 0)
        if fall == "stream_groesse":
            zustand["headers"] = {"content-length": "0"}
    elif fall == "http":
        zustand["status"] = 503
    elif fall == "timeout":
        zustand["fehler"] = httpx.ReadTimeout("Test")
    elif fall == "pbf":
        zustand["inhalt"] = b"kaputtes PBF"
    elif fall == "redirect":
        zustand["status"] = 302
        zustand["headers"] = {"location": "http://unsicher.test/pbf"}
    else:
        def fehler(db, lauf, pfad):
            db.execute(OsmGebaeude.__table__.insert().values(
                lauf_id=lauf.id, osm_way_id=77, ecken_json="[]", ecken_hash="x", min_lat=0, max_lat=0,
                min_lng=0, max_lng=0,
            ))
            db.commit()
            raise SQLAlchemyError("Test")
        monkeypatch.setattr(osm, "_einlesen", fehler)
    neu = osm.importieren("region", URL, erzwingen=True)
    assert neu.status == "fehler" and neu.fehler_code == code
    with sitzung() as db:
        assert db.get(OsmImportLauf, alt.id).status == "aktiv"
        assert db.scalar(select(func.count()).select_from(OsmGebaeude)) == 1
    assert not list(Path(settings.OSM_GEBAEUDE_DATA_DIR).glob("*.tmp"))


def test_quellen_validieren(monkeypatch):
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN",
                        f"region={URL},falsch=http://osm.test/x,Ungueltig={URL},../x={URL},region={URL},x=https://")
    assert osm.quellen() == [("region", URL)]


@pytest.mark.asyncio
async def test_loop_startverzoegerung_und_quellenfehler(monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "OSM_GEBAEUDE_IMPORT_ENABLED", True)
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", f"eins={URL},zwei={URL}")
    pausen, importe = [], []

    async def schlafen(sekunden):
        pausen.append(sekunden)
        if len(pausen) == 2:
            raise asyncio.CancelledError

    def importieren(name, url):
        importe.append(name)
        if name == "eins":
            raise ValueError("Testfehler je Quelle")

    monkeypatch.setattr(osm.asyncio, "sleep", schlafen)
    monkeypatch.setattr(osm, "importieren", importieren)
    with pytest.raises(asyncio.CancelledError):
        await osm.osm_gebaeude_loop()
    assert pausen[0] == 120 and 0 < pausen[1] <= 25 * 3600
    assert importe == ["eins", "zwei"]
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_IMPORT_ENABLED", False)
    await osm.osm_gebaeude_loop()
    assert len(pausen) == 2


def test_main_registriert_osm_loop(monkeypatch):
    from tests.background_loop_helpers import registered_background_loop_names

    assert "osm_gebaeude_loop" in registered_background_loop_names(monkeypatch)


@pytest.mark.parametrize("phase", ["anlegen", "umschalten"])
def test_db_commitfehler_rollback_und_fehlerlauf(osm_umgebung, monkeypatch, phase):
    from sqlalchemy.exc import SQLAlchemyError

    sitzung, pbf, _ = osm_umgebung
    pbf()
    alt = osm.importieren("region", URL)
    original = sitzung.class_.commit
    ausgeloest = False

    def commit(db):
        nonlocal ausgeloest
        kandidaten = db.new if phase == "anlegen" else db.dirty
        zielstatus = "laeuft" if phase == "anlegen" else "ersetzt"
        if not ausgeloest and any(isinstance(lauf, OsmImportLauf) and lauf.status == zielstatus
                                 for lauf in kandidaten):
            ausgeloest = True
            raise SQLAlchemyError("Simulierter Commitfehler")
        original(db)

    monkeypatch.setattr(sitzung.class_, "commit", commit)
    fehler = osm.importieren("region", URL, erzwingen=True)
    assert ausgeloest and fehler.id is not None
    assert fehler.status == "fehler" and fehler.fehler_code == "OSM_DB_FEHLER"
    with sitzung() as db:
        assert db.get(OsmImportLauf, alt.id).status == "aktiv"
        assert db.get(OsmImportLauf, fehler.id).status == "fehler"
        assert db.scalar(select(func.count()).select_from(OsmGebaeude)) == 1


def test_nur_neueste_pruefung_behaelt_zeilen(osm_umgebung):
    sitzung, pbf, _ = osm_umgebung
    pbf(_bestand())
    alt = osm.importieren("region", URL)
    pbf()
    erste = osm.importieren("region", URL, erzwingen=True)
    pbf()
    zweite = osm.importieren("region", URL, erzwingen=True)
    assert erste.status == "pruefung" and zweite.status == "pruefung"
    with sitzung() as db:
        assert db.get(OsmImportLauf, erste.id).status == "verworfen"
        assert db.scalar(select(func.count()).select_from(OsmGebaeude).where(OsmGebaeude.lauf_id == erste.id)) == 0
        assert db.get(OsmImportLauf, zweite.id).status == "pruefung"
        assert db.get(OsmImportLauf, alt.id).status == "aktiv"
    assert [p["id"] for p in osm.status()[0]["pruefung"]] == [zweite.id]
