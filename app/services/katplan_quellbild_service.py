"""Eingebettete Quellbilder und Ausschnitte ohne Datenbankmutationen."""

import logging
import math
from dataclasses import dataclass
from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from app.models.katplan import KatplanDokumentSeite
from app.services.katplan_quelle_service import absolute_pfad

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bildinfo:
    index: int
    breite: int
    hoehe: int
    format: str
    bytes: int


def eingebettete_bilder(seite: KatplanDokumentSeite, *, warnings: list[str] | None = None) -> list[Bildinfo]:
    """Originalindizes beibehalten; defekte PDFs/Bilder melden, maximal 20 grosse Bilder."""
    bilder: list[Bildinfo] = []
    try:
        reader = PdfReader(absolute_pfad(seite.einzel_pdf_pfad))
        for index, bild in enumerate(reader.pages[0].images):
            try:
                with Image.open(BytesIO(bild.data)) as img:
                    if min(img.size) >= 200:
                        bilder.append(Bildinfo(index, img.width, img.height, img.format or "", len(bild.data)))
                if len(bilder) >= 20:
                    break
            except Exception:
                logger.warning("Quellbild %s nicht lesbar", index, exc_info=True)
                if warnings is not None:
                    warnings.append(f"Bild {index}: nicht lesbar.")
    except Exception:
        logger.warning("Quellseiten-PDF nicht lesbar: %s", seite.id, exc_info=True)
        if warnings is not None:
            warnings.append("Eingebettete Bilder konnten nicht vollständig gelesen werden.")
        return []
    return bilder


def jpeg_bytes(img: Image.Image, max_kante: int | None = None) -> bytes:
    """Auch CMYK, Paletten und Transparenz als RGB-JPEG ausgeben."""
    rgb = img.convert("RGB")
    if max_kante is not None:
        rgb.thumbnail((max_kante, max_kante))
    out = BytesIO()
    rgb.save(out, "JPEG", quality=85)
    return out.getvalue()


def bild_bytes(seite: KatplanDokumentSeite, index: int) -> tuple[bytes, str]:
    if type(index) is not int or index not in {b.index for b in eingebettete_bilder(seite)}:
        raise ValueError("Eingebettetes Bild nicht gefunden.")
    try:
        reader = PdfReader(absolute_pfad(seite.einzel_pdf_pfad))
        with Image.open(BytesIO(reader.pages[0].images[index].data)) as img:
            return jpeg_bytes(img), "image/jpeg"
    except Exception as exc:
        raise ValueError("Eingebettetes Bild nicht lesbar.") from exc


def seitenbild_info(seite: KatplanDokumentSeite) -> dict[str, int] | None:
    if not seite.bild_pfad:
        return None
    try:
        with Image.open(absolute_pfad(seite.bild_pfad)) as img:
            return {"breite": img.width, "hoehe": img.height}
    except (OSError, ValueError):
        logger.warning("Seitenbild nicht lesbar: %s", seite.id, exc_info=True)
        return None


def ausschnitt(seite: KatplanDokumentSeite, rahmen: list[float]) -> bytes:
    if (not isinstance(rahmen, list) or len(rahmen) != 4
            or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in rahmen)):
        raise ValueError("Rahmen muss vier relative Koordinaten zwischen 0 und 1 enthalten.")
    x0, y0, x1, y1 = rahmen
    if x1 - x0 < 0.05 - 1e-10 or y1 - y0 < 0.05 - 1e-10:
        raise ValueError("Rahmen benötigt mindestens 5 % je Achse.")
    if not seite.bild_pfad:
        raise ValueError("Kein Seitenbild vorhanden.")
    try:
        with Image.open(absolute_pfad(seite.bild_pfad)) as img:
            return jpeg_bytes(img.crop((round(x0 * img.width), round(y0 * img.height),
                                        round(x1 * img.width), round(y1 * img.height))))
    except (OSError, ValueError) as exc:
        raise ValueError("Seitenbild nicht lesbar.") from exc
