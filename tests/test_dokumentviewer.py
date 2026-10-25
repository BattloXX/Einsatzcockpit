"""Seitenauswahl, unveränderte Originale und weitergeltende Freigabe-/Tenantgrenzen."""

import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanDokument, KatplanElement, KatplanPlan
from app.models.user import User
from app.services import katplan_anhang_service as anhaenge
from app.services.dokumentviewer_service import pdf_auszug, seiten_auswahl
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import lege_element_an
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401
from tests.test_katplan_k6a import png


def dokument_pdf(anzahl=12, kennung=""):
    writer = PdfWriter()
    if kennung:
        writer.add_metadata({"/Subject": kennung})
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for nr in range(anzahl):
        hoehe = 180 if nr % 2 else 500
        page = writer.add_blank_page(width=300 + nr, height=hoehe)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data((f"BT /F1 18 Tf 24 {hoehe - 42} Td (Unterlage - Seite {nr + 1}) Tj "
                          "0 -32 Td /F1 10 Tf (Originaldokument bleibt unveraendert.) Tj ET "
                          f"q 0.8 0.1 0.1 rg 24 {hoehe - 105} 250 8 re f Q").encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def dokumente(basis_umgebung, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    with TestingSession() as db:
        info = basis_umgebung
        set_tenant_context(db, info["org_id"])
        user = db.query(User).filter_by(username=info["users"]["katplan_freigeber"]).one()
        plan = db.get(KatplanPlan, info["plan_id"])
        element = lege_element_an(db, plan, user, typ="massnahme", titel="Vier Unterlagen")
        dateien = []
        for i in range(4):
            bild = i == 3
            anhang = anhaenge.anhang_hochladen(db, plan, user, png() if bild else dokument_pdf(kennung=str(i)),
                f"datei-{i}.png" if bild else f"datei-{i}.pdf", "image/png" if bild else "application/pdf",
                element=element, beschriftung=f"Unterlage {i + 1}")
            dateien.append({"uuid": anhang.uuid, "path": tmp_path / anhang.pfad})
        geheim = anhaenge.anhang_hochladen(db, plan, user, dokument_pdf(), "geheim.pdf", "application/pdf",
                                         element=element, beschriftung="Geheime Unterlage", vertraulich=True)
        version = freigeben(db, plan, user)
        db.commit()
        return {**info, "dateien": dateien, "geheim": geheim.uuid, "version_id": version.id,
                "element_id": element.id}


@pytest.mark.parametrize("text,erwartet", [
    ("Seite 5", [5]), ("Seiten 5–8", [5, 6, 7, 8]), ("2, 5 und 9", [2, 5, 9]),
    ("3–5 und 12–15", [3, 4, 5, 12, 13, 14, 15]), ("5,2,2-4", [2, 3, 4, 5]),
    ("alle", list(range(1, 16))),
])
def test_seitenauswahl(text, erwartet):
    assert seiten_auswahl(text, 15) == erwartet


@pytest.mark.parametrize("text", ["", "0", "-1", "16", "1,,2", "3-1", "1e2", "1.5", "1-99999999999999"])
def test_ungueltige_auswahl(text):
    with pytest.raises(ValueError):
        seiten_auswahl(text, 15)


def test_pdf_auszug_ist_vector_pdf_und_erhaelt_seitenformat():
    original = dokument_pdf()
    auszug = PdfReader(io.BytesIO(pdf_auszug(original, "2,5,9")))
    assert len(auszug.pages) == 3
    assert [float(p.mediabox.width) for p in auszug.pages] == [301, 304, 308]
    assert [float(p.mediabox.height) for p in auszug.pages] == [180, 500, 500]
    assert [p.extract_text().splitlines()[0] for p in auszug.pages] == [
        "Unterlage - Seite 2", "Unterlage - Seite 5", "Unterlage - Seite 9"]
    assert len(PdfReader(io.BytesIO(original)).pages) == 12


def test_rechte_auswahl_und_unveraenderte_originale(client, dokumente):
    login(client, dokumente, "readonly")
    datei = dokumente["dateien"][0]
    url = f'/plaene/anhang/{datei["uuid"]}?version_id={dokumente["version_id"]}'
    original = datei["path"].read_bytes()
    response = client.get(url + "&auswahl=2,5,9")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    reader = PdfReader(io.BytesIO(response.content))
    assert len(reader.pages) == 3 and float(reader.pages[0].mediabox.width) == 301
    assert client.get(url).content == original == datei["path"].read_bytes()
    assert client.get(url + "&auswahl=99").status_code == 422
    assert client.get(f'/plaene/anhang/{dokumente["geheim"]}?auswahl=1').status_code == 404
    assert "Geheime Unterlage" not in client.get(f'/plaene/{dokumente["plan_id"]}').text
    login(client, dokumente, "fremd")
    assert client.get(url + "&auswahl=1").status_code == 404


def test_defekte_und_fehlende_dateien(client, dokumente):
    login(client, dokumente, "readonly")
    datei = dokumente["dateien"][0]
    url = f'/plaene/anhang/{datei["uuid"]}'
    datei["path"].write_bytes(b"kaputtes pdf")
    assert client.get(url + "?auswahl=1").status_code == 422
    datei["path"].unlink()
    assert client.get(url + "?auswahl=1").status_code == 404


def test_quellenauszug_auswahl_und_historische_freigabe(client, dokumente):
    from app.models.katplan import KatplanAnhang

    datei = dokumente["dateien"][0]
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        anhang = db.query(KatplanAnhang).filter_by(uuid=datei["uuid"]).one()
        plan = db.get(KatplanPlan, dokumente["plan_id"])
        user = db.query(User).filter_by(username=dokumente["users"]["katplan_freigeber"]).one()
        quelle = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Originalquelle",
                                dateiname_original="quelle.pdf", pfad=anhang.pfad, groesse_bytes=100,
                                seitenzahl=12, status="fertig")
        db.add(quelle)
        db.flush()
        element = db.get(KatplanElement, dokumente["element_id"])
        element.quelle_dokument_id, element.quelle_seiten = quelle.id, "3-5"
        plan.status = "in_ueberarbeitung"
        version = freigeben(db, plan, user)
        db.commit()
        url = f"/plaene/quelle/{quelle.uuid}/original?version_id={version.id}"
    login(client, dokumente, "readonly")
    original = datei["path"].read_bytes()
    response = client.get(url + "&seiten=3-5&auswahl=1,3")
    assert response.status_code == 200
    reader = PdfReader(io.BytesIO(response.content))
    assert [float(p.mediabox.width) for p in reader.pages] == [302, 304]
    assert client.get(url).content == original == datei["path"].read_bytes()
    assert client.get(url + "&seiten=3-5&auswahl=4").status_code == 422
    assert client.get(url.replace(f"version_id={version.id}",
                                  f'version_id={dokumente["version_id"]}') + "&auswahl=1").status_code == 404


def test_objektdokument_auszug_verwendet_freigegebenen_seitenbereich(client, dokumente, tmp_path, monkeypatch):
    from app.models.master import OrgSettings, SystemSettings
    from app.models.objekt import Objekt, ObjektDokument

    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekte"))
    (tmp_path / "objekte").mkdir()
    original = dokument_pdf()
    (tmp_path / "objekte/quelle.pdf").write_bytes(original)
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="objekt_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().objekt_module_enabled = True
        objekt = Objekt(org_id=dokumente["org_id"], name="Testobjekt")
        db.add(objekt)
        db.flush()
        dok = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id,
                            dateiname_original="Objekt.pdf", pfad="quelle.pdf", seitenzahl=12)
        db.add(dok)
        db.flush()
        plan = db.get(KatplanPlan, dokumente["plan_id"])
        user = db.query(User).filter_by(username=dokumente["users"]["katplan_freigeber"]).one()
        anhang = anhaenge.objektdokument_verlinken(db, plan, user, dok.id, "3-5")
        version = freigeben(db, plan, user)
        db.flush()
        # Ein späterer Arbeitsstand darf den freigegebenen Auszug nicht verändern.
        anhang.seiten = "8-10"
        db.commit()
        url = f"/plaene/anhang/{anhang.uuid}?version_id={version.id}&auszug=1"
    try:
        login(client, dokumente, "readonly")
        response = client.get(url + "&auswahl=1,3")
        assert response.status_code == 200
        assert [float(p.mediabox.width) for p in PdfReader(io.BytesIO(response.content)).pages] == [302, 304]
        assert (tmp_path / "objekte/quelle.pdf").read_bytes() == original
        login(client, dokumente, "katplan_verwalter")
        assert client.get(url + "&auswahl=1").status_code == 404  # Bisherige Objekt-Leserolle fehlt.
    finally:
        with TestingSession() as db:
            set_tenant_context(db, None)
            flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one()
            if vorher is None:
                db.delete(flag)
            else:
                flag.value = vorher
            db.commit()
