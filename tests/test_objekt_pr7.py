"""Objektverwaltung PR 7: Druck (Objektblatt, Anhang, Sammelmappe, QR)."""
import io

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
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    Objekt,
    ObjektBMA,
    ObjektGefahr,
    ObjektKontakt,
)


def _seiten_zahl(pdf: bytes) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture()
def pr7_env(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekt_media"))
    # Keine Netzwerk-/Tile-Zugriffe in Tests: statische Karte deaktivieren
    import app.services.objekt_pdf_service as ops
    monkeypatch.setattr(ops, "render_objekt_map_png", lambda objekt, **kw: None)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="pr7-org", name="Druck Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    objekt = Objekt(org_id=org.id, nummer=1, name="Rattpack Werk 2",
                    vulgoname="Alte Fabrik", status=OBJEKT_STATUS_FREIGEGEBEN,
                    strasse="Dammstraße", hausnummer="64", plz="6922", ort="Wolfurt",
                    lat=47.4652, lng=9.7503)
    db.add(objekt)
    db.flush()
    db.add(ObjektBMA(org_id=org.id, objekt_id=objekt.id, bma_nummer="1044",
                     bmz_standort="EG Büro", schluesselsafe_vorhanden=True,
                     schluesselsafe_standort="beim Haupteingang"))
    gefahr = GefahrenKatalog(org_id=org.id, name="EX-Bereich", piktogramm_typ="ex")
    db.add(gefahr)
    db.flush()
    db.add(ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=gefahr.id,
                        un_nummer="1173", detail="Ethylacetat"))
    db.add(ObjektKontakt(org_id=org.id, objekt_id=objekt.id, art="brandschutzbeauftragter",
                         name="Fischnaller Stephan",
                         telefone_json='["+43 5574 6756-310"]'))
    db.commit()

    import app.db as app_db
    monkeypatch.setattr(app_db, "SessionLocal", Session)

    yield db, org, objekt

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_qr_datauri():
    from app.services.qr_service import generate_qr_datauri
    uri = generate_qr_datauri("https://example.org/objekte/1/einsatz", druck=True)
    assert uri is not None and uri.startswith("data:image/png;base64,")


def test_objektblatt_pdf_erzeugung(pr7_env):
    from app.services.objekt_pdf_service import render_objektblatt_pdf
    db, org, objekt = pr7_env
    pdf = render_objektblatt_pdf(objekt, org, "https://example.org/")
    assert pdf[:5] == b"%PDF-"
    assert _seiten_zahl(pdf) >= 1


def test_objektblatt_hinweise_nur_mit_checkbox(pr7_env):
    """DSGVO: Wohnanlagen-Hinweise nur andrucken, wenn explizit angefordert."""
    from app.models.objekt import ObjektWohnanlage
    from app.services.objekt_pdf_service import render_objektblatt_pdf
    db, org, objekt = pr7_env
    db.add(ObjektWohnanlage(org_id=org.id, objekt_id=objekt.id,
                            hinweise="GEHEIMTEXT-MARKER"))
    db.commit()
    db.refresh(objekt)

    # Der Text landet nur im PDF-Quelltext, wenn mit_hinweisen=True — indirekt
    # prueft man das ueber das gerenderte Template (Render-Pfad identisch).
    from app.core.templating import templates as _t
    from app.models.objekt import GEFAHR_PIKTOGRAMME, KONTAKT_ARTEN
    tpl = _t.env.get_template("pdf/objektblatt.html")
    from datetime import UTC, datetime
    basis = dict(objekt=objekt, org=org, now=datetime.now(UTC), karte_datauri=None,
                 qr_datauri=None, gefahr_piktogramme=GEFAHR_PIKTOGRAMME,
                 kontakt_arten=KONTAKT_ARTEN, symbol_legende=[],
                 erstellt_str="", geaendert_str="", gedruckt_str="")
    ohne = tpl.render(mit_hinweisen=False, **basis)
    mit = tpl.render(mit_hinweisen=True, **basis)
    assert "GEHEIMTEXT-MARKER" not in ohne
    assert "GEHEIMTEXT-MARKER" in mit

    pdf = render_objektblatt_pdf(objekt, org, mit_hinweisen=False)
    assert pdf[:5] == b"%PDF-"


def test_objektblatt_mit_anhang(pr7_env):
    """Anhang haengt alle 'bei Einsatz drucken'-Seiten an das Objektblatt."""
    import asyncio

    from app.models.objekt import ObjektDokumentSeite
    from app.services.objekt_dokument_service import store_dokument_upload, verarbeite_dokument
    from app.services.objekt_pdf_service import objektblatt_mit_anhang, render_objektblatt_pdf
    from tests.test_objekt_pr3 import _FakeUpload, _test_pdf

    db, org, objekt = pr7_env
    dokument = asyncio.run(store_dokument_upload(_FakeUpload(_test_pdf(3)), objekt, None, db))
    db.commit()
    verarbeite_dokument(dokument.id, render_func=lambda p, n, dpi: None)
    db.expire_all()

    # 2 von 3 Seiten als Einsatzdruck markieren
    seiten = db.query(ObjektDokumentSeite).order_by(ObjektDokumentSeite.seiten_nr).all()
    seiten[0].bei_einsatz_drucken = True
    seiten[2].bei_einsatz_drucken = True
    db.commit()

    blatt = render_objektblatt_pdf(objekt, org)
    blatt_seiten = _seiten_zahl(blatt)

    mit_anhang = objektblatt_mit_anhang(objekt, org, db, mit_anhang=True)
    assert _seiten_zahl(mit_anhang) == blatt_seiten + 2

    ohne_anhang = objektblatt_mit_anhang(objekt, org, db, mit_anhang=False)
    assert _seiten_zahl(ohne_anhang) == blatt_seiten


def test_sammelmappe(pr7_env):
    from app.services.objekt_pdf_service import render_objektblatt_pdf, sammelmappe
    db, org, objekt = pr7_env
    objekt2 = Objekt(org_id=org.id, nummer=2, name="Zweites Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt2)
    db.commit()

    einzeln = _seiten_zahl(render_objektblatt_pdf(objekt, org)) + \
        _seiten_zahl(render_objektblatt_pdf(objekt2, org))
    mappe = sammelmappe([objekt, objekt2], org, db)
    assert _seiten_zahl(mappe) == einzeln


def test_pr7_routen():
    from app.routers.ui_objekt import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/{objekt_id}/objektblatt.pdf" in pfade
    assert "/objekte/druck" in pfade
