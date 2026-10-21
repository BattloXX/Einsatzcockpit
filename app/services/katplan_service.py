"""Grundfunktionen fuer Suche, Kennungen und Aenderungsprotokolle."""

import json
import re
from collections.abc import Iterator
from datetime import date
from html.parser import HTMLParser
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.core.html_utils import sanitize_html
from app.models.katplan import (
    KATPLAN_GEFAHRENARTEN,
    KATPLAN_PLANARTEN,
    KatplanAbschnitt,
    KatplanAenderung,
    KatplanElement,
    KatplanLink,
    KatplanPhase,
    KatplanPlan,
    _utcnow,
)
from app.models.kontakt import Kontakt
from app.models.user import User
from app.services.katplan_typen import PLANARTEN
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
    from app.services.katplan_akteur_service import STANDARD_AKTEURE, schluessel_lesen

    keys = schluessel_lesen(element.akteure_json)
    labels = {s: b for s, b, _, _ in STANDARD_AKTEURE}
    db = object_session(element)
    if db is not None:
        from app.models.katplan import KatplanAkteur

        labels.update({a.schluessel: a.bezeichnung for a in db.query(KatplanAkteur).filter_by(org_id=element.org_id)})
    teile.extend(keys)
    teile.extend(labels.get(s, s) for s in keys)
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



def markiere_ueberarbeitung(db: Session, plan: KatplanPlan, user: User, *, quelle: str = "ui") -> None:
    """Inhaltsaenderungen schuetzen unabhaengig von der Kopfdaten-Version."""
    if plan.status == "freigegeben":
        protokolliere(db, plan, "plan", "geaendert", user, feld="status",
                      alt=plan.status, neu="in_ueberarbeitung", quelle=quelle)
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


def lege_element_an(db: Session, plan: KatplanPlan, user: User, *, typ: str, titel: str,
                    abschnitt_id: int | None = None, daten: dict | None = None,
                    beschreibung_html: str | None = None, herkunft: str = "manual",
                    quelle_dokument_id: int | None = None, quelle_seiten: str | None = None,
                    quelle: str = "ui") -> KatplanElement:
    """Gemeinsame Anlage fuer Schnellerfassung und Uebernahme einer Quellseite."""
    from app.services.katplan_typen import ELEMENT_TYPEN

    if plan.org_id != user.org_id or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    if typ not in ELEMENT_TYPEN:
        raise ValueError("Unbekannter Elementtyp.")
    geschwister = db.query(KatplanElement).filter_by(
        org_id=plan.org_id, plan_id=plan.id, abschnitt_id=abschnitt_id,
    ).all()
    element = KatplanElement(
        org_id=plan.org_id, plan_id=plan.id, typ=typ, titel=titel, abschnitt_id=abschnitt_id,
        kennung=naechste_kennung(db, plan, typ), sort=max((e.sort for e in geschwister), default=0) + 10,
        herkunft=herkunft, quelle_dokument_id=quelle_dokument_id, quelle_seiten=quelle_seiten,
        daten_json=json.dumps(daten or {}, ensure_ascii=False), tags_json="[]",
        beschreibung_html=bereinige_beschreibung(beschreibung_html),
        erstellt_von_id=user.id, aktualisiert_von_id=user.id,
    )
    aktualisiere_element_suchtext(element)
    db.add(element)
    db.flush()
    protokolliere(db, plan, "element", "angelegt", user, element_id=element.id, quelle=quelle)
    markiere_ueberarbeitung(db, plan, user, quelle=quelle)
    return element


def lege_abschnitt_an(db: Session, plan: KatplanPlan, user: User, *, titel: str,
                      parent_id: int | None = None, quelle: str = "ui") -> KatplanAbschnitt:
    """Gemeinsame Kapitelanlage fuer Editor und Import."""
    if plan.org_id != user.org_id or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    titel = titel.strip()
    if not titel or len(titel) > 200:
        raise ValueError("Bitte einen Titel mit höchstens 200 Zeichen eingeben.")
    if parent_id is not None and not db.query(KatplanAbschnitt).filter_by(
        id=parent_id, org_id=plan.org_id, plan_id=plan.id,
    ).first():
        raise ValueError("Unbekanntes Kapitel.")
    geschwister = db.query(KatplanAbschnitt).filter_by(
        org_id=plan.org_id, plan_id=plan.id, parent_id=parent_id,
    ).all()
    abschnitt = KatplanAbschnitt(org_id=plan.org_id, plan_id=plan.id, parent_id=parent_id,
                                titel=titel, sort=max((a.sort for a in geschwister), default=0) + 10)
    db.add(abschnitt)
    db.flush()
    protokolliere(db, plan, "abschnitt", "angelegt", user, neu=titel, quelle=quelle)
    markiere_ueberarbeitung(db, plan, user, quelle=quelle)
    return abschnitt


def lege_phase_an(db: Session, plan: KatplanPlan, user: User, *, name: str, quelle: str = "ui") -> KatplanPhase:
    if plan.org_id != user.org_id or plan.status == "archiviert" or not name.strip() or len(name) > 120:
        raise ValueError("Ungültige Phase oder archivierter Plan.")
    phase = KatplanPhase(org_id=plan.org_id, plan_id=plan.id, name=name.strip())
    db.add(phase)
    db.flush()
    protokolliere(db, plan, "phase", "angelegt", user, neu=phase.name, quelle=quelle)
    markiere_ueberarbeitung(db, plan, user, quelle=quelle)
    return phase


def validiere_element_felder(typ: str, *, titel: str, prioritaet: int, daten: dict,
                             tags: list[str], adresse: dict, beschreibung_html: str | None,
                             geometrie: str | None, geo_geprueft: bool = False,
                             verworfen: list[str] | None = None,
                             akteure: list[str] | None = None) -> dict[str, Any]:
    """Gemeinsame Feldregeln des Editors und der MCP-Entwurfswerkzeuge."""
    from app.services.katplan_geo_service import ableite_punkt, pruefe_geometrie
    from app.services.katplan_typen import bereinige_daten

    if not titel.strip() or len(titel.strip()) > 200:
        raise ValueError("Titel: Bitte 1 bis 200 Zeichen eingeben.")
    if not isinstance(daten, dict):
        raise ValueError("Daten müssen ein JSON-Objekt sein.")
    bereinigt = bereinige_daten(typ, daten, verworfen)
    if prioritaet not in (1, 2, 3):
        raise ValueError("Priorität muss 1, 2 oder 3 sein.")
    if len(tags) > 20 or any(len(t) > 50 for t in tags):
        raise ValueError("Höchstens 20 Tags mit je 50 Zeichen eingeben.")
    werte: dict[str, Any] = {
        "titel": titel.strip(), "prioritaet": prioritaet,
        "daten_json": json.dumps(bereinigt, ensure_ascii=False),
        "tags_json": json.dumps(tags, ensure_ascii=False),
        "beschreibung_html": bereinige_beschreibung(beschreibung_html),
    }
    if akteure is not None:
        werte["akteure_json"] = json.dumps(akteure, ensure_ascii=False) if akteure else None
    for feld, maximum in (("strasse", 200), ("hausnummer", 20), ("plz", 10), ("ort", 100)):
        wert = adresse.get(feld, "").strip()
        if len(wert) > maximum:
            raise ValueError(f"{feld}: höchstens {maximum} Zeichen.")
        werte[feld] = wert or None
    geom = pruefe_geometrie(typ, geometrie)
    lat, lng = ableite_punkt(geom) if geom else (None, None)
    werte.update(geometry_json=json.dumps(geom, sort_keys=True) if geom else None,
                 lat=lat, lng=lng,
                 geo_status=("geprueft" if geo_geprueft else "ungeprueft") if geom else "keine")
    return werte


def pruefverantwortliche(db: Session, org_id: int | None) -> list[User]:
    return [u for u in db.query(User).filter_by(org_id=org_id, active=True).order_by(User.display_name).all()
            if u.role_codes & {"katplan_verwalter", "katplan_freigeber", "org_admin", "admin"}]


def validiere_stammdaten(*, titel: str, gefahrenarten: list[str], beschreibung_html: str = "",
                         geltungsbereich: str = "", verantwortliche_stelle: str = "",
                         wiedervorlage_am: str = "", planart: str = "katastrophenplan",
                         pruefintervall_monate: str | int | None = None,
                         pruef_verantwortlich_user_id: str | int | None = None) -> dict:
    """Gemeinsame Formularvalidierung; Fehlerprioritaet der Oberflaeche beibehalten."""
    try:
        intervall = int(pruefintervall_monate) if pruefintervall_monate not in (None, "") else None
        person = int(pruef_verantwortlich_user_id) if pruef_verantwortlich_user_id not in (None, "") else None
    except (ValueError, TypeError):
        raise ValueError("Bitte gültige Prüfdaten eingeben.") from None
    if intervall is not None and (isinstance(pruefintervall_monate, bool)
                                  or str(intervall) != str(pruefintervall_monate) or not 1 <= intervall <= 60):
        raise ValueError("Das Prüfintervall muss zwischen 1 und 60 Monaten liegen.")
    fehler = None
    termin = None
    try:
        termin = date.fromisoformat(wiedervorlage_am) if wiedervorlage_am else None
    except ValueError:
        fehler = "Bitte ein gültiges Wiedervorlagedatum eingeben."
    if not titel.strip() or len(titel.strip()) > 200:
        fehler = "Bitte einen Titel mit höchstens 200 Zeichen eingeben."
    if (planart == "katastrophenplan" and not gefahrenarten
            or any(code not in KATPLAN_GEFAHRENARTEN for code in gefahrenarten)):
        fehler = "Bitte mindestens eine gültige Gefahrenart auswählen."
    if len(geltungsbereich) > 300 or len(verantwortliche_stelle) > 200:
        fehler = "Geltungsbereich (300) oder verantwortliche Stelle (200) ist zu lang."
    if planart not in KATPLAN_PLANARTEN:
        fehler = "Bitte eine gültige Planart wählen."
    if fehler:
        raise ValueError(fehler)
    return {"planart": planart, "titel": titel.strip(), "gefahrenarten_json": json.dumps(sorted(set(gefahrenarten))),
            "beschreibung_html": bereinige_beschreibung(beschreibung_html),
            "geltungsbereich": geltungsbereich.strip() or None,
            "verantwortliche_stelle": verantwortliche_stelle.strip() or None,
            "wiedervorlage_am": termin, "pruefintervall_monate": intervall,
            "pruef_verantwortlich_user_id": person}


def plan_anlegen(db: Session, user: User, *, werte: dict, quelle: str = "ui") -> KatplanPlan:
    """Validierte Stammdaten und die Vorlage der Planart ohne Commit anlegen."""
    person = werte.get("pruef_verantwortlich_user_id")
    if person is not None and person not in {u.id for u in pruefverantwortliche(db, user.org_id)}:
        raise ValueError("Benutzer nicht verfügbar.")
    kontakt_id = werte.get("verantwortlich_kontakt_id")
    if kontakt_id is not None and not db.query(Kontakt).filter_by(org_id=user.org_id, id=kontakt_id).first():
        raise ValueError("Kontakt nicht verfügbar.")
    werte = {**werte, "pruef_verantwortlich_user_id": person if person is not None else user.id}
    plan = KatplanPlan(org_id=user.org_id, erstellt_von_id=user.id, aktualisiert_von_id=user.id, **werte)
    db.add(plan)
    db.flush()
    protokolliere(db, plan, "plan", "angelegt", user, quelle=quelle)
    vorlage = PLANARTEN[plan.planart]
    for titel in vorlage.kapitel:
        lege_abschnitt_an(db, plan, user, titel=titel, quelle=quelle)
    for sort, (name, farbe) in enumerate(vorlage.phasen, 1):
        phase = lege_phase_an(db, plan, user, name=name, quelle=quelle)
        phase.farbe, phase.sort = farbe, sort * 10
    return plan


def stammdaten_aendern(db: Session, plan: KatplanPlan, user: User, *, version: int,
                      werte: dict, quelle: str = "ui") -> list[str]:
    """Validierte Stammdaten mit Versionsschutz und Feldprotokoll schreiben."""
    pruefe_version(plan, version)
    if plan.org_id != user.org_id or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")
    erlaubt = {"planart", "titel", "gefahrenarten_json", "beschreibung_html", "geltungsbereich",
               "verantwortliche_stelle", "wiedervorlage_am", "verantwortlich_kontakt_id",
               "pruefintervall_monate", "pruef_verantwortlich_user_id"}
    if set(werte) - erlaubt:
        raise ValueError("Nicht erlaubte Stammdatenfelder.")
    kontakt_id = werte.get("verantwortlich_kontakt_id")
    if kontakt_id is not None and not db.query(Kontakt).filter_by(org_id=plan.org_id, id=kontakt_id).first():
        raise ValueError("Kontakt nicht verfügbar.")
    person = werte.get("pruef_verantwortlich_user_id")
    if person is not None and person not in {u.id for u in pruefverantwortliche(db, plan.org_id)}:
        raise ValueError("Benutzer nicht verfügbar.")
    intervall = werte.get("pruefintervall_monate")
    if intervall is not None and (type(intervall) is not int or not 1 <= intervall <= 60):
        raise ValueError("Das Prüfintervall muss zwischen 1 und 60 Monaten liegen.")
    geaendert = []
    for feld, wert in werte.items():
        alt = getattr(plan, feld)
        if alt != wert:
            if feld == "wiedervorlage_am":
                plan.wiedervorlage_erinnert_am = None
                plan.wiedervorlage_vorab_erinnert_am = None
                plan.wiedervorlage_eskaliert_am = None
            setattr(plan, feld, wert)
            geaendert.append(feld)
            protokolliere(db, plan, "plan", "geaendert", user, feld=feld,
                          alt=str(alt) if alt is not None else None,
                          neu=str(wert) if wert is not None else None, quelle=quelle)
    if set(geaendert) - {"wiedervorlage_am", "pruefintervall_monate", "pruef_verantwortlich_user_id"}:
        markiere_ueberarbeitung(db, plan, user, quelle=quelle)
    plan.version += 1
    plan.aktualisiert_am, plan.aktualisiert_von_id = _utcnow(), user.id
    return geaendert
