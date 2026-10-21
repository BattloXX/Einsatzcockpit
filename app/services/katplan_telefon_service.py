"""Umwandlung von Telefonnummern in zentrale Kontaktverknuepfungen."""

import json
import re
from html import escape
from html.parser import HTMLParser
from typing import Any

from sqlalchemy.orm import Session

from app.core.telefon import telefon_identitaet_at, telefon_normalisiert
from app.models.katplan import KatplanElement, KatplanPlan
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.user import User
from app.services.katplan_service import (
    aktualisiere_element_suchtext,
    bereinige_beschreibung,
    markiere_ueberarbeitung,
    protokolliere,
)


def kontakt_treffer(db: Session, org_id: int, nummer: str) -> list[Kontakt]:
    normalisiert = telefon_normalisiert(nummer)
    identitaet = telefon_identitaet_at(nummer)
    varianten = {normalisiert, identitaet}
    if identitaet.startswith("+43"):
        varianten.add("0" + identitaet[3:])
    return db.query(Kontakt).join(KontaktTelefon).filter(
        Kontakt.org_id == org_id, Kontakt.archiviert.is_(False), KontaktTelefon.org_id == org_id,
        KontaktTelefon.nummer_normalisiert.in_(varianten),
    ).order_by(Kontakt.anzeigename, Kontakt.id).distinct().all()


def _ohne_nummer(wert: str, nummer: str) -> str:
    muster = r"(?:\b(?:Tel\.?|Telefon)\s*:\s*)?" + re.escape(nummer)
    return re.sub(r"[ \t]{2,}", " ", re.sub(muster, "", wert, flags=re.IGNORECASE)).strip()


def _daten_ohne_nummer(wert: Any, nummer: str) -> Any:
    if isinstance(wert, str):
        return _ohne_nummer(wert, nummer)
    if isinstance(wert, dict):
        return {key: _daten_ohne_nummer(kind, nummer) for key, kind in wert.items()}
    if isinstance(wert, list):
        return [_daten_ohne_nummer(kind, nummer) for kind in wert]
    return wert


class _BeschreibungTeile(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.teile: list[tuple[bool, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.teile.append((True, self.get_starttag_text() or f"<{tag}>"))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self.teile.append((True, f"</{tag}>"))

    def handle_data(self, data: str) -> None:
        self.teile.append((False, data))


def _html_ohne_nummer(html: str, nummer: str) -> str | None:
    # Tags bleiben erhalten, auch wenn eine Nummer ueber Inline-Tags verteilt ist.
    parser = _BeschreibungTeile()
    parser.feed(bereinige_beschreibung(html) or "")
    parser.close()
    teile = [wert for _, wert in parser.teile]
    sichtbar = ""
    bereiche = []
    for i, (ist_tag, wert) in enumerate(parser.teile):
        if ist_tag:
            if re.match(r"</?(?:p|li|h[1-6]|ul|ol)\b|<br\b", wert, re.I):
                sichtbar += "\n"
        else:
            start = len(sichtbar)
            sichtbar += wert
            bereiche.append((i, start, len(sichtbar)))
    # Die Erkennung verwendet normalisierten sichtbaren Text; beliebige Leerraeume
    # innerhalb derselben Originalschreibweise deshalb ebenfalls beruecksichtigen.
    nummer_muster = r"[^\S\n]+".join(re.escape(t) for t in nummer.split())
    muster = r"(?:\b(?:Tel\.?|Telefon)\s*:\s*)?" + nummer_muster
    treffer = list(re.finditer(muster, sichtbar, re.I))
    for i, start, ende in bereiche:
        wert = teile[i]
        for match in reversed(treffer):
            links, rechts = max(start, match.start()), min(ende, match.end())
            if links < rechts:
                wert = wert[:links - start] + wert[rechts - start:]
        teile[i] = escape(re.sub(r"[ \t]{2,}", " ", wert), quote=False)
    return bereinige_beschreibung("".join(teile))


def nummer_entfernen(db: Session, plan: KatplanPlan, element: KatplanElement, user: User, nummer: str) -> None:
    for feld, neu in (
        ("beschreibung_html", _html_ohne_nummer(element.beschreibung_html or "", nummer)),
        ("daten_json", json.dumps(_daten_ohne_nummer(json.loads(element.daten_json or "{}"), nummer),
                                  ensure_ascii=False)),
    ):
        alt = getattr(element, feld)
        if alt != neu:
            setattr(element, feld, neu)
            protokolliere(db, plan, "element", "geaendert", user, element_id=element.id,
                          feld=feld, alt=alt, neu=neu)
    element.version += 1
    element.aktualisiert_von_id = user.id
    aktualisiere_element_suchtext(element)
    markiere_ueberarbeitung(db, plan, user)
