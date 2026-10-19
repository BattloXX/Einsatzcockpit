"""Generische PDF-Seiten-Pipeline ohne Poppler oder Tesseract."""
import io
from unittest.mock import Mock

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import settings
from app.services.pdf_seiten_pipeline import (
    PdfFehler,
    entferne_seitendateien,
    pruefe_pdf,
    zerlege_pdf,
)


TEXT = "Dies ist der ausreichend lange Textlayer der ersten PDF-Seite."
OCR_TEXT = "Dies ist der ausreichend lange erkannte Text der zweiten Seite."


def _pdf_bytes():
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 750 Td ({TEXT}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.mark.parametrize("fall, detail, status", [
    ("leer", "Leere Datei", 400),
    ("gross", "Datei zu gross (max. 1 MB)", 413),
    ("mime", "Nur PDF-Dateien erlaubt", 415),
    ("unlesbar", "PDF konnte nicht gelesen werden", 415),
    ("seiten", "PDF hat 2 Seiten (max. 1)", 413),
])
def test_pruefe_pdf_fehler(fall, detail, status):
    data = _pdf_bytes()
    max_bytes = 1024 * 1024
    max_seiten = 2
    detect_mime = Mock(return_value="application/pdf")
    if fall == "leer":
        data = b""
    elif fall == "gross":
        data = b"x" * (max_bytes + 1)
    elif fall == "mime":
        data = b"Kein PDF"
        detect_mime.return_value = "text/plain"
    elif fall == "unlesbar":
        data = b"%PDF-1.4\nunvollstaendig"
    elif fall == "seiten":
        max_seiten = 1
    with pytest.raises(PdfFehler) as exc:
        pruefe_pdf(data, max_bytes=max_bytes, max_seiten=max_seiten, detect_mime=detect_mime)
    assert exc.value.detail == detail
    assert str(exc.value) == detail
    assert exc.value.status_code == status
    if fall in ("leer", "gross"):
        detect_mime.assert_not_called()
    else:
        detect_mime.assert_called_once_with(data)


def test_pruefe_pdf_grenzwerte():
    data = _pdf_bytes()
    assert pruefe_pdf(
        data, max_bytes=len(data), max_seiten=2,
        detect_mime=lambda data: "application/pdf",
    ) == 2


@pytest.mark.parametrize("mit_rendering", [True, False])
def test_zerlege_pdf(tmp_path, monkeypatch, mit_rendering):
    monkeypatch.setattr(settings, "OBJEKT_OCR_ENABLED", True)
    monkeypatch.setattr(settings, "OBJEKT_OCR_MIN_CHARS", 20)
    original = tmp_path / "original.pdf"
    original.write_bytes(_pdf_bytes())
    buf = io.BytesIO()
    Image.new("RGBA", (60, 120), color="white").save(buf, "PNG")
    png = buf.getvalue()
    render = Mock(return_value=png if mit_rendering else None)
    ocr = Mock(return_value=f"  {OCR_TEXT}\n")

    ergebnisse = zerlege_pdf(
        original, tmp_path, render_func=render, ocr_func=ocr,
        dpi=144, thumb_groesse=30, log_kontext="Dokument 7",
    )

    assert [e.seiten_nr for e in ergebnisse] == [1, 2]
    assert render.call_args_list == [((original, 1, 144),), ((original, 2, 144),)]
    assert (ergebnisse[0].volltext, ergebnisse[0].text_quelle) == (TEXT, "pdf")
    if mit_rendering:
        assert (ergebnisse[1].volltext, ergebnisse[1].text_quelle) == (OCR_TEXT, "ocr")
        ocr.assert_called_once_with(png)
    else:
        assert (ergebnisse[1].volltext, ergebnisse[1].text_quelle) == (None, "none")
        ocr.assert_not_called()
    for i, ergebnis in enumerate(ergebnisse, start=1):
        assert ergebnis.einzel_pdf_name == f"seite_{i:04d}.pdf"
        einzel = tmp_path / ergebnis.einzel_pdf_name
        assert len(PdfReader(einzel).pages) == 1
        bytes_belegt = einzel.stat().st_size
        if mit_rendering:
            assert ergebnis.bild_name == f"seite_{i:04d}.png"
            assert ergebnis.thumb_name == f"seite_{i:04d}_thumb.jpg"
            assert (tmp_path / ergebnis.bild_name).read_bytes() == png
            with Image.open(tmp_path / ergebnis.thumb_name) as thumb:
                assert thumb.size == (30, 60)
                assert thumb.mode == "RGB"
            bytes_belegt += len(png) + (tmp_path / ergebnis.thumb_name).stat().st_size
        else:
            assert ergebnis.bild_name is None
            assert ergebnis.thumb_name is None
        assert ergebnis.bytes_belegt == bytes_belegt
    erwartet = {"original.pdf", "seite_0001.pdf", "seite_0002.pdf"}
    if mit_rendering:
        erwartet |= {"seite_0001.png", "seite_0002.png", "seite_0001_thumb.jpg", "seite_0002_thumb.jpg"}
    assert {p.name for p in tmp_path.iterdir()} == erwartet


def test_zerlege_pdf_thumb_fehler(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "OBJEKT_OCR_ENABLED", True)
    monkeypatch.setattr(settings, "OBJEKT_OCR_MIN_CHARS", 20)
    original = tmp_path / "original.pdf"
    original.write_bytes(_pdf_bytes())
    ocr = Mock(return_value=OCR_TEXT)
    ergebnisse = zerlege_pdf(
        original, tmp_path, render_func=lambda *_: b"ungueltiges PNG", ocr_func=ocr,
        dpi=144, thumb_groesse=30, log_kontext="Dokument 7",
    )
    for i, ergebnis in enumerate(ergebnisse, start=1):
        assert ergebnis.bild_name == f"seite_{i:04d}.png"
        assert ergebnis.thumb_name is None
        assert ergebnis.bytes_belegt == (
            (tmp_path / ergebnis.einzel_pdf_name).stat().st_size + len(b"ungueltiges PNG")
        )
        assert f"Thumb-Erzeugung fehlgeschlagen (Dokument 7 Seite {i})" in caplog.text
    assert ergebnisse[1].volltext == OCR_TEXT
    ocr.assert_called_once_with(b"ungueltiges PNG")


def test_objekt_wrapper_ocr_default_zur_laufzeit(monkeypatch):
    from app.services import objekt_dokument_service

    monkeypatch.setattr(settings, "OBJEKT_OCR_ENABLED", True)
    monkeypatch.setattr(settings, "OBJEKT_OCR_MIN_CHARS", 20)
    ocr = Mock(return_value=OCR_TEXT)
    monkeypatch.setattr(objekt_dokument_service, "_ocr_tesseract", ocr)
    assert objekt_dokument_service.extrahiere_seitentext(None, b"png") == (OCR_TEXT, "ocr")
    ocr.assert_called_once_with(b"png")
    andere_ocr = Mock(return_value="Anderer ausreichend langer erkannter Text")
    assert objekt_dokument_service.extrahiere_seitentext(None, b"png", andere_ocr) == (
        andere_ocr.return_value, "ocr",
    )
    andere_ocr.assert_called_once_with(b"png")
    assert ocr.call_count == 1


def test_entferne_seitendateien(tmp_path):
    for name in ("original.pdf", "andere.txt", "seite_0001.pdf", "seite_0001.png", "seite_0001_thumb.jpg"):
        (tmp_path / name).write_bytes(b"datei")
    entferne_seitendateien(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"original.pdf", "andere.txt"}
    entferne_seitendateien(tmp_path)
