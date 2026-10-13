"""Shared presentation helpers for MCP object-document pages."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from PIL import Image

from app.services.objekt_dokument_service import absolute_pfad

SEITE_ANSEHEN_HINWEIS = "Kein gerendertes Seitenbild vorhanden."


def seite_ergebnis(dokument: Any, seite: Any) -> dict[str, object]:
    """Metadaten und OCR-Text einer Seite fuer die MCP-Ansicht."""
    return {
        "dokument_id": dokument.id,
        "objekt_id": dokument.objekt_id,
        "dateiname": dokument.dateiname_original,
        "seitenzahl": dokument.seitenzahl,
        "seite": seite.seiten_nr,
        "dokumentart": seite.dokumentart,
        "titel": seite.titel,
        "melderlinien": seite.melderlinien,
        "stand": seite.stand.isoformat() if seite.stand else None,
        "volltext": seite.volltext,
        "text_quelle": seite.text_quelle,
        "hat_bild": bool(seite.bild_pfad or seite.thumb_pfad),
    }


def bild_anhaengen(ergebnis: dict[str, object], seite: Any, max_kante_px: int) -> None:
    """Haengt das verkleinerte Seitenbild (JPEG, Base64) an das Ergebnis an."""
    daten, vorhanden = seiten_bild(seite, max_kante_px)
    ergebnis["hat_bild"] = vorhanden
    if daten:
        ergebnis["bild_base64"] = daten
        ergebnis["bild_mime"] = "image/jpeg"
    else:
        ergebnis["hinweis"] = SEITE_ANSEHEN_HINWEIS


def seiten_bild(seite: Any, max_kante_px: int) -> tuple[str | None, bool]:
    """Return a bounded JPEG data payload and whether an image was available."""
    max_kante_px = max(400, min(2400, max_kante_px))
    for relativ in (seite.bild_pfad, seite.thumb_pfad):
        if not relativ:
            continue
        pfad: Path = absolute_pfad(relativ)
        if not pfad.is_file():
            continue
        with Image.open(pfad) as bild:
            rgb = bild.convert("RGB")
            rgb.thumbnail((max_kante_px, max_kante_px))
            ausgabe = io.BytesIO()
            rgb.save(ausgabe, format="JPEG", quality=80)
        return base64.b64encode(ausgabe.getvalue()).decode("ascii"), True
    return None, False


def seiten_text(seiten: list[Any], max_zeichen: int) -> dict[str, object]:
    """OCR-Index mehrerer Seiten, gekappt auf max_zeichen.

    naechste_seite zeigt immer nach vorn: eine einzelne Seite, die allein das
    Limit sprengt, wird gekuerzt geliefert (seite_gekuerzt), damit seitenweises
    Weiterlesen nicht endlos dieselbe Seite anfordert.
    """
    max_zeichen = max(1000, min(200000, max_zeichen))
    items: list[dict[str, object]] = []
    benutzt = 0
    for index, seite in enumerate(seiten):
        text = seite.volltext or ""
        if items and benutzt + len(text) > max_zeichen:
            return {"seiten": items, "abgeschnitten": True, "naechste_seite": seite.seiten_nr}
        if len(text) > max_zeichen:
            item = _text_item(seite, text[:max_zeichen])
            item["seite_gekuerzt"] = True
            items.append(item)
            folgende = seiten[index + 1].seiten_nr if index + 1 < len(seiten) else None
            return {"seiten": items, "abgeschnitten": True, "naechste_seite": folgende}
        items.append(_text_item(seite, text))
        benutzt += len(text)
    return {"seiten": items, "abgeschnitten": False, "naechste_seite": None}


def _text_item(seite: Any, volltext: str) -> dict[str, object]:
    return {
        "nr": seite.seiten_nr,
        "dokumentart": seite.dokumentart,
        "titel": seite.titel,
        "text_quelle": seite.text_quelle,
        "volltext": volltext,
    }
