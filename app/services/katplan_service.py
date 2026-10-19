"""Grundfunktionen fuer Suche, Kennungen und Aenderungsprotokolle."""

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any

from sqlalchemy.orm import Session

from app.models.katplan import KatplanAenderung, KatplanElement, KatplanPlan
from app.models.user import User

_KENNUNG_PRAEFIXE = {
    "kontrollstelle": "KS", "massnahme": "M", "info": "I", "gefahrenbereich": "GB",
    "ablauf": "A", "ressource": "R", "medien": "D", "zustaendigkeit": "Z", "objektverweis": "O",
}


class KatplanKonflikt(Exception):
    """Der Arbeitsstand wurde zwischenzeitlich von jemand anderem geaendert."""


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.teile: list[str] = []

    def handle_data(self, data: str) -> None:
        self.teile.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.teile.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.teile.append(" ")


def _strings(wert: Any) -> Iterator[str]:
    if isinstance(wert, str):
        yield wert
    elif isinstance(wert, dict):
        for value in wert.values():
            yield from _strings(value)
    elif isinstance(wert, list):
        for value in wert:
            yield from _strings(value)


def baue_suchtext(element: KatplanElement) -> str:
    parser = _HTMLText()
    parser.feed(element.beschreibung_html or "")
    parser.close()
    teile = [element.titel or "", element.kennung or "", "".join(parser.teile)]
    for roh in (element.daten_json, element.tags_json):
        try:
            daten = json.loads(roh or "null")
        except (ValueError, TypeError):
            continue
        teile.extend(_strings(daten))
    teile.extend([element.strasse or "", element.hausnummer or "", element.plz or "", element.ort or ""])
    return " ".join(" ".join(teile).lower().split())[:20000]


def naechste_kennung(db: Session, plan: KatplanPlan, typ: str) -> str:
    if typ not in _KENNUNG_PRAEFIXE:
        raise ValueError(f"Unbekannter Elementtyp: {typ}")
    praefix = _KENNUNG_PRAEFIXE[typ]
    muster = re.compile(rf"{praefix}-(\d+)")
    # Noch nicht geflushte Elemente derselben Transaktion mitzaehlen.
    db.flush()
    kennungen = db.query(KatplanElement.kennung).filter(
        KatplanElement.org_id == plan.org_id,
        KatplanElement.plan_id == plan.id,
        KatplanElement.kennung.like(f"{praefix}-%"),
    ).all()
    nummer = max((int(treffer[1]) for (kennung,) in kennungen
                  if kennung and (treffer := muster.fullmatch(kennung))), default=0) + 1
    return f"{praefix}-{nummer:02d}"


def protokolliere(
    db: Session, plan: KatplanPlan, entitaet: str, aktion: str, user: User | None,
    feld: str | None = None, alt: str | None = None, neu: str | None = None,
    element_id: int | None = None, quelle: str = "ui",
) -> KatplanAenderung:
    aenderung = KatplanAenderung(
        org_id=plan.org_id, plan_id=plan.id, entitaet=entitaet, aktion=aktion,
        user_id=user.id if user else None, feld=feld, alt=alt, neu=neu,
        element_id=element_id, quelle=quelle,
    )
    db.add(aenderung)
    return aenderung


def pruefe_version(obj: KatplanPlan | KatplanElement, erwartet: int) -> None:
    if obj.version != erwartet:
        raise KatplanKonflikt("Der Arbeitsstand wurde zwischenzeitlich geaendert. Bitte neu laden.")
