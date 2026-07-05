"""Objektverwaltung PR 9: Offline-Sync-Manifest (Android-Precaching)."""
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
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.services.objekt_service import build_sync_manifest


@pytest.fixture(scope="module")
def sync_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org_a = FireDept(slug="sync-a", name="Sync A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="sync-b", name="Sync B", color="#00ff00", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    frei = Objekt(org_id=org_a.id, nummer=1, name="Freigegeben",
                  status=OBJEKT_STATUS_FREIGEGEBEN)
    entwurf = Objekt(org_id=org_a.id, nummer=2, name="Entwurf",
                     status=OBJEKT_STATUS_ENTWURF)
    fremd = Objekt(org_id=org_b.id, nummer=1, name="Fremde Org",
                   status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add_all([frei, entwurf, fremd])
    db.flush()

    dokument = ObjektDokument(org_id=org_a.id, objekt_id=frei.id,
                              dateiname_original="plan.pdf", pfad="a/b/original.pdf",
                              seitenzahl=2, status="fertig")
    db.add(dokument)
    db.flush()
    db.add(ObjektDokumentSeite(org_id=org_a.id, objekt_id=frei.id, dokument_id=dokument.id,
                               seiten_nr=1, einzel_pdf_pfad="a/b/seite_0001.pdf",
                               bild_pfad="a/b/seite_0001.png", thumb_pfad="a/b/seite_0001_thumb.jpg",
                               dokumentart="bma_melderplan"))
    db.add(ObjektDokumentSeite(org_id=org_a.id, objekt_id=frei.id, dokument_id=dokument.id,
                               seiten_nr=2, einzel_pdf_pfad="a/b/seite_0002.pdf"))
    db.commit()

    yield db, org_a.id, org_b.id, frei.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_manifest_nur_freigegebene(sync_db):
    db, org_a_id, _, frei_id = sync_db
    manifest = build_sync_manifest(db, org_a_id)
    namen = [o["name"] for o in manifest["objekte"]]
    assert namen == ["Freigegeben"]
    assert manifest["objekte"][0]["einsatz_url"] == f"/objekte/{frei_id}/einsatz"


def test_manifest_seiten_urls(sync_db):
    db, org_a_id, _, _ = sync_db
    manifest = build_sync_manifest(db, org_a_id)
    seiten = manifest["objekte"][0]["seiten"]
    assert len(seiten) == 2
    # Seite 1: alle drei Dateien
    urls1 = seiten[0]["urls"]
    assert any(u.endswith("/thumb") for u in urls1)
    assert any(u.endswith("/bild") for u in urls1)
    assert any(u.endswith("/pdf") for u in urls1)
    # Seite 2: nur Einzel-PDF (kein Rendering)
    urls2 = seiten[1]["urls"]
    assert len(urls2) == 1 and urls2[0].endswith("/pdf")


def test_manifest_org_isolation(sync_db):
    db, org_a_id, org_b_id, _ = sync_db
    manifest_b = build_sync_manifest(db, org_b_id)
    namen = [o["name"] for o in manifest_b["objekte"]]
    assert namen == ["Fremde Org"]
    assert all(o["name"] != "Freigegeben" for o in manifest_b["objekte"])


def test_manifest_versionsindikator(sync_db):
    db, org_a_id, _, _ = sync_db
    manifest = build_sync_manifest(db, org_a_id)
    assert manifest["version"] == 1
    assert manifest["objekte"][0]["aktualisiert_am"] is not None


def test_pr9_endpoint_registriert():
    from app.routers.ui_objekt_dokumente import router
    pfade = {r.path for r in router.routes}
    assert "/api/objekte/sync" in pfade
