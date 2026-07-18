"""Rettungskarten-Katalog (Euro NCAP / CTIF Euro Rescue): Parsing, Sync, Suche, Oeffnen."""
import httpx
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.routing import Match

from app.db import Base
from app.models.nachschlagewerk import RettungsdatenblattCache, RettungskartenKatalog
from app.services import rettungskarten_katalog_service as rkk
from app.services import rettungskarten_service as rks
from tests.conftest import flatten_routes


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


_PDF = b"%PDF-1.4\n%fake rescue sheet\n"

SAMPLE = {"_rid": "x", "_count": 2, "Documents": [
    {"id": "35", "name": "A1", "make_name": "Audi", "model_name": "A1",
     "body_type": "Hatchback", "build_year_from": "2010", "build_year_until": "2018",
     "doors": "3", "powertrain": "Gasoline/Diesel", "picture_url": "http://pic/audi.png",
     "documents": [
         {"id": "56", "url": "http://x/en/A1_EN.pdf", "language": "EN", "type": "Rescue Sheet"},
         {"id": "57", "url": "http://x/de/A1_DE.pdf", "language": "DE", "type": "Rescue Sheet"},
         {"id": "58", "url": "http://x/de/A1_g_DE.pdf", "language": "DE", "type": "Rescue Guide"},
     ]},
    {"id": "83", "name": "e-tron", "make_name": "Audi", "model_name": "e-tron",
     "body_type": "SUV", "build_year_from": "2019", "doors": "5", "powertrain": "Electric",
     "documents": []},
    # Duplikat-ID -> muss verworfen werden
    {"id": "35", "name": "A1 (dup)", "make_name": "Audi", "documents": []},
]}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(
        bind=engine,
        tables=[RettungskartenKatalog.__table__, RettungsdatenblattCache.__table__])
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# ── Parsing ───────────────────────────────────────────────────────────────────

def test_parse_variants_grundfelder_und_dedupe():
    zeilen = rkk.parse_variants(SAMPLE)
    assert len(zeilen) == 2  # Duplikat-ID entfernt
    a1 = next(z for z in zeilen if z["quelle_id"] == "35")
    assert a1["hersteller"] == "Audi"
    assert a1["modell"] == "A1"
    assert a1["karosserie"] == "Hatchback"
    assert a1["baujahr_von"] == 2010 and a1["baujahr_bis"] == 2018
    assert a1["tueren"] == 3 and a1["antrieb"] == "Gasoline/Diesel"
    assert a1["bild_url"] == "http://pic/audi.png"


def test_bestes_dokument_bevorzugt_deutsches_rettungsblatt():
    zeilen = rkk.parse_variants(SAMPLE)
    a1 = next(z for z in zeilen if z["quelle_id"] == "35")
    # DE Rescue Sheet vor EN Rescue Sheet vor DE Rescue Guide
    assert a1["pdf_url"] == "http://x/de/A1_DE.pdf"
    assert a1["pdf_sprache"] == "DE"


def test_parse_variant_ohne_dokument():
    zeilen = rkk.parse_variants(SAMPLE)
    etron = next(z for z in zeilen if z["quelle_id"] == "83")
    assert etron["pdf_url"] is None and etron["pdf_sprache"] is None
    assert etron["baujahr_von"] == 2019 and etron["baujahr_bis"] is None


def test_liste_aus_antwort_akzeptiert_bare_liste():
    zeilen = rkk.parse_variants(SAMPLE["Documents"])
    assert len(zeilen) == 2


# ── Sync ────────────────────────────────────────────────────────────────────────

def test_sync_katalog_ersetzt(db, monkeypatch):
    monkeypatch.setattr(rkk, "_MIN_KATALOG", 2)
    monkeypatch.setattr(rkk, "_hole_rohdaten", lambda url: SAMPLE)
    monkeypatch.setattr(rkk.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL", "http://api.test")
    n = rkk.sync_katalog(db)
    assert n == 2
    assert rkk.anzahl(db) == 2
    # zweiter Lauf ersetzt (kein Duplizieren)
    assert rkk.sync_katalog(db) == 2
    assert rkk.anzahl(db) == 2


def test_sync_unplausibel_behaelt_bestand(db, monkeypatch):
    monkeypatch.setattr(rkk, "_MIN_KATALOG", 2)
    monkeypatch.setattr(rkk, "_hole_rohdaten", lambda url: SAMPLE)
    monkeypatch.setattr(rkk.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL", "http://api.test")
    assert rkk.sync_katalog(db) == 2
    # Jetzt zu wenige Zeilen -> nicht uebernehmen, Bestand bleibt
    monkeypatch.setattr(rkk, "_MIN_KATALOG", 99)
    assert rkk.sync_katalog(db) == -1
    assert rkk.anzahl(db) == 2


def test_sync_ohne_url(db, monkeypatch):
    monkeypatch.setattr(rkk.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL", "")
    assert rkk.sync_katalog(db) == -1


def test_sync_abruf_fehler(db, monkeypatch):
    monkeypatch.setattr(rkk.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL", "http://api.test")
    monkeypatch.setattr(rkk, "_hole_rohdaten", lambda url: None)
    assert rkk.sync_katalog(db) == -1


# ── Suche / index.json ──────────────────────────────────────────────────────────

def test_suche_katalog_mehrere_begriffe(db, monkeypatch):
    monkeypatch.setattr(rkk, "_MIN_KATALOG", 2)
    monkeypatch.setattr(rkk, "_hole_rohdaten", lambda url: SAMPLE)
    monkeypatch.setattr(rkk.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL", "http://api.test")
    rkk.sync_katalog(db)
    assert len(rkk.suche_katalog(db, "audi")) == 2
    assert len(rkk.suche_katalog(db, "audi a1")) == 1
    assert len(rkk.suche_katalog(db, "bmw")) == 0


def test_alle_als_dicts_hat_pdf_flag(db, monkeypatch):
    monkeypatch.setattr(rkk, "_MIN_KATALOG", 2)
    monkeypatch.setattr(rkk, "_hole_rohdaten", lambda url: SAMPLE)
    monkeypatch.setattr(rkk.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL", "http://api.test")
    rkk.sync_katalog(db)
    dicts = rkk.alle_als_dicts(db)
    assert {d["modell"]: d["hat_pdf"] for d in dicts} == {"A1": True, "e-tron": False}


# ── Oeffnen (on-demand cachen) ──────────────────────────────────────────────────

class _Resp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


class _Client:
    def __init__(self, resp, **kw):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return self._resp


def _kat(db, **kw):
    base = dict(quelle_id="35", hersteller="Audi", modell="A1", baujahr_von=2010,
                baujahr_bis=2018, antrieb="Electric", pdf_url="http://x/de/A1_DE.pdf")
    base.update(kw)
    e = RettungskartenKatalog(**base)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_hole_aus_katalog_laedt_und_cached(db, monkeypatch, tmp_path):
    monkeypatch.setattr(rks.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rks.httpx, "Client", lambda **kw: _Client(_Resp(_PDF)))
    kat = _kat(db)
    cache = rks.hole_aus_katalog(db, kat)
    assert cache is not None and cache.hat_pdf
    assert cache.bytes == len(_PDF) and cache.quelle == "http://x/de/A1_DE.pdf"
    assert rks.absolute_pfad(cache).read_bytes() == _PDF
    # zweiter Aufruf = Cache-Hit ohne Fetch
    monkeypatch.setattr(rks.httpx, "Client",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("kein Fetch")))
    assert rks.hole_aus_katalog(db, kat).id == cache.id


def test_hole_aus_katalog_ohne_pdf_url(db):
    kat = _kat(db, quelle_id="83", modell="e-tron", pdf_url=None)
    assert rks.hole_aus_katalog(db, kat) is None


# ── Routen-Registrierung ────────────────────────────────────────────────────────

def _match_endpoint(path: str, method: str = "GET") -> str | None:
    import app.main as m
    scope = {"type": "http", "method": method, "path": path}
    for r in flatten_routes(m.app.router.routes):
        matches = getattr(r, "matches", None)
        if matches is None:
            continue
        match, _ = matches(scope)
        if match == Match.FULL:
            return getattr(getattr(r, "endpoint", None), "__name__", None)
    return None


def test_katalog_json_route_registriert():
    assert _match_endpoint("/nachschlagewerke/rettungskarten/katalog.json") == "rettungskarten_katalog_json"


def test_katalog_oeffnen_route_nur_numerisch():
    assert _match_endpoint("/nachschlagewerke/rettungskarten/katalog/7/oeffnen") == "rettungskarten_katalog_oeffnen"
    assert _match_endpoint("/nachschlagewerke/rettungskarten/katalog/abc/oeffnen") is None
