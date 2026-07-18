"""Nachschlagewerke PR 4: Rettungsdatenblatt-Modell + on-demand fetch/Cache."""
import httpx
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.nachschlagewerk import RettungsdatenblattCache
from app.services import rettungskarten_service as rks


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


_PDF = b"%PDF-1.4\n%fake rescue sheet\n"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[RettungsdatenblattCache.__table__])
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


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


# ── Modell / Helfer ───────────────────────────────────────────────────────────

def test_anzeige_name_und_hat_pdf():
    e = RettungsdatenblattCache(hersteller="VW", modell="Golf VII", baujahr_von=2012,
                                baujahr_bis=2019, pfad="rettungskarten/x/original.pdf")
    assert e.hat_pdf is True
    assert e.anzeige_name == "VW Golf VII (2012-2019)"
    e2 = RettungsdatenblattCache(hersteller="Audi", modell="A3")
    assert e2.hat_pdf is False
    assert e2.anzeige_name == "Audi A3"


def test_deep_links_immer_erzeugbar():
    links = rks.deep_links("VW", "Golf")
    urls = " ".join(link["url"] for link in links)
    assert links and "euroncap" in urls and "adac" in urls
    assert "eurorescue.org" not in urls  # tote Domain darf nicht zurueckkehren


# ── Cache-Lookup ──────────────────────────────────────────────────────────────

def test_finde_und_suche(db):
    db.add(RettungsdatenblattCache(hersteller="VW", modell="Golf VII", baujahr_von=2012))
    db.commit()
    assert rks.finde(db, "VW", "Golf VII", 2012) is not None
    assert rks.finde(db, "VW", "Golf VII", 2099) is None
    assert len(rks.suche(db, "golf")) == 1
    assert len(rks.suche(db, "audi")) == 0


def test_finde_oder_hole_cache_hit_ohne_fetch(db, monkeypatch):
    db.add(RettungsdatenblattCache(hersteller="VW", modell="Golf", baujahr_von=None,
                                   pfad="rettungskarten/x/original.pdf"))
    db.commit()

    def _boom(**kw):
        raise AssertionError("es darf kein Fetch passieren")
    monkeypatch.setattr(rks.httpx, "Client", _boom)

    eintrag, links = rks.finde_oder_hole(db, "VW", "Golf")
    assert eintrag is not None and eintrag.pfad
    assert links  # Deep-Links werden trotzdem mitgegeben


def test_finde_oder_hole_ohne_quelle_nur_deeplinks(db, monkeypatch):
    monkeypatch.setattr(rks.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE", "")
    eintrag, links = rks.finde_oder_hole(db, "Audi", "A4")
    assert eintrag is None
    assert links


def test_finde_oder_hole_holt_und_cached(db, monkeypatch, tmp_path):
    monkeypatch.setattr(rks.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rks.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE",
                        "https://example.test/{hersteller}/{modell}.pdf")
    monkeypatch.setattr(rks.httpx, "Client", lambda **kw: _Client(_Resp(_PDF)))

    eintrag, links = rks.finde_oder_hole(db, "VW", "Passat", baujahr_von=2015)
    assert eintrag is not None
    assert eintrag.pfad and eintrag.bytes == len(_PDF)
    assert eintrag.sha256
    pdf = rks.absolute_pfad(eintrag)
    assert pdf is not None and pdf.exists()
    assert pdf.read_bytes() == _PDF
    # zweiter Aufruf = Cache-Hit (kein neuer DB-Eintrag)
    monkeypatch.setattr(rks.httpx, "Client",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("kein Fetch")))
    eintrag2, _ = rks.finde_oder_hole(db, "VW", "Passat", baujahr_von=2015)
    assert eintrag2.id == eintrag.id


def test_finde_oder_hole_kein_pdf_wird_verworfen(db, monkeypatch, tmp_path):
    monkeypatch.setattr(rks.settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rks.settings, "NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE",
                        "https://example.test/{hersteller}/{modell}.pdf")
    monkeypatch.setattr(rks.httpx, "Client", lambda **kw: _Client(_Resp(b"<html>nope</html>")))
    eintrag, links = rks.finde_oder_hole(db, "VW", "Tiguan")
    assert eintrag is None
    assert rks.finde(db, "VW", "Tiguan") is None
