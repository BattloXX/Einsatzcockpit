"""Objektdokumente: Versionsgruppen und produktive Lesesicht."""
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektDokument, ObjektDokumentSeite
from app.routers.ui_objekt_dokumente import _galerie_context, _seite_fuer_user, dokumente_sammel_pdf, seite_pdf
from app.services.objekt_dokument_service import hole_dokument_gruppe, naechste_versionsnummer
from app.services.objekt_service import build_sync_manifest


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def version_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="dokument-version", name="Dokument Version", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    objekt = Objekt(org_id=org.id, nummer=1, name="Versionsobjekt", status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.flush()
    yield db, org, objekt
    db.close()
    Base.metadata.drop_all(bind=engine)


def _dokument(org_id, objekt_id, name, **werte):
    return ObjektDokument(
        org_id=org_id,
        objekt_id=objekt_id,
        dateiname_original=name,
        pfad=f"dokumente/{name}",
        status="fertig",
        **werte,
    )


def test_versionsgruppe_und_standardwerte(version_db):
    db, org, objekt = version_db
    genesis = _dokument(org.id, objekt.id, "genesis.pdf")
    db.add(genesis)
    db.flush()
    version_2 = _dokument(org.id, objekt.id, "v2.pdf", dokument_gruppe_id=genesis.id, versionsnummer=2)
    version_3 = _dokument(org.id, objekt.id, "v3.pdf", dokument_gruppe_id=genesis.id, versionsnummer=3)
    db.add_all([version_2, version_3])
    db.flush()

    assert [d.id for d in hole_dokument_gruppe(db, genesis)] == [genesis.id, version_2.id, version_3.id]
    assert [d.id for d in hole_dokument_gruppe(db, version_3)] == [genesis.id, version_2.id, version_3.id]
    assert naechste_versionsnummer(db, version_2) == 4
    assert genesis.ist_aktuelle_version is True
    assert genesis.freigabe_status == "freigegeben"
    assert genesis.versionsnummer == 1
    assert genesis.dokument_gruppe_id is None


def test_produktive_sicht_ignoriert_alte_versionen(version_db, monkeypatch, tmp_path):
    db, org, objekt = version_db
    aktuell = _dokument(org.id, objekt.id, "aktuell.pdf")
    db.add(aktuell)
    db.flush()
    alt = _dokument(
        org.id,
        objekt.id,
        "alt.pdf",
        dokument_gruppe_id=aktuell.id,
        versionsnummer=2,
        ist_aktuelle_version=False,
    )
    db.add(alt)
    db.flush()
    aktuelle_seiten = [
        ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=aktuell.id, seiten_nr=1,
                             dokumentart="bma_melderplan", einzel_pdf_pfad="aktuell-1.pdf"),
        ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=aktuell.id, seiten_nr=2,
                             einzel_pdf_pfad="aktuell-2.pdf"),
    ]
    alte_seiten = [
        ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=alt.id, seiten_nr=1,
                             dokumentart="feuerwehrplan", einzel_pdf_pfad="alt-1.pdf"),
        ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=alt.id, seiten_nr=2,
                             einzel_pdf_pfad="alt-2.pdf"),
    ]
    db.add_all(aktuelle_seiten + alte_seiten)
    db.commit()

    monkeypatch.setattr("app.core.permissions.is_objekt_verwalter", lambda user: False)
    user = SimpleNamespace(is_system_admin=False, org_id=org.id)
    request = Request({"type": "http", "method": "GET", "path": "/"})
    context = _galerie_context(request, db, user, objekt)
    assert [seite.id for seite in context["seiten"]] == [seite.id for seite in aktuelle_seiten]
    assert context["zaehler"] == {"bma_melderplan": 1}
    assert context["gesamt"] == 2
    assert context["unklassifiziert"] == 1
    assert [d.id for d in context["dokumente"]] == [aktuell.id]

    gemergte_seiten = []
    monkeypatch.setattr("app.routers.ui_objekt_dokumente._objekt_or_404", lambda *args: objekt)
    monkeypatch.setattr(
        "app.routers.ui_objekt_dokumente.sammel_pdf",
        lambda seiten: gemergte_seiten.extend(seiten) or b"pdf",
    )
    response = dokumente_sammel_pdf(objekt.id, request, db, user)
    assert response.body == b"pdf"
    assert [seite.id for seite in gemergte_seiten] == [seite.id for seite in aktuelle_seiten]

    manifest = build_sync_manifest(db, org.id)
    assert [seite["seite_id"] for seite in manifest["objekte"][0]["seiten"]] == [seite.id for seite in aktuelle_seiten]

    old_pdf = tmp_path / "alt.pdf"
    old_pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("app.routers.ui_objekt_dokumente.absolute_pfad", lambda _: old_pdf)
    assert _seite_fuer_user(db, alte_seiten[0].id, user).id == alte_seiten[0].id
    assert seite_pdf(alte_seiten[0].id, request, db, user).status_code == 200
