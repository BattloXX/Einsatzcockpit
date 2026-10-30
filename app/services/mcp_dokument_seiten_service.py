"""Shared presentation helpers for MCP object-document pages."""

from __future__ import annotations

import base64
import io
import math
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


def _ausschnitt_pruefen(ausschnitt: list[float] | None) -> None:
    if ausschnitt is None:
        return
    if (not isinstance(ausschnitt, list) or len(ausschnitt) != 4
            or any(isinstance(w, bool) or not isinstance(w, (int, float))
                   or not math.isfinite(w) or not 0 <= w <= 1 for w in ausschnitt)):
        raise ValueError("Ausschnitt: vier endliche Zahlen in 0..1 erforderlich")
    x0, y0, x1, y1 = ausschnitt
    if x1 - x0 < 0.02 - 1e-12 or y1 - y0 < 0.02 - 1e-12:
        raise ValueError("Ausschnitt: x0 < x1 und y0 < y1; Breite und Hoehe mindestens 0.02")


def _bildpfad(seite: Any) -> Path | None:
    for relativ in (seite.bild_pfad, seite.thumb_pfad):
        if relativ:
            pfad = absolute_pfad(relativ)
            if pfad.is_file():
                return pfad
    return None


def bild_anhaengen(ergebnis: dict[str, object], seite: Any, max_kante_px: int,
                   ausschnitt: list[float] | None = None) -> None:
    """Haengt das gedrehte, gegebenenfalls zugeschnittene Seitenbild an."""
    daten, vorhanden = seiten_bild(seite, max_kante_px, ausschnitt)
    rotation = getattr(seite, "rotation", 0) or 0
    ergebnis["rotation"] = rotation
    ergebnis["bildgroesse"] = None
    pfad = _bildpfad(seite)
    if pfad:
        with Image.open(pfad) as bild:
            breite, hoehe = bild.size
        ergebnis["bildgroesse"] = [hoehe, breite] if rotation % 180 else [breite, hoehe]
    ergebnis["koordinaten_hinweis"] = "Koordinaten normiert auf die gelieferte (gedrehte) Gesamtseite."
    if ausschnitt is not None:
        ergebnis["ausschnitt"] = list(ausschnitt)
        ergebnis["koordinaten_hinweis"] = (
            "bild_x = x0 + u*(x1-x0), bild_y = y0 + v*(y1-y0); u,v normiert im Ausschnitt"
        )
    ergebnis["hat_bild"] = vorhanden
    if daten:
        ergebnis["bild_base64"] = daten
        ergebnis["bild_mime"] = "image/jpeg"
    else:
        ergebnis["hinweis"] = SEITE_ANSEHEN_HINWEIS


def seiten_bild(seite: Any, max_kante_px: int,
                ausschnitt: list[float] | None = None) -> tuple[str | None, bool]:
    """Dreht und schneidet vor dem Verkleinern zu, auch beim Thumbnail-Fallback."""
    _ausschnitt_pruefen(ausschnitt)
    max_kante_px = max(400, min(2400, max_kante_px))
    pfad = _bildpfad(seite)
    if pfad is None:
        return None, False
    with Image.open(pfad) as bild:
        rgb = bild.convert("RGB")
        rotation = (getattr(seite, "rotation", 0) or 0) % 360
        if rotation:
            rgb = rgb.rotate(-rotation, expand=True)
        if ausschnitt is not None:
            x0, y0, x1, y1 = ausschnitt
            breite, hoehe = rgb.size
            rgb = rgb.crop((math.floor(x0 * breite), math.floor(y0 * hoehe),
                            math.ceil(x1 * breite), math.ceil(y1 * hoehe)))
        rgb.thumbnail((max_kante_px, max_kante_px))
        ausgabe = io.BytesIO()
        rgb.save(ausgabe, format="JPEG", quality=80)
    return base64.b64encode(ausgabe.getvalue()).decode("ascii"), True


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
