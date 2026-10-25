"""Seitenauswahl und verlustfreie PDF-Auszüge ohne Änderung der Originaldatei."""

import io
import re
from collections.abc import Mapping
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response
from pypdf import PdfReader, PdfWriter


def seiten_auswahl(text: str, anzahl: int) -> list[int]:
    if not isinstance(text, str) or len(text) > 2048 or anzahl < 1:
        raise ValueError("Bitte eine gültige Seitenauswahl eingeben.")
    text = re.sub(r"^(?:seiten?|s\.)\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\bund\b", ",", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", "", text).replace("–", "-").replace("—", "-")
    if text.lower() in ("alle", "all", "*"):
        text = f"1-{anzahl}"
    nummern: set[int] = set()
    for teil in text.split(","):
        match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", teil)
        if not match:
            raise ValueError("Seiten z. B. als 2, 5–8 oder 3–5, 12–15 eingeben.")
        start, ende = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= ende <= anzahl:
            raise ValueError(f"Bitte Seiten zwischen 1 und {anzahl} auswählen.")
        if ende - start + 1 > 2000 or len(nummern) + ende - start + 1 > 4000:
            raise ValueError("Höchstens 2000 Seiten auf einmal auswählen.")
        nummern.update(range(start, ende + 1))
    if len(nummern) > 2000:
        raise ValueError("Höchstens 2000 Seiten auf einmal auswählen.")
    return sorted(nummern)


def pdf_auszug(quelle: Path | bytes, seiten: str) -> bytes:
    try:
        reader = PdfReader(io.BytesIO(quelle) if isinstance(quelle, bytes) else quelle)
        nummern = seiten_auswahl(seiten, len(reader.pages))
        writer = PdfWriter()
        for nr in nummern:
            writer.add_page(reader.pages[nr - 1])
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Das PDF ist beschädigt, geschützt oder kann nicht gelesen werden.") from exc


def auszug_antwort(quelle: Path | bytes, seiten: str) -> Response:
    return Response(pdf_auszug(quelle, seiten), media_type="application/pdf", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="dokument-auswahl.pdf"',
    })


def viewer_auswahl(quelle: Path | bytes, parameter: Mapping[str, str]) -> Response:
    """Fester Dokumentauszug zuerst; Benutzerauswahl bezieht sich auf dessen Seiten."""
    if "seiten" in parameter:
        quelle = pdf_auszug(quelle, parameter["seiten"])
    return auszug_antwort(quelle, parameter.get("auswahl", "alle"))
