"""Wiederverwendbare PDF-Pruefung, Seitenzerlegung, Rasterung und Textextraktion."""
from __future__ import annotations

import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("einsatzleiter.objekt_dokument")

RenderFunc = Callable[[Path, int, int], bytes | None]
OcrFunc = Callable[[bytes], str]


class PdfFehler(Exception):
    """Fachlicher PDF-Fehler ohne HTTP- oder Modell-Abhaengigkeit."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def pruefe_pdf(
    data: bytes,
    *,
    max_bytes: int,
    max_seiten: int,
    detect_mime: Callable[[bytes], str | None],
) -> int:
    """Prueft Upload-Limits und Lesbarkeit und gibt die Seitenzahl zurueck."""
    if not data:
        raise PdfFehler("Leere Datei")

    if len(data) > max_bytes:
        raise PdfFehler(f"Datei zu gross (max. {max_bytes // (1024 * 1024)} MB)", 413)

    mime = detect_mime(data)
    if mime != "application/pdf":
        raise PdfFehler("Nur PDF-Dateien erlaubt", 415)

    # Seitenzahl + Validierung via pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        seitenzahl = len(reader.pages)
    except Exception as exc:
        raise PdfFehler("PDF konnte nicht gelesen werden", 415) from exc

    if seitenzahl > max_seiten:
        raise PdfFehler(f"PDF hat {seitenzahl} Seiten (max. {max_seiten})", 413)

    return seitenzahl


def render_seite_png_poppler(pdf_path: Path, seiten_nr: int, dpi: int) -> bytes | None:
    """Rastert eine PDF-Seite via pdf2image/Poppler. None wenn nicht verfuegbar.

    Entscheidung 2026-07-05: pdf2image + Poppler (Prod = Debian, apt install
    poppler-utils) statt PyMuPDF (AGPL). Kapselung haelt einen Backend-Tausch lokal.
    """
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        logger.warning("pdf2image nicht installiert — Seiten-Rendering uebersprungen")
        return None
    try:
        bilder = convert_from_path(
            str(pdf_path), dpi=dpi, first_page=seiten_nr, last_page=seiten_nr,
        )
    except Exception:
        logger.exception("Poppler-Rendering fehlgeschlagen (%s Seite %d)", pdf_path, seiten_nr)
        return None
    if not bilder:
        return None
    buf = io.BytesIO()
    bilder[0].save(buf, format="PNG")
    return buf.getvalue()


def ocr_tesseract(png: bytes) -> str:
    """OCR eines Seitenbilds via Tesseract. "" wenn pytesseract/Binary fehlt (CI/Tests)."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract nicht installiert — OCR uebersprungen")
        return ""
    try:
        img = Image.open(io.BytesIO(png))
        return pytesseract.image_to_string(img, lang=settings.OBJEKT_OCR_LANG)
    except Exception:
        logger.exception("Tesseract-OCR fehlgeschlagen")
        return ""


def normalisiere_text(text: str) -> str:
    """Whitespace normalisieren + auf die konfigurierte Maximallaenge kappen."""
    zusammen = " ".join((text or "").split())
    return zusammen[: settings.OBJEKT_VOLLTEXT_MAX_CHARS]


def extrahiere_seitentext(
    page: object | None,
    png: bytes | None,
    ocr_func: OcrFunc,
) -> tuple[str | None, str]:
    """Ermittelt den Volltext einer Seite: erst PDF-Textlayer (pypdf), sonst OCR.

    Gibt (volltext, quelle) zurueck — quelle ∈ pdf/ocr/none. Injizierbare ocr_func
    fuer Tests/CI ohne Tesseract.
    """
    text = ""
    if page is not None:
        try:
            text = normalisiere_text(page.extract_text() or "")  # type: ignore[attr-defined]
        except Exception:
            text = ""
    if text and len(text) >= settings.OBJEKT_OCR_MIN_CHARS:
        return text, "pdf"
    # Textlayer fehlt/zu kurz → OCR auf dem gerenderten Seitenbild versuchen
    if settings.OBJEKT_OCR_ENABLED and png:
        ocr_text = normalisiere_text(ocr_func(png))
        if len(ocr_text) >= settings.OBJEKT_OCR_MIN_CHARS:
            return ocr_text, "ocr"
    if text:
        return text, "pdf"
    return None, "none"


@dataclass
class SeitenErgebnis:
    seiten_nr: int
    einzel_pdf_name: str
    bild_name: str | None
    thumb_name: str | None
    volltext: str | None
    text_quelle: str
    bytes_belegt: int


def zerlege_pdf(
    original: Path,
    dest_dir: Path,
    *,
    render_func: RenderFunc,
    ocr_func: OcrFunc,
    dpi: int,
    thumb_groesse: int,
    log_kontext: str,
) -> list[SeitenErgebnis]:
    """Erzeugt Seitendateien und Volltext; Dateinamen sind relativ zu dest_dir."""
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(original))
    ergebnisse = []

    for i, page in enumerate(reader.pages, start=1):
        bytes_belegt = 0
        # 1) Verlustfreie Einzelseite
        writer = PdfWriter()
        writer.add_page(page)
        einzel = dest_dir / f"seite_{i:04d}.pdf"
        with einzel.open("wb") as fh:
            writer.write(fh)
        bytes_belegt += einzel.stat().st_size

        # 2) Hi-Res-Rendering + Thumb (optional, wenn Poppler verfuegbar)
        bild_name: str | None = None
        thumb_name: str | None = None
        png = render_func(original, i, dpi)
        if png:
            bild = dest_dir / f"seite_{i:04d}.png"
            bild.write_bytes(png)
            bytes_belegt += len(png)
            bild_name = f"seite_{i:04d}.png"

            try:
                img: Any = Image.open(io.BytesIO(png))
                img.thumbnail((thumb_groesse, thumb_groesse * 2))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                thumb = dest_dir / f"seite_{i:04d}_thumb.jpg"
                img.save(thumb, "JPEG", quality=80)
                bytes_belegt += thumb.stat().st_size
                thumb_name = f"seite_{i:04d}_thumb.jpg"
            except Exception:
                logger.exception("Thumb-Erzeugung fehlgeschlagen (%s Seite %d)",
                                 log_kontext, i)

        # 3) Volltext fuer die Suche (PDF-Textlayer, sonst OCR auf dem Rendering)
        volltext, text_quelle = extrahiere_seitentext(page, png, ocr_func)

        ergebnisse.append(SeitenErgebnis(
            seiten_nr=i,
            einzel_pdf_name=einzel.name,
            bild_name=bild_name,
            thumb_name=thumb_name,
            volltext=volltext,
            text_quelle=text_quelle,
            bytes_belegt=bytes_belegt,
        ))
    return ergebnisse


def entferne_seitendateien(dest_dir: Path) -> None:
    """Entfernt abgeleitete Seitendateien, behaelt das Original bei."""
    for f in dest_dir.glob("seite_*"):
        f.unlink(missing_ok=True)
