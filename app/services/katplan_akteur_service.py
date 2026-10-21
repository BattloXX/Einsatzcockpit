"""Tenantgebundener Akteurskatalog und gemeinsame Zuordnungsregeln."""

import json
import re
import unicodedata
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.katplan import KatplanAkteur, KatplanElement

FARBEN = ("gruen", "gelb", "orange", "rot", "blau", "grau")
STANDARD_AKTEURE = (
    ("feuerwehr", "Feuerwehr", "rot", "flame"),
    ("netzbetreiber", "Netzbetreiber", "gelb", "bolt"),
    ("behoerde", "Behörde", "blau", "building"),
    ("polizei", "Polizei", "blau", "shield"),
    ("rettung", "Rettungsdienst", "rot", "ambulance"),
    ("gemeinde", "Gemeinde/Bauhof", "gruen", "home"),
    ("sonstige", "Sonstige", "grau", None),
)
_ICON_DATEI = Path(__file__).resolve().parents[1] / "static/icons/tabler-auswahl.txt"
ICONS = frozenset(z.strip() for z in _ICON_DATEI.read_text().splitlines() if z and not z.startswith("#"))


def schluessel_lesen(roh: str | None) -> list[str]:
    try:
        daten = json.loads(roh or "[]")
    except (ValueError, TypeError):
        return []
    return [s for s in daten if isinstance(s, str)] if isinstance(daten, list) else []


def akteure(db: Session, org_id: int | None, nur_aktive: bool = True) -> list[KatplanAkteur]:
    from app.models.master import FireDept

    def _laden() -> list[KatplanAkteur]:
        return db.query(KatplanAkteur).filter_by(org_id=org_id).order_by(
            KatplanAkteur.sort, KatplanAkteur.bezeichnung).all()

    katalog = _laden()
    if not katalog and org_id is not None:
        # Nur beim Erstzugriff den Organisationsdatensatz sperren: parallele Anlage serialisieren.
        db.query(FireDept).filter_by(id=org_id).with_for_update().first()
        katalog = _laden()
    if not katalog:
        katalog = [KatplanAkteur(org_id=org_id, schluessel=s, bezeichnung=b, farbe=f, icon_name=i,
                                sort=n * 10, aktiv=True)
                   for n, (s, b, f, i) in enumerate(STANDARD_AKTEURE, 1)]
        db.add_all(katalog)
        db.flush(katalog)
    return [a for a in katalog if a.aktiv or not nur_aktive]


def akteur_speichern(db: Session, org_id: int | None, schluessel: str | None,
                     bezeichnung: str, farbe: str, icon_name: str | None,
                     aktiv: bool, sort: int | None = None) -> KatplanAkteur:
    bezeichnung = bezeichnung.strip()
    icon_name = (icon_name or "").strip() or None
    if not 1 <= len(bezeichnung) <= 80:
        raise ValueError("Bezeichnung: Bitte 1 bis 80 Zeichen eingeben.")
    if farbe not in FARBEN:
        raise ValueError("Ungültige Farbe.")
    if icon_name is not None and icon_name not in ICONS:
        raise ValueError("Unbekanntes Icon.")
    katalog = akteure(db, org_id, nur_aktive=False)
    if schluessel is not None:
        akteur = next((a for a in katalog if a.schluessel == schluessel), None)
        if akteur is None:
            raise ValueError(f"Unbekannter Akteur: {schluessel}")
    else:
        ascii_name = unicodedata.normalize("NFKD", bezeichnung).encode("ascii", "ignore").decode()
        basis = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:36] or "akteur"
        neu = basis
        nummer = 2
        while neu in {a.schluessel for a in katalog}:
            neu = f"{basis}-{nummer}"
            nummer += 1
        akteur = KatplanAkteur(org_id=org_id, schluessel=neu, sort=max((a.sort for a in katalog), default=0) + 10)
        db.add(akteur)
    akteur.bezeichnung, akteur.farbe, akteur.icon_name, akteur.aktiv = bezeichnung, farbe, icon_name, aktiv
    if sort is not None:
        akteur.sort = sort
    db.flush()
    return akteur


def akteur_loeschen(db: Session, org_id: int | None, schluessel: str) -> None:
    akteur = db.query(KatplanAkteur).filter_by(org_id=org_id, schluessel=schluessel).one_or_none()
    if akteur is None:
        raise ValueError(f"Unbekannter Akteur: {schluessel}")
    for e in db.query(KatplanElement).filter(
        KatplanElement.org_id == org_id, KatplanElement.akteure_json.contains(schluessel, autoescape=True),
    ):
        if schluessel in schluessel_lesen(e.akteure_json):
            raise ValueError("Dieser Akteur wird verwendet. Bitte stattdessen deaktivieren.")
    db.delete(akteur)
    db.flush()


def validiere_akteure(db: Session, org_id: int | None, schluessel: list[str]) -> list[str]:
    if not isinstance(schluessel, list) or any(not isinstance(s, str) for s in schluessel):
        raise ValueError("Akteure müssen eine Liste von Schlüsseln sein.")
    eindeutig = list(dict.fromkeys(schluessel))
    if len(eindeutig) > 10:
        raise ValueError("Höchstens 10 Akteure auswählen.")
    aktiv = {a.schluessel for a in akteure(db, org_id)}
    for s in eindeutig:
        if s not in aktiv:
            raise ValueError(f"Unbekannter Akteur: {s}")
    return eindeutig


def katalog_daten(db: Session, org_id: int | None, verwendet: set[str]) -> list[dict]:
    return [{f: getattr(a, f) for f in ("schluessel", "bezeichnung", "farbe", "icon_name", "sort")}
            for a in akteure(db, org_id, nur_aktive=False) if a.aktiv or a.schluessel in verwendet]


def anzeige_kontext(katalog: list[dict], elemente: list) -> dict:
    nach_key = {a["schluessel"]: a for a in katalog}
    element_akteure = {e.id: [nach_key.get(s, {"schluessel": s, "bezeichnung": s, "farbe": "grau", "icon_name": None})
                            for s in schluessel_lesen(e.akteure_json)] for e in elemente}
    verwendet = {a["schluessel"] for werte in element_akteure.values() for a in werte}
    vorkommend = [a for a in katalog if a["schluessel"] in verwendet]
    vorkommend += [dict(schluessel=s, bezeichnung=s, farbe="grau", icon_name=None)
                   for s in sorted(verwendet - nach_key.keys())]
    gruppen: list[dict] = [
        {"akteur": a, "elemente": [e for e in elemente if a["schluessel"] in schluessel_lesen(e.akteure_json)]}
        for a in vorkommend]
    gruppen.append({"akteur": None, "elemente": [e for e in elemente if not element_akteure[e.id]]})
    return {"element_akteure": element_akteure, "vorkommende_akteure": vorkommend, "nach_akteur": gruppen}
