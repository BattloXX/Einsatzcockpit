"""api_import.py (temporaerer EUS-Migrations-Endpunkt): Auth, Upload, Klassifizierung.

Diese Datei wird zusammen mit app/routers/api_import.py nach Abschluss der
EUS-Migration wieder entfernt.
"""
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.main import app
from app.models.master import FireDept
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektDokumentSeite
from app.routers.api_import import _match_dokumentart


def _test_pdf(seiten: int = 2) -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(seiten):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── Dokumentart-Heuristik ──────────────────────────────────────────────────────

@pytest.mark.parametrize("label,erwartet", [
    ("Brandschutzplan", "brandschutzplan"),
    ("BSP Erdgeschoss", "brandschutzplan"),
    ("Melderplan Linie 12", "bma_melderplan"),
    ("Übersichtsplan", "lageplan"),
    ("Grundriss EG", "lageplan"),
    ("Gefahrgutdatenblatt Lager", "gefahrgutdatenblatt"),
    ("", None),
    (None, None),
    ("Foto vom Eingang", None),  # kein Treffer -> unklassifiziert statt geraten
])
def test_match_dokumentart(label, erwartet):
    assert _match_dokumentart(label) == erwartet


# ── In-Memory-DB ueber alle drei Code-Pfade hinweg identisch ──────────────────
# (Endpunkt-Dependency, verarbeite_dokument-Background-Session, Test-Setup) —
# Muster aus test_objekt_pr3.py: SessionLocal wird innerhalb der jeweiligen
# Funktion importiert, daher wirkt das Monkeypatch von app.db.SessionLocal.

@pytest.fixture()
def import_env(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekt_media"))
    monkeypatch.setattr(app_settings, "IMPORT_API_KEY", "test-key-123")

    # StaticPool: der Endpunkt ist async (wegen store_dokument_upload), seine
    # DB-Dependency aber sync -> FastAPI fuehrt Dependency und Endpunkt-Body in
    # unterschiedlichen Threads aus. Ohne StaticPool bekaeme jeder Thread eine
    # eigene, separate SQLite-:memory:-Verbindung (keine gemeinsamen Tabellen).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    import app.db as app_db
    monkeypatch.setattr(app_db, "SessionLocal", Session)

    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="import-org", name="Import Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    objekt = Objekt(org_id=org.id, nummer=1, name="Testobjekt", status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.commit()
    objekt_id = objekt.id
    db.close()

    client = TestClient(app)
    yield client, Session, objekt_id

    Base.metadata.drop_all(bind=engine)


# ── Auth ───────────────────────────────────────────────────────────────────────

def test_falscher_key_403(import_env):
    client, _Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("test.pdf", _test_pdf(), "application/pdf")},
        headers={"X-Import-Key": "falsch"},
    )
    assert resp.status_code == 403


def test_key_header_fehlt_422(import_env):
    client, _Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("test.pdf", _test_pdf(), "application/pdf")},
    )
    assert resp.status_code == 422


def test_leerer_key_fail_closed(import_env, monkeypatch):
    """Leerer IMPORT_API_KEY -> Endpunkt fuer NIEMANDEN nutzbar, auch nicht mit leerem Header."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "IMPORT_API_KEY", "")
    client, _Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("test.pdf", _test_pdf(), "application/pdf")},
        headers={"X-Import-Key": ""},
    )
    assert resp.status_code == 403


def test_unbekanntes_objekt_404(import_env):
    client, _Session, _objekt_id = import_env
    resp = client.post(
        "/api/import/dokument/999999",
        files={"file": ("test.pdf", _test_pdf(), "application/pdf")},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert resp.status_code == 404


# ── Upload + Split + Klassifizierung ───────────────────────────────────────────

def test_upload_split_und_klassifizierung(import_env):
    client, Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("Melderplan.pdf", _test_pdf(2), "application/pdf")},
        params={
            "dok_typ": 5,  # EUS "Laufkarte" -> bma_melderplan
            "favorit": "true",
            "melderlinie": "12",
            "stand": "2024-03-15 00:00:00",
            "bemerkung": "Laufkarten EG",
        },
        headers={"X-Import-Key": "test-key-123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["seitenzahl"] == 2
    assert body["seiten_erzeugt"] == 2
    assert body["dokumentart"] == "bma_melderplan"
    assert body["status"] == "fertig"

    db = Session()
    set_tenant_context(db, None)
    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.dokument_id == body["id"])
        .all()
    )
    assert len(seiten) == 2
    for s in seiten:
        assert s.dokumentart == "bma_melderplan"
        assert s.bei_einsatz_drucken is True
        assert s.melderlinien == "12"
        assert s.stand is not None and s.stand.isoformat() == "2024-03-15"
        assert s.titel == "Laufkarten EG"
    db.close()


def test_upload_label_fallback_wenn_dok_typ_unbekannt(import_env):
    """dok_typ ohne Mapping (0/Sonstiges) -> Fuzzy-Match auf dok_typ_label."""
    client, _Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("BSP.pdf", _test_pdf(1), "application/pdf")},
        params={"dok_typ": 0, "dok_typ_label": "Brandschutzplan EG"},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert resp.status_code == 200
    assert resp.json()["dokumentart"] == "brandschutzplan"


def test_upload_einsatzdruck_oder_logik(import_env):
    """bei_einsatz_drucken = favorit ODER dl ODER bmzfbf ODER sammelplatz."""
    client, Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("BMZ.pdf", _test_pdf(1), "application/pdf")},
        params={"bmzfbf": "true"},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert resp.status_code == 200
    db = Session()
    set_tenant_context(db, None)
    seite = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.dokument_id == resp.json()["id"])
        .first()
    )
    assert seite is not None and seite.bei_einsatz_drucken is True
    db.close()


def test_upload_idempotent_gleicher_dateiname(import_env):
    """Zweiter Upload mit gleichem Dateinamen legt kein Duplikat an."""
    client, Session, objekt_id = import_env
    pdf = _test_pdf(1)
    erste = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("Doppelt.pdf", pdf, "application/pdf")},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert erste.status_code == 200
    zweite = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("Doppelt.pdf", pdf, "application/pdf")},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert zweite.status_code == 200
    assert zweite.json()["id"] == erste.json()["id"]
    assert zweite.json().get("duplikat") is True

    db = Session()
    set_tenant_context(db, None)
    from app.models.objekt import ObjektDokument
    anzahl = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.dateiname_original == "Doppelt.pdf")
        .count()
    )
    assert anzahl == 1
    db.close()


def test_upload_ohne_klassifizierungshinweise_bleibt_unklassifiziert(import_env):
    client, Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("Sonstiges.pdf", _test_pdf(1), "application/pdf")},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dokumentart"] is None

    db = Session()
    set_tenant_context(db, None)
    seite = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.dokument_id == body["id"])
        .first()
    )
    assert seite is not None
    assert seite.dokumentart is None
    assert seite.bei_einsatz_drucken is False
    assert seite.melderlinien is None
    db.close()


def test_upload_kein_pdf_415(import_env):
    client, _Session, objekt_id = import_env
    resp = client.post(
        f"/api/import/dokument/{objekt_id}",
        files={"file": ("bild.png", b"\x89PNG\r\n\x1a\nnicht wirklich ein bild", "image/png")},
        headers={"X-Import-Key": "test-key-123"},
    )
    assert resp.status_code == 415


def test_router_registriert():
    from app.routers.api_import import router
    pfade = {r.path for r in router.routes}
    assert "/api/import/dokument/{objekt_id}" in pfade
