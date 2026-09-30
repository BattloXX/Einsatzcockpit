"""Service-Regressions fuer fertig analysierte, extern uebergebene PDFs."""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept, OrgStorageUsage
from app.models.objekt import (
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.models.user import User
from app.services.objekt_dokument_service import (
    ObjektDokumentFehler,
    absolute_pfad,
    gebe_dokument_frei,
    hole_wartende_dokumente,
    store_dokument_bytes,
    verarbeite_dokument_mit_analyse,
    verwirf_wartendes_dokument,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


def _pdf(seiten: int = 2) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(seiten):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _analyse(anzahl: int = 2) -> list[dict]:
    return [
        {
            "volltext": f"MCP Suchtext Seite {nummer}",
            "dokumentart": "lageplan",
            "titel": f"Plan {nummer}",
            "melderlinien": "ML 7",
            "stand": "2026-09-30",
            "bei_einsatz_drucken": nummer == 1,
        }
        for nummer in range(1, anzahl + 1)
    ]


@pytest.fixture()
def mcp_db(tmp_path, monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "medien"))
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="mcp-dok", name="MCP Dokumente", color="#123456", bos="Feuerwehr")
    fremde_org = FireDept(slug="mcp-fremd", name="Andere Org", color="#654321", bos="Feuerwehr")
    db.add_all([org, fremde_org])
    db.flush()
    user = User(username="mcp-dok", display_name="MCP Dokument", org_id=org.id)
    entwurf = Objekt(org_id=org.id, nummer=1, name="Entwurf", status=OBJEKT_STATUS_ENTWURF)
    freigegeben = Objekt(org_id=org.id, nummer=2, name="Produktiv", status=OBJEKT_STATUS_FREIGEGEBEN)
    fremdes_objekt = Objekt(org_id=fremde_org.id, nummer=1, name="Fremd", status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add_all([user, entwurf, freigegeben, fremdes_objekt])
    db.commit()
    yield db, org, fremde_org, user, entwurf, freigegeben, fremdes_objekt
    db.close()
    Base.metadata.drop_all(bind=engine)


def test_fertig_analysiertes_pdf_ist_sofort_durchsuchbar_ohne_ocr_oder_ki(mcp_db, monkeypatch):
    db, _org, _fremde_org, user, entwurf, *_ = mcp_db
    ocr = Mock(side_effect=AssertionError("OCR darf nicht laufen"))
    ki = Mock(side_effect=AssertionError("KI darf nicht laufen"))
    vision = Mock(side_effect=AssertionError("AI-Service darf nicht laufen"))
    monkeypatch.setattr("app.services.objekt_dokument_service._ocr_tesseract", ocr)
    monkeypatch.setattr("app.services.objekt_ki_service.analysiere_unklassifizierte_seiten", ki)
    monkeypatch.setattr("app.services.ai_service.complete_vision", vision)
    dokument = store_dokument_bytes(_pdf(), "plan.pdf", entwurf, user, db)
    ergebnis = verarbeite_dokument_mit_analyse(dokument.id, _analyse(), db=db, user_id=user.id,
                                                render_func=lambda *_: None)
    db.commit()

    assert ergebnis.status == "fertig"
    seiten = db.query(ObjektDokumentSeite).filter_by(dokument_id=dokument.id).all()
    assert len(seiten) == 2
    assert all(s.text_quelle == "mcp" and s.klassifiziert_von_id == user.id for s in seiten)
    assert seiten[0].volltext == "MCP Suchtext Seite 1"
    assert seiten[0].dokumentart == "lageplan" and seiten[0].titel == "Plan 1"
    assert db.query(ObjektDokumentSeite).filter(
        ObjektDokumentSeite.volltext.like("%Suchtext%")
    ).count() == 2
    ocr.assert_not_called()
    ki.assert_not_called()
    vision.assert_not_called()


@pytest.mark.parametrize(
    ("data", "seiten"),
    [(b"", _analyse()), (b"kein pdf", _analyse()), (_pdf(), _analyse(3))],
    ids=("leer", "kein-pdf", "seitenzahl"),
)
def test_ungueltige_uebergaben_hinterlassen_keine_dokumente(mcp_db, data, seiten):
    db, _org, _fremde_org, user, entwurf, *_ = mcp_db
    with pytest.raises(ObjektDokumentFehler):
        dokument = store_dokument_bytes(data, "plan.pdf", entwurf, user, db)
        verarbeite_dokument_mit_analyse(dokument.id, seiten, db=db, user_id=user.id)
    assert db.query(ObjektDokument).count() == 0


def test_ungueltige_dokumentart_und_renderfehler_raeumen_auf(mcp_db):
    db, _org, _fremde_org, user, entwurf, *_ = mcp_db
    for analyse, render in [([{**_analyse()[0], "dokumentart": "falsch"}, _analyse()[1]], None),
                            (_analyse(), lambda *_: (_ for _ in ()).throw(RuntimeError("render kaputt")))]:
        dokument = store_dokument_bytes(_pdf(), "plan.pdf", entwurf, user, db)
        pfad = absolute_pfad(dokument.pfad).parent
        with pytest.raises(ObjektDokumentFehler):
            verarbeite_dokument_mit_analyse(dokument.id, analyse, db=db, user_id=user.id, render_func=render)
        assert db.get(ObjektDokument, dokument.id) is None
        assert not pfad.exists()


def test_upload_limit_und_quota_hinterlassen_weder_datei_noch_zeile(mcp_db, monkeypatch):
    db, org, _fremde_org, user, entwurf, *_ = mcp_db
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "OBJEKT_PDF_MAX_BYTES", 1)
    with pytest.raises(ObjektDokumentFehler) as exc:
        store_dokument_bytes(_pdf(), "zu-gross.pdf", entwurf, user, db)
    assert exc.value.status_code == 413 and db.query(ObjektDokument).count() == 0
    monkeypatch.setattr(app_settings, "OBJEKT_PDF_MAX_BYTES", 10_000_000)
    org.storage_quota_bytes = 1
    with pytest.raises(ObjektDokumentFehler) as exc:
        store_dokument_bytes(_pdf(), "quota.pdf", entwurf, user, db)
    assert exc.value.status_code == 413 and db.query(ObjektDokument).count() == 0
    assert not list(absolute_pfad(".").glob("*/**/original.pdf"))


def test_wartende_version_wird_erst_bei_freigabe_aktuell_und_kann_verworfen_werden(mcp_db):
    db, org, fremde_org, user, entwurf, objekt, fremdes_objekt = mcp_db
    alt = store_dokument_bytes(_pdf(1), "alt.pdf", entwurf, user, db)
    verarbeite_dokument_mit_analyse(alt.id, _analyse(1), db=db, user_id=user.id, render_func=lambda *_: None)
    db.commit()
    # Das Produktivobjekt bekommt eine wartende Erstversion und eine Ersatzversion.
    produktiv = store_dokument_bytes(_pdf(1), "prod.pdf", objekt, user, db)
    verarbeite_dokument_mit_analyse(produktiv.id, _analyse(1), db=db, user_id=user.id, render_func=lambda *_: None)
    assert produktiv.ist_aktuelle_version is False
    assert [d.id for d in hole_wartende_dokumente(db, objekt)] == [produktiv.id]
    gebe_dokument_frei(db, produktiv, user.id)
    db.commit()
    neu = store_dokument_bytes(_pdf(1), "neu.pdf", objekt, user, db, ersetzt_dokument_id=produktiv.id)
    verarbeite_dokument_mit_analyse(neu.id, _analyse(1), db=db, user_id=user.id, render_func=lambda *_: None)
    assert neu.versionsnummer == 2 and neu.ist_aktuelle_version is False
    assert produktiv.ist_aktuelle_version is True
    gebe_dokument_frei(db, neu, user.id)
    assert produktiv.freigabe_status == "archiviert" and neu.ist_aktuelle_version is True
    db.commit()
    wartend = store_dokument_bytes(_pdf(1), "verwerfen.pdf", objekt, user, db)
    verarbeite_dokument_mit_analyse(wartend.id, _analyse(1), db=db, user_id=user.id, render_func=lambda *_: None)
    verwirf_wartendes_dokument(db, wartend, user.id)
    db.commit()
    assert wartend.freigabe_status == "verworfen" and not absolute_pfad(wartend.pfad).parent.exists()
    usage = db.get(OrgStorageUsage, org.id)
    assert usage is not None and usage.used_bytes > 0
    fremd = store_dokument_bytes(_pdf(1), "fremd.pdf", fremdes_objekt, user, db)
    with pytest.raises(ObjektDokumentFehler):
        store_dokument_bytes(_pdf(1), "cross.pdf", objekt, user, db, ersetzt_dokument_id=fremd.id)


class _FakeUpload:
    def __init__(self, filename: str):
        self.filename = filename


@pytest.mark.asyncio
async def test_router_upload_und_bulk_behalten_fehlermeldungen(mcp_db, monkeypatch):
    """Der HTTP-Adapter bleibt nach dem Service-Refactor bei seinen alten Texten."""
    from app.routers import ui_objekt_dokumente as router

    db, _org, _fremde_org, user, entwurf, *_ = mcp_db
    monkeypatch.setattr(router, "_objekt_or_404", lambda *_: entwurf)
    monkeypatch.setattr(router, "_galerie_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(router.templates, "TemplateResponse", lambda _request, _template, ctx: ctx)
    monkeypatch.setattr(router, "write_objekt_change", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router, "write_audit", lambda *_args, **_kwargs: None)

    async def _store(datei, *_args):
        if datei.filename == "bad.pdf":
            raise HTTPException(status_code=415, detail="Nur PDF-Dateien erlaubt")
        return SimpleNamespace(id=77)

    monkeypatch.setattr(router, "store_dokument_upload", _store)
    result = await router.dokumente_upload(
        entwurf.id, SimpleNamespace(), BackgroundTasks(), db, user, None,
        [_FakeUpload("bad.pdf"), _FakeUpload("gut.pdf")],
    )
    assert result["upload_fehler"] == ["bad.pdf: Nur PDF-Dateien erlaubt"]
    with pytest.raises(HTTPException, match="Ungueltige Seiten-Auswahl"):
        router.seiten_bulk_klassifizieren(entwurf.id, SimpleNamespace(), db, user, None, "x")
    with pytest.raises(HTTPException, match="Keine Seiten ausgewaehlt"):
        router.seiten_bulk_klassifizieren(entwurf.id, SimpleNamespace(), db, user, None, "")
