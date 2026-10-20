"""Quellenvertrag, Registry und Textaufbereitung fuer den Suchindex."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IndexChunk:
    text: str
    seite: int | None = None
    abschnitt: str | None = None


@dataclass(frozen=True)
class IndexDokument:
    quell_id: str
    titel: str
    untertitel: str | None
    keywords: str
    chunks: list[IndexChunk]
    sichtbarkeit: str = "standard"
    lat: float | None = None
    lng: float | None = None
    meta: dict[str, int | str | None] = field(default_factory=dict)
    geaendert_am: datetime | None = None


class QuellenAdapter(Protocol):
    quelle: str

    def dokument(self, db: Session, org_id: int, quell_id: str) -> IndexDokument | None: ...

    def alle_ids(self, db: Session, org_id: int) -> list[str]: ...


ADAPTER: dict[str, QuellenAdapter] = {}


def registriere(adapter: QuellenAdapter) -> None:
    ADAPTER[adapter.quelle] = adapter


def lade_adapter() -> None:
    from app.services.suche import adapter  # noqa: F401


def adapter_fuer(quelle: str) -> QuellenAdapter:
    lade_adapter()
    return ADAPTER[quelle]


def zerlege_text(text: str, max_zeichen: int = 2500, ueberlappung: int = 300) -> list[str]:
    """Teilt bevorzugt an Absaetzen/Saetzen, sonst an Wortgrenzen.

    Ein einzelnes ueberlanges Wort bleibt ganz; die Ueberlappung wird auf
    vollstaendige Woerter begrenzt und darf den Fortschritt nicht verhindern.
    """
    if max_zeichen <= 0 or not 0 <= ueberlappung < max_zeichen:
        raise ValueError("Ungueltige Chunk-Groesse oder Ueberlappung")
    text = text.strip()
    chunks: list[str] = []
    start = 0
    while start < len(text):
        ende = min(start + max_zeichen, len(text))
        if ende < len(text):
            fenster = text[start:ende + 1]
            grenzen = [m.start() for m in re.finditer(r"\s+", fenster)]
            if grenzen:
                ende = start + grenzen[-1]
                # Kurze Reststuecke vermeiden, ohne Absatz/Satzgrenzen zu verlieren.
                for muster in (r"\n\s*\n", r"(?<=[.!?])\s+"):
                    bevorzugt = [m.start() for m in re.finditer(muster, fenster)
                                 if m.start() >= max_zeichen // 2]
                    if bevorzugt:
                        ende = start + bevorzugt[-1]
                        break
            else:
                rest = re.search(r"\s", text[ende:])
                ende = ende + rest.start() if rest else len(text)
        chunks.append(text[start:ende].strip())
        if ende == len(text):
            break
        naechster = max(start + 1, ende - ueberlappung)
        while naechster < ende and not text[naechster - 1].isspace():
            naechster += 1
        while naechster < len(text) and text[naechster].isspace():
            naechster += 1
        start = naechster
    return chunks


def html_zu_text(html: str | None) -> str:
    from app.services.katplan_service import _HTMLText

    parser = _HTMLText()
    parser.feed(html or "")
    parser.close()
    return " ".join("".join(parser.teile).split())
