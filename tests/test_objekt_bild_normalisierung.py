"""Objektbilder werden vor der bestehenden Dokument-Pipeline zu A4-PDFs."""
import asyncio
import io
import threading
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from pypdf import PdfReader

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektDokument
from app.services import objekt_bild_normalisierung as bild_service
from app.services import objekt_dokument_service as service
from tests.test_objekt_dokumente_wartend_ui import _login, _setup
from tests.test_objekt_pr3 import _test_pdf
from tests.test_objekt_pr3 import pr3_env as pr3_env


def _bild(format="JPEG", groesse=(100, 200), modus="RGB", farbe="red", **optionen):
    with Image.new(modus, groesse, farbe) as bild, io.BytesIO() as buf:
        bild.save(buf, format, **optionen)
        return buf.getvalue()


def _seiten(data):
    return PdfReader(io.BytesIO(data)).pages


def _a4(seite, hochkant=True):
    erwartet = (595, 842) if hochkant else (842, 595)
    assert float(seite.mediabox.width) == pytest.approx(erwartet[0], abs=1)
    assert float(seite.mediabox.height) == pytest.approx(erwartet[1], abs=1)


def test_jpeg_hochkant():
    pdf, name = bild_service.bild_zu_pdf(_bild(groesse=(3000, 4000)), "IMG_1234.JPG")
    assert name == "IMG_1234.pdf"
    assert len(_seiten(pdf)) == 1
    _a4(_seiten(pdf)[0])
    assert _seiten(pdf)[0]["/Resources"]["/XObject"].get_object()["/image"].get_object()["/Filter"] == "/DCTDecode"


def test_exif_orientation_bestimmt_seitenformat():
    exif = Image.Exif()
    exif[274] = 6
    pdf, _ = bild_service.bild_zu_pdf(_bild(groesse=(400, 300), exif=exif), "gedreht.jpg")
    _a4(_seiten(pdf)[0])


def test_png_transparenz_weiss_und_plan_kompakt():
    with Image.new("RGBA", (2000, 1000), (255, 0, 0, 255)) as bild, io.BytesIO() as buf:
        bild.paste((0, 0, 0, 0), (0, 0, 1000, 1000))
        bild.save(buf, "PNG")
        data = buf.getvalue()
    leinwand, plan = bild_service._bild_auf_leinwand(data)
    assert plan is True
    with leinwand:
        assert leinwand.size == (3508, 2480)
        with leinwand.convert("RGB") as rgb:
            assert rgb.getpixel((1000, 1240)) == (255, 255, 255)
            assert rgb.getpixel((2500, 1240)) == (255, 0, 0)
            assert rgb.getpixel((0, 0)) == (255, 255, 255)
    pdf, _ = bild_service.bild_zu_pdf(data, "transparent.png")
    _a4(_seiten(pdf)[0], False)
    # Kein unkomprimiertes ASCIIHex (wuerde eine A4-Seite auf ~17 MB aufblaehen)
    assert b"/ASCIIHexDecode" not in pdf
    assert b"/DCTDecode" in pdf
    assert len(pdf) < 2 * 1024 * 1024


def test_gps_exif_wird_entfernt():
    exif = Image.Exif()
    exif[34853] = {0: b"\x02\x03\x00\x00", 1: "N", 2: (48.0, 12.0, 0.0)}
    exif[270] = "GPS Test"
    data = _bild(exif=exif)
    assert b"Exif" in data and b"GPS" in data
    pdf, _ = bild_service.bild_zu_pdf(data, "gps.jpg")
    assert b"Exif" not in pdf and b"GPS" not in pdf
    assert not _seiten(pdf)[0].images[0].image.getexif()


def test_mehrere_bilder_in_reihenfolge():
    pdf, name = bild_service.bilder_zu_pdf([
        (_bild(groesse=(100, 200)), "erstes.jpg"),
        (_bild("PNG", (200, 100)), "zweites.png"),
        (_bild(groesse=(100, 300)), "drittes.jpg"),
    ])
    assert name == "erstes.pdf"
    seiten = _seiten(pdf)
    assert len(seiten) == 3
    for seite, hochkant in zip(seiten, [True, False, True], strict=True):
        _a4(seite, hochkant)


def test_pixelgrenze(monkeypatch):
    monkeypatch.setattr(bild_service, "BILD_MAX_PIXEL", 100)
    with pytest.raises(bild_service.BildFehler) as exc:
        bild_service.bild_zu_pdf(_bild(), "gross.jpg")
    assert exc.value.status_code == 413
    assert exc.value.detail == "Bild zu groß"


@pytest.mark.parametrize("grenze", [100, 15_000])
def test_pillow_decompression_bomb(monkeypatch, grenze):
    data = _bild()
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", grenze)
    with pytest.raises(bild_service.BildFehler) as exc:
        bild_service.bild_zu_pdf(data, "gross.jpg")
    assert exc.value.status_code == 413


def test_kaputtes_jpeg():
    data = b"\xff\xd8\xff\xe0" + b"kaputt" * 10
    assert bild_service.ist_bild(data)
    with pytest.raises(bild_service.BildFehler) as exc:
        bild_service.bild_zu_pdf(data, "kaputt.jpg")
    assert exc.value.status_code == 415


def test_html_mit_bildnamen(pr3_env):
    db, _, _, objekt = pr3_env
    with pytest.raises(service.ObjektDokumentFehler) as exc:
        service.store_dokument_bytes(b"<html>kein Bild</html>", "x.jpg", objekt, None, db)
    assert exc.value.status_code == 415
    assert exc.value.detail == "Nur PDF- oder Bilddateien (JPG, PNG, WebP, HEIC) erlaubt"


def test_jpeg_speichern(pr3_env):
    db, _, _, objekt = pr3_env
    dokument = service.store_dokument_bytes(_bild(), "foto.jpg", objekt, None, db)
    assert isinstance(dokument, ObjektDokument)
    assert dokument.mime == "application/pdf"
    assert dokument.dateiname_original == "foto.pdf"
    assert dokument.seitenzahl == 1
    original = service.absolute_pfad(dokument.pfad)
    assert original.is_relative_to(settings.OBJEKT_MEDIA_DIR)
    assert original.read_bytes().startswith(b"%PDF")
    assert list(original.parent.iterdir()) == [original]


def test_pdf_bleibt_bytegleich(pr3_env):
    db, _, _, objekt = pr3_env
    data = _test_pdf(2)
    dokument = service.store_dokument_bytes(data, "plan.pdf", objekt, None, db)
    assert service.absolute_pfad(dokument.pfad).read_bytes() == data
    assert dokument.dateiname_original == "plan.pdf"
    assert dokument.seitenzahl == 2


def test_heic():
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    pdf, name = bild_service.bild_zu_pdf(_bild("HEIF"), "IMG_1234.HEIC")
    assert name == "IMG_1234.pdf"
    assert len(_seiten(pdf)) == 1
    _a4(_seiten(pdf)[0])


def test_heic_ohne_plugin(monkeypatch):
    monkeypatch.setattr(bild_service, "_HEIC_OK", False)
    monkeypatch.setattr(service, "_detect_mime", lambda _: "image/heic")
    with pytest.raises(bild_service.BildFehler) as exc:
        bild_service.bild_zu_pdf(b"heic", "foto.heic")
    assert exc.value.status_code == 415


@pytest.mark.parametrize("name,erwartet", [("", "foto.pdf"), ("IMG_1234.HEIC", "IMG_1234.pdf")])
def test_dateiname(name, erwartet):
    assert bild_service.bild_zu_pdf(_bild(), name)[1] == erwartet


@pytest.mark.parametrize("format", ["WEBP", "PNG"])
def test_weitere_bildformate(format):
    pdf, _ = bild_service.bild_zu_pdf(_bild(format), "bild")
    _a4(_seiten(pdf)[0])


@pytest.mark.parametrize("async_upload", [False, True])
def test_bild_byte_limit_vor_umwandlung(pr3_env, monkeypatch, async_upload):
    db, _, _, objekt = pr3_env
    data = _bild()
    monkeypatch.setattr(settings, "OBJEKT_BILD_MAX_BYTES", len(data) - 1)
    umwandlung = Mock(side_effect=AssertionError("Umwandlung trotz Groessenlimit"))
    monkeypatch.setattr(service, "bild_zu_pdf", umwandlung)
    with pytest.raises(HTTPException if async_upload else service.ObjektDokumentFehler) as exc:
        if async_upload:
            asyncio.run(service.store_dokument_upload(UploadFile(io.BytesIO(data), filename="foto.jpg"),
                                                      objekt, None, db))
        else:
            service.store_dokument_bytes(data, "foto.jpg", objekt, None, db)
    assert exc.value.status_code == 413
    umwandlung.assert_not_called()


def test_async_umwandlung_im_threadpool_einmal(pr3_env, monkeypatch):
    db, _, _, objekt = pr3_env
    hauptthread = threading.get_ident()
    aufrufe = []
    original = service.bild_zu_pdf

    def umwandlung(data, name):
        assert threading.get_ident() != hauptthread
        aufrufe.append(name)
        return original(data, name)

    monkeypatch.setattr(service, "bild_zu_pdf", umwandlung)
    dokument = asyncio.run(service.store_dokument_upload(UploadFile(io.BytesIO(_bild()), filename="foto.jpg"),
                                                         objekt, None, db))
    assert dokument.dateiname_original == "foto.pdf"
    assert aufrufe == ["foto.jpg"]


def test_png_upload_route(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    org_id, _, objekt_id = _setup("bild_upload", nummer=9740)
    worker = Mock()
    monkeypatch.setattr("app.routers.ui_objekt_dokumente.verarbeite_dokument", worker)
    assert _login(client, "bild_upload").status_code == 302
    response = client.post(f"/objekte/{objekt_id}/dokumente/upload",
                           data={"_csrf": client.cookies.get("ec_csrf")},
                           files={"dateien": ("plan.png", _bild("PNG"), "image/png")})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        dokument = db.query(ObjektDokument).filter_by(objekt_id=objekt_id).one()
        assert dokument.mime == "application/pdf"
        assert dokument.dateiname_original == "plan.pdf"
        assert dokument.seitenzahl == 1
        assert service.absolute_pfad(dokument.pfad).read_bytes().startswith(b"%PDF")
        worker.assert_called_once_with(dokument.id)
