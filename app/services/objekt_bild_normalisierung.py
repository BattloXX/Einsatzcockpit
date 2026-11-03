"""Normalisiert Objektbilder ohne Original-Metadaten zu A4-PDFs."""
from __future__ import annotations

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIC_OK = True
except Exception:
    _HEIC_OK = False

BILD_MIMES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
BILD_MAX_PIXEL = 80_000_000
A4_PIXEL = (2480, 3508)
RAND_PIXEL = 118


class BildFehler(Exception):
    """Fachlicher Bildfehler ohne HTTP-Abhaengigkeit."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def ist_bild(data: bytes) -> bool:
    from app.services.objekt_dokument_service import _detect_mime

    return _detect_mime(data) in BILD_MIMES


def _bild_auf_leinwand(data: bytes) -> tuple[Image.Image, bool]:
    """Erzeugt eine weisse A4-Seite; der Aufrufer schliesst die Leinwand.

    Zweiter Wert: True bei Plan/Screenshot (wenige Farben) fuer hoehere JPEG-Qualitaet.
    """
    from app.services.objekt_dokument_service import _detect_mime

    mime = _detect_mime(data)
    if mime not in BILD_MIMES or (mime in {"image/heic", "image/heif"} and not _HEIC_OK):
        raise BildFehler("Bildformat nicht unterstuetzt", 415)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as quelle:
                if quelle.width * quelle.height > BILD_MAX_PIXEL:
                    raise BildFehler("Bild zu groß", 413)
                verlustfrei = quelle.mode == "P" or (quelle.format == "PNG" and quelle.getcolors(256) is not None)
                with ImageOps.exif_transpose(quelle) as bild:
                    groesse = A4_PIXEL if bild.height > bild.width else A4_PIXEL[::-1]
                    modus = "L" if bild.mode == "L" else "RGB"
                    with Image.new(modus, groesse, "white") as leinwand:
                        bild.thumbnail((groesse[0] - 2 * RAND_PIXEL, groesse[1] - 2 * RAND_PIXEL),
                                       Image.Resampling.LANCZOS)
                        position = ((groesse[0] - bild.width) // 2, (groesse[1] - bild.height) // 2)
                        if bild.mode in {"RGBA", "LA"} or "transparency" in bild.info:
                            with bild.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
                                leinwand.paste(rgba, position, alpha)
                        else:
                            with bild.convert(modus) as normalisiert:
                                leinwand.paste(normalisiert, position)
                        # Pillow bettet P-Bilder unkomprimiert (ASCIIHex) ein -> immer JPEG,
                        # Plaene/Screenshots aber mit Q95 ohne Farb-Subsampling (scharfe Linien).
                        return leinwand.copy(), verlustfrei
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise BildFehler("Bild zu groß", 413) from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise BildFehler("Bild konnte nicht gelesen werden", 415) from exc


def bilder_zu_pdf(bilder: list[tuple[bytes, str]]) -> tuple[bytes, str]:
    """Erzeugt eine PDF-Seite pro Bild in der uebergebenen Reihenfolge."""
    if not bilder:
        raise BildFehler("Keine Bilder uebergeben")
    with PdfWriter() as writer:
        for data, _ in bilder:
            seite, plan = _bild_auf_leinwand(data)
            with seite, io.BytesIO() as einzel_pdf:
                if plan:
                    seite.save(einzel_pdf, "PDF", resolution=300, quality=95, subsampling=0)
                else:
                    seite.save(einzel_pdf, "PDF", resolution=300, quality=85)
                writer.add_page(PdfReader(einzel_pdf).pages[0])
        with io.BytesIO() as ergebnis:
            writer.write(ergebnis)
            return ergebnis.getvalue(), (Path(bilder[0][1]).stem or "foto") + ".pdf"


def bild_zu_pdf(data: bytes, dateiname: str) -> tuple[bytes, str]:
    """Wrapper fuer ein einzelnes Bild."""
    return bilder_zu_pdf([(data, dateiname)])
