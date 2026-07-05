"""Objektverwaltung PR 3: Dokumenten-Pipeline (Upload, Split, Klassifikation, Sammel-PDF)."""
import asyncio
import io
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.objekt import (
    DOKUMENT_STATUS_FERTIG,
    DOKUMENTARTEN,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)


def _test_pdf(seiten: int = 3) -> bytes:
    """Erzeugt ein Mini-PDF mit N leeren A4-Seiten via pypdf."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(seiten):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _fake_png() -> bytes:
    """Erzeugt ein kleines echtes PNG via Pillow."""
    from PIL import Image
    img = Image.new("RGB", (60, 85), color=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeUpload:
    """Minimaler UploadFile-Ersatz fuer store_dokument_upload."""
    def __init__(self, data: bytes, filename: str = "test.pdf"):
        self._data = data
        self.filename = filename

    async def read(self) -> bytes:
        return self._data


@pytest.fixture()
def pr3_env(tmp_path, monkeypatch):
    """In-Memory-DB + temporaeres Storage-Verzeichnis."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekt_media"))

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="pr3-org", name="Org PR3", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="pr3-org-b", name="Org PR3 B", color="#00ff00", bos="Feuerwehr")
    db.add_all([org, org_b])
    db.flush()
    objekt = Objekt(org_id=org.id, nummer=1, name="Testobjekt", status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.commit()

    # verarbeite_dokument nutzt SessionLocal → auf Test-Engine umbiegen
    import app.db as app_db
    monkeypatch.setattr(app_db, "SessionLocal", Session)

    yield db, org.id, org_b.id, objekt

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_dokumentarten_taxonomie():
    assert set(DOKUMENTARTEN) == {
        "bma_datenblatt", "bma_melderplan", "brandschutzplan",
        "gefahrgutdatenblatt", "lageplan", "objektinformation",
    }


def test_upload_und_verarbeitung(pr3_env):
    from app.services.objekt_dokument_service import (
        absolute_pfad,
        store_dokument_upload,
        verarbeite_dokument,
    )
    db, org_id, _, objekt = pr3_env

    pdf = _test_pdf(3)
    dokument = asyncio.run(store_dokument_upload(_FakeUpload(pdf), objekt, None, db))
    db.commit()
    assert dokument.seitenzahl == 3
    assert dokument.status == "neu"
    assert absolute_pfad(dokument.pfad).exists()

    # Verarbeitung mit injizierter Rasterfunktion (kein Poppler noetig)
    png = _fake_png()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: png)

    db.expire_all()
    dokument = db.get(ObjektDokument, dokument.id)
    assert dokument.status == DOKUMENT_STATUS_FERTIG
    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.dokument_id == dokument.id)
        .order_by(ObjektDokumentSeite.seiten_nr)
        .all()
    )
    assert [s.seiten_nr for s in seiten] == [1, 2, 3]
    for s in seiten:
        assert s.einzel_pdf_pfad and absolute_pfad(s.einzel_pdf_pfad).exists()
        assert s.bild_pfad and absolute_pfad(s.bild_pfad).exists()
        assert s.thumb_pfad and absolute_pfad(s.thumb_pfad).exists()
    assert dokument.belegt_bytes > dokument.groesse_bytes


def test_verarbeitung_ohne_rasterung(pr3_env):
    """Ohne Poppler (render_func → None) bleiben bild/thumb NULL, Split klappt trotzdem."""
    from app.services.objekt_dokument_service import store_dokument_upload, verarbeite_dokument
    db, org_id, _, objekt = pr3_env

    dokument = asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(2)), objekt, None, db))
    db.commit()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: None)

    db.expire_all()
    seiten = db.query(ObjektDokumentSeite).filter(
        ObjektDokumentSeite.dokument_id == dokument.id).all()
    assert len(seiten) == 2
    assert all(s.bild_pfad is None and s.thumb_pfad is None for s in seiten)
    assert all(s.einzel_pdf_pfad for s in seiten)


def test_upload_kein_pdf_abgelehnt(pr3_env):
    from fastapi import HTTPException

    from app.services.objekt_dokument_service import store_dokument_upload
    db, _, _, objekt = pr3_env
    with pytest.raises(HTTPException) as exc:
        asyncio.run(store_dokument_upload(_FakeUpload(_fake_png(), "bild.png"), objekt, None, db))
    assert exc.value.status_code == 415


def test_upload_seitenlimit(pr3_env, monkeypatch):
    from fastapi import HTTPException

    from app.config import settings as app_settings
    from app.services.objekt_dokument_service import store_dokument_upload
    db, _, _, objekt = pr3_env
    monkeypatch.setattr(app_settings, "OBJEKT_PDF_MAX_SEITEN", 2)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(3)), objekt, None, db))
    assert exc.value.status_code == 413


def test_sammel_pdf_reihenfolge(pr3_env):
    from pypdf import PdfReader

    from app.services.objekt_dokument_service import (
        sammel_pdf,
        store_dokument_upload,
        verarbeite_dokument,
    )
    db, _, _, objekt = pr3_env
    dokument = asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(3)), objekt, None, db))
    db.commit()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: None)
    db.expire_all()

    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.dokument_id == dokument.id)
        .order_by(ObjektDokumentSeite.seiten_nr.desc())  # bewusst umgekehrt
        .all()
    )
    pdf = sammel_pdf(seiten)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 3


def test_delete_dokument_gibt_quota_frei(pr3_env):
    from app.models.master import OrgStorageUsage
    from app.services.objekt_dokument_service import (
        absolute_pfad,
        delete_dokument,
        store_dokument_upload,
        verarbeite_dokument,
    )
    db, org_id, _, objekt = pr3_env
    dokument = asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(2)), objekt, None, db))
    db.commit()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: _fake_png())
    db.expire_all()

    dokument = db.get(ObjektDokument, dokument.id)
    verzeichnis = absolute_pfad(dokument.pfad).parent
    assert verzeichnis.exists()

    delete_dokument(dokument, db)
    db.commit()

    assert not verzeichnis.exists()
    assert db.query(ObjektDokument).count() == 0
    assert db.query(ObjektDokumentSeite).count() == 0
    usage = db.query(OrgStorageUsage).filter(OrgStorageUsage.org_id == org_id).first()
    assert usage is not None and usage.used_bytes == 0


def test_seiten_isolation(pr3_env):
    from app.services.objekt_dokument_service import store_dokument_upload, verarbeite_dokument
    db, org_a_id, org_b_id, objekt = pr3_env
    dokument = asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(1)), objekt, None, db))
    db.commit()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: None)
    db.expire_all()

    set_tenant_context(db, org_b_id)
    assert db.query(ObjektDokumentSeite).count() == 0
    assert db.query(ObjektDokument).count() == 0
    set_tenant_context(db, org_a_id)
    assert db.query(ObjektDokumentSeite).count() == 1
    set_tenant_context(db, None)


def test_pr3_registrierung():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "objekt_dokument" in _TENANT_TABLE_NAMES
    assert "objekt_dokument_seite" in _TENANT_TABLE_NAMES
    from app.routers.ui_objekt_dokumente import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/{objekt_id}/dokumente" in pfade
    assert "/objekte/{objekt_id}/dokumente/upload" in pfade
    assert "/objekt-medien/seite/{seite_id}/thumb" in pfade


# ── Objekt-Loeschung (Router-Helfer, org_admin/system_admin) ──────────────────

def test_objekt_loeschen_raeumt_dateien_und_quota(pr3_env):
    """_loesche_objekt loescht Dokument-Verzeichnisse, gibt Quota frei und
    entfernt Objekt + Dokument-Zeilen (Kinder via Kaskade)."""
    from types import SimpleNamespace

    from app.models.master import OrgStorageUsage
    from app.routers.ui_objekt import _loesche_objekt
    from app.services.objekt_dokument_service import (
        absolute_pfad,
        store_dokument_upload,
        verarbeite_dokument,
    )
    db, org_id, _, objekt = pr3_env

    dokument = asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(2)), objekt, None, db))
    db.commit()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: _fake_png())
    db.expire_all()
    dokument = db.get(ObjektDokument, dokument.id)
    verzeichnis = absolute_pfad(dokument.pfad).parent
    assert verzeichnis.exists()
    usage = db.query(OrgStorageUsage).filter(OrgStorageUsage.org_id == org_id).first()
    assert usage is not None and usage.used_bytes > 0

    objekt_id = objekt.id
    _loesche_objekt(db, objekt, SimpleNamespace(id=None))
    db.commit()

    assert not verzeichnis.exists()
    db.expire_all()
    usage = db.query(OrgStorageUsage).filter(OrgStorageUsage.org_id == org_id).first()
    assert usage is not None and usage.used_bytes == 0
    assert db.query(Objekt).filter(Objekt.id == objekt_id).first() is None
    assert db.query(ObjektDokument).count() == 0


def test_objekt_loeschen_ohne_dokumente(pr3_env):
    from types import SimpleNamespace

    from app.routers.ui_objekt import _loesche_objekt
    db, org_id, _, _objekt = pr3_env

    leer = Objekt(org_id=org_id, nummer=99, name="Ohne Dokumente",
                  status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(leer)
    db.commit()
    _loesche_objekt(db, leer, SimpleNamespace(id=None))
    db.commit()
    assert db.query(Objekt).filter(Objekt.nummer == 99).first() is None


def test_bulk_loeschen_route_registriert():
    from app.routers.ui_objekt import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/bulk-loeschen" in pfade
    assert "/objekte/{objekt_id}/loeschen" in pfade
