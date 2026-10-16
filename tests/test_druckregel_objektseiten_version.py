"""Nur freigegebene aktuelle Objekt-Dokumentversionen werden gedruckt."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite
from app.services.print_dispatcher import _seiten_for_elements
from tests.conftest import TEST_DB_URL


@pytest.fixture
def db(setup_db):
    session = sessionmaker(bind=create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}))()
    set_tenant_context(session, None)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_objektseiten_aus_alter_oder_unfreigegebener_version_fehlten(db):
    objekt = Objekt(org_id=998802, nummer="O-1", name="Objekt")
    db.add(objekt)
    db.flush()
    aktuell = ObjektDokument(org_id=998802, objekt_id=objekt.id, dateiname_original="a.pdf", pfad="a.pdf")
    alt = ObjektDokument(org_id=998802, objekt_id=objekt.id, dateiname_original="b.pdf", pfad="b.pdf",
                         ist_aktuelle_version=False)
    offen = ObjektDokument(org_id=998802, objekt_id=objekt.id, dateiname_original="c.pdf", pfad="c.pdf",
                           freigabe_status="in_ueberarbeitung")
    db.add_all([aktuell, alt, offen])
    db.flush()
    seiten = [
        ObjektDokumentSeite(org_id=998802, objekt_id=objekt.id, dokument_id=dokument.id,
                             seiten_nr=1, einzel_pdf_pfad=f"{dokument.id}.pdf", bei_einsatz_drucken=True)
        for dokument in (aktuell, alt, offen)
    ]
    db.add_all(seiten)
    db.flush()
    assert [seite.id for seite in _seiten_for_elements(db, objekt.id, ["bei_einsatz_drucken"])] == [seiten[0].id]
