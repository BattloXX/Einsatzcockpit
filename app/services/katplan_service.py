"""Grundfunktionen fuer Suche, Kennungen und Aenderungsprotokolle."""

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.core.html_utils import sanitize_html
from app.models.katplan import KatplanAbschnitt, KatplanAenderung, KatplanElement, KatplanLink, KatplanPlan
from app.models.user import User
from app.services.katplan_ziel_service import _anzeige, ziel_laden

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


def baue_suchtext(element: KatplanElement, link_titel: list[str] | None = None) -> str:
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
    if link_titel is None:
        db = object_session(element)
        link_titel = []
        if db is not None and element.id is not None:
            for link in db.query(KatplanLink).filter_by(org_id=element.org_id, element_id=element.id,
                                                       plan_id=element.plan_id).order_by(
                                                           KatplanLink.sort, KatplanLink.id):
                ziel = ziel_laden(db, element.org_id, link.ziel_typ, link.ziel_id)
                if ziel is not None:
                    link_titel.append(_anzeige(ziel)[0])
    teile.extend(link_titel)
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



def markiere_ueberarbeitung(db: Session, plan: KatplanPlan, user: User) -> None:
    """Inhaltsaenderungen schuetzen unabhaengig von der Kopfdaten-Version."""
    if plan.status == "freigegeben":
        protokolliere(db, plan, "plan", "geaendert", user, feld="status",
                      alt=plan.status, neu="in_ueberarbeitung")
        plan.status = "in_ueberarbeitung"


def aktualisiere_element_suchtext(element: KatplanElement) -> None:
    element.suchtext = baue_suchtext(element)


def abschnitte_baum(db: Session, plan: KatplanPlan) -> list[dict[str, Any]]:
    """Jeden Knoten genau einmal aufnehmen, auch bei beschaedigten Elternketten."""
    abschnitte = db.query(KatplanAbschnitt).filter_by(
        org_id=plan.org_id, plan_id=plan.id,
    ).order_by(KatplanAbschnitt.sort, KatplanAbschnitt.id).all()
    elemente = db.query(KatplanElement).filter_by(
        org_id=plan.org_id, plan_id=plan.id,
    ).order_by(KatplanElement.sort, KatplanElement.id).all()
    knoten: dict[int, dict[str, Any]] = {
        a.id: {"abschnitt": a, "kinder": [], "elemente": []} for a in abschnitte
    }
    wurzeln: list[dict[str, Any]] = []
    ohne: dict[str, Any] = {"abschnitt": None, "kinder": [], "elemente": []}
    for abschnitt in abschnitte:
        eltern = abschnitt.parent_id
        gesehen = {abschnitt.id}
        while eltern is not None and eltern in knoten and eltern not in gesehen:
            gesehen.add(eltern)
            eltern = knoten[eltern]["abschnitt"].parent_id
        if abschnitt.parent_id is not None and abschnitt.parent_id in knoten and eltern not in gesehen:
            knoten[abschnitt.parent_id]["kinder"].append(knoten[abschnitt.id])
        else:
            wurzeln.append(knoten[abschnitt.id])
    for element in elemente:
        ziel = knoten.get(element.abschnitt_id, ohne) if element.abschnitt_id is not None else ohne
        ziel["elemente"].append(element)
    return [*wurzeln, ohne]


def loesche_abschnitt(db: Session, plan: KatplanPlan, abschnitt: KatplanAbschnitt, user: User) -> None:
    for kind in db.query(KatplanAbschnitt).filter_by(
        org_id=plan.org_id, plan_id=plan.id, parent_id=abschnitt.id,
    ).all():
        kind.parent_id = abschnitt.parent_id
    for element in db.query(KatplanElement).filter_by(
        org_id=plan.org_id, plan_id=plan.id, abschnitt_id=abschnitt.id,
    ).all():
        element.abschnitt_id = abschnitt.parent_id
        element.version += 1
        element.aktualisiert_von_id = user.id
        aktualisiere_element_suchtext(element)
        protokolliere(db, plan, "element", "geaendert", user, feld="abschnitt_id",
                      alt=str(abschnitt.id), neu=str(abschnitt.parent_id) if abschnitt.parent_id else None,
                      element_id=element.id)
    # Vor dem DELETE flushen, damit ON DELETE CASCADE keine Unterkapitel entfernt.
    db.flush()
    db.delete(abschnitt)
    protokolliere(db, plan, "abschnitt", "geloescht", user, alt=abschnitt.titel)
    markiere_ueberarbeitung(db, plan, user)


def bereinige_beschreibung(roh: str | None) -> str | None:
    """Quill-Leerabsätze ebenfalls als fehlende Beschreibung behandeln."""
    html = sanitize_html(roh)
    parser = _HTMLText()
    parser.feed(html or "")
    return html if "".join(parser.teile).strip() else None


def sortiere_baum(db: Session, plan: KatplanPlan, user: User,
                  abschnitte: list[dict[str, Any]], elemente: list[dict[str, Any]]) -> None:
    """Gesamten Baum vor der ersten Mutation auf Zugehörigkeit und Zyklen prüfen."""
    kapitel = {a.id: a for a in db.query(KatplanAbschnitt).filter_by(
        org_id=plan.org_id, plan_id=plan.id,
    ).all()}
    eintraege = {e.id: e for e in db.query(KatplanElement).filter_by(
        org_id=plan.org_id, plan_id=plan.id,
    ).all()}
    for daten, bestand, elternfeld in ((abschnitte, kapitel, "parent_id"),
                                       (elemente, eintraege, "abschnitt_id")):
        if len(daten) != len(bestand) or {d["id"] for d in daten} != set(bestand):
            raise ValueError("Die Sortierung muss alle Einträge dieses Plans genau einmal enthalten.")
        if any(d[elternfeld] is not None and d[elternfeld] not in kapitel for d in daten):
            raise ValueError("Unbekanntes Kapitel in der Sortierung.")
    eltern = {d["id"]: d["parent_id"] for d in abschnitte}
    for aid in eltern:
        gesehen: set[int] = set()
        aktuell: int | None = aid
        while aktuell is not None:
            if aktuell in gesehen:
                raise ValueError("Kapitel dürfen keinen Zyklus bilden.")
            gesehen.add(aktuell)
            aktuell = eltern[aktuell]
    geaendert = False
    for daten, bestand, elternfeld in ((abschnitte, kapitel, "parent_id"),
                                       (elemente, eintraege, "abschnitt_id")):
        zaehler: dict[int | None, int] = {}
        for d in sorted(daten, key=lambda d: d["sort"]):
            parent = d[elternfeld]
            zaehler[parent] = zaehler.get(parent, 0) + 10
            obj = bestand[d["id"]]
            if getattr(obj, elternfeld) != parent or obj.sort != zaehler[parent]:
                setattr(obj, elternfeld, parent)
                obj.sort = zaehler[parent]
                if isinstance(obj, KatplanElement):
                    obj.version += 1
                    obj.aktualisiert_von_id = user.id
                geaendert = True
    if geaendert:
        protokolliere(db, plan, "plan", "sortiert", user)
        markiere_ueberarbeitung(db, plan, user)


def verschiebe_nachbar(db: Session, plan: KatplanPlan, user: User,
                      obj: KatplanAbschnitt | KatplanElement, richtung: str) -> None:
    if richtung not in ("hoch", "runter"):
        raise ValueError("Richtung muss hoch oder runter sein.")
    geschwister: list[KatplanAbschnitt | KatplanElement] = []
    if isinstance(obj, KatplanElement):
        geschwister.extend(db.query(KatplanElement).filter_by(
            org_id=plan.org_id, plan_id=plan.id, abschnitt_id=obj.abschnitt_id,
        ).order_by(KatplanElement.sort, KatplanElement.id).all())
    else:
        geschwister.extend(db.query(KatplanAbschnitt).filter_by(
            org_id=plan.org_id, plan_id=plan.id, parent_id=obj.parent_id,
        ).order_by(KatplanAbschnitt.sort, KatplanAbschnitt.id).all())
    index = geschwister.index(obj)
    ziel = index + (-1 if richtung == "hoch" else 1)
    if 0 <= ziel < len(geschwister):
        nachbar = geschwister[ziel]
        obj.sort, nachbar.sort = nachbar.sort, obj.sort
        if obj.sort == nachbar.sort:
            # Auch Altbestände mit gleichen Sortwerten bleiben bedienbar.
            geschwister[index], geschwister[ziel] = nachbar, obj
            for nummer, eintrag in enumerate(geschwister, 1):
                eintrag.sort = nummer * 10
        for eintrag in (obj, nachbar):
            if isinstance(eintrag, KatplanElement):
                eintrag.version += 1
                eintrag.aktualisiert_von_id = user.id
        protokolliere(db, plan, "plan", "sortiert", user)
        markiere_ueberarbeitung(db, plan, user)
