"""Einheitliche, schreibfreie Kontaktauswahl für Vorschau, PDF und MCP."""

from __future__ import annotations

import base64
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.kontakt import Kontakt, KontaktDruckprofil, KontaktKategorie
from app.models.master import FireDept, OrgSettings
from app.models.objekt import ObjektKontakt
from app.services.kontakt_service import _mit_details

KATEGORIEN = [
    "Feuerwehr & Einsatzorganisationen",
    "Gemeinde & Behörden",
    "Infrastruktur & Versorger",
    "Dienstleister & Unterstützung",
    "Wichtige Einrichtungen",
    "Feuerwehr intern",
    "Objektkontakte",
    "Sonstige",
]
KATEGORIE_KEYS = [
    "einsatz",
    "behoerden",
    "infrastruktur",
    "dienstleister",
    "einrichtungen",
    "intern",
    "objekt",
    "sonstige",
]
DruckFeld = Literal["organisation", "funktion", "telefone", "email", "erreichbarkeit", "druckhinweis"]
FELDER: dict[DruckFeld, str] = {
    "organisation": "Organisation",
    "funktion": "Funktion",
    "telefone": "Telefonnummern",
    "email": "E-Mail",
    "erreichbarkeit": "Erreichbarkeit",
    "druckhinweis": "Druckhinweis",
}


def _standardfelder() -> list[DruckFeld]:
    return ["organisation", "funktion", "telefone", "erreichbarkeit", "druckhinweis"]


class DruckEinstellungen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kategorien: list[int] | None = None  # None = Standardkategorien, [] = alle
    nur_aktiv: bool = True
    nur_freigegeben: bool = True
    nur_einsatzrelevant: bool = False
    objektkontakte_ausschliessen: bool = True
    ohne_telefon_ausschliessen: bool = False
    typ: Literal["all", "person", "stelle"] = "all"
    organisation: str = Field(default="", max_length=200)
    funktion: str = Field(default="", max_length=150)
    prioritaet: Literal["", "hoch", "normal", "niedrig"] = ""
    q: str = Field(default="", max_length=200)
    layout: Literal["kompakt", "detail"] = "kompakt"
    papier: Literal["A4", "A3"] = "A4"
    ausrichtung: Literal["auto", "portrait", "landscape"] = "auto"
    sortierung: Literal["prioritaet", "name", "organisation", "manuell"] = "prioritaet"
    logo_anzeigen: bool = True
    gruppieren: bool = True
    felder: list[DruckFeld] = Field(default_factory=_standardfelder)


_LOGO_DIR = Path(__file__).resolve().parents[1] / "static" / "img" / "uploads"
_LOGO_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def organisationslogo_data_uri(db: Session, org: FireDept) -> str:
    """Betten nur das eigene hochgeladene Organisationslogo ein, ohne externe Abrufe."""
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    candidates = [org_settings.logo_path if org_settings else None, org.logo_path]
    for candidate in candidates:
        if not candidate or not candidate.startswith("/static/img/uploads/"):
            continue
        filename = candidate.removeprefix("/static/img/uploads/")
        suffix = Path(filename).suffix.lower()
        if suffix not in _LOGO_MIMES or filename != f"logo_org{org.id}{suffix}":
            continue
        try:
            root = _LOGO_DIR.resolve()
            path = (root / filename).resolve()
            if path.parent != root or path.name != filename:
                continue
            with path.open("rb") as logo_file:
                data = logo_file.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                continue
            if suffix == ".svg":
                # SVG-Logos bleiben Bilder; Dokumenttypen und aktive Inhalte sind ausgeschlossen.
                if b"<!doctype" in data.lower() or b"<!entity" in data.lower():
                    continue
                svg = ElementTree.fromstring(data)
                if svg.tag != "{http://www.w3.org/2000/svg}svg" and svg.tag != "svg":
                    continue
                if any(node.tag.rsplit("}", 1)[-1].lower() in {"script", "foreignobject"} for node in svg.iter()):
                    continue
            else:
                from PIL import Image

                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
            return f"data:{_LOGO_MIMES[suffix]};base64," + base64.b64encode(data).decode("ascii")
        except OSError, ValueError, ElementTree.ParseError:
            continue
    return ""


def kategorien(db: Session, org_id: int) -> list[KontaktKategorie]:
    return (
        db.query(KontaktKategorie)
        .filter(KontaktKategorie.org_id == org_id)
        .order_by(KontaktKategorie.sortierung, KontaktKategorie.name, KontaktKategorie.id)
        .all()
    )


def standardkategorien_anlegen(db: Session, org_id: int) -> None:
    """Explizite Einrichtung für neue Mandanten; Druckabrufe schreiben niemals."""
    vorhanden = kategorien(db, org_id)
    for index, name in enumerate(KATEGORIEN):
        key = KATEGORIE_KEYS[index]
        if any(k.schluessel == key for k in vorhanden):
            continue
        kat = next((k for k in vorhanden if k.name == name), None)
        if kat is None:
            kat = KontaktKategorie(
                org_id=org_id, name=name, sortierung=index * 10, druck_standard=index < 3, objektkontakte=index == 6
            )
            db.add(kat)
        kat.schluessel = key
    db.flush()


def kategorie_speichern(
    db: Session,
    org_id: int,
    *,
    name: str,
    kategorie_id: int | None = None,
    aktiv: bool | None = None,
    sortierung: int | None = None,
    druck_standard: bool | None = None,
    objektkontakte: bool | None = None,
) -> KontaktKategorie:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Kategoriename muss 1 bis 100 Zeichen enthalten.")
    kat = db.query(KontaktKategorie).filter_by(org_id=org_id, id=kategorie_id).first() if kategorie_id else None
    if kategorie_id and kat is None:
        raise ValueError("Kategorie nicht gefunden.")
    duplicate = db.query(KontaktKategorie).filter_by(org_id=org_id, name=name).first()
    if duplicate and duplicate is not kat:
        raise ValueError("Kategorie existiert bereits.")
    if kat is None:
        kat = KontaktKategorie(org_id=org_id)
        db.add(kat)
    kat.name = name
    for field, value in (
        ("aktiv", aktiv),
        ("sortierung", sortierung),
        ("druck_standard", druck_standard),
        ("objektkontakte", objektkontakte),
    ):
        if value is not None:
            setattr(kat, field, value)
    db.flush()
    return kat


def profile(db: Session, org_id: int, user_id: int) -> list[dict]:
    cats = kategorien(db, org_id)

    def ids(keys):
        return [k.id for k in cats if k.aktiv and k.schluessel in keys]

    defaults = [
        ("einsatz", "Einsatztelefonliste", DruckEinstellungen()),
        (
            "gsl",
            "GSL-Kontaktverzeichnis",
            DruckEinstellungen(kategorien=ids(KATEGORIE_KEYS[:4]) or [-1], layout="detail", felder=list(FELDER)),
        ),
        ("intern", "Feuerwehr intern", DruckEinstellungen(kategorien=ids(["intern"]) or [-1])),
        ("dienstleister", "Dienstleister", DruckEinstellungen(kategorien=ids(["dienstleister"]) or [-1])),
        (
            "vollstaendig",
            "Vollständiges Kontaktverzeichnis",
            DruckEinstellungen(
                kategorien=[],
                nur_freigegeben=False,
                objektkontakte_ausschliessen=False,
                layout="detail",
                felder=list(FELDER),
            ),
        ),
    ]
    result = [
        {"id": key, "name": name, "einstellungen": opts.model_dump(), "persoenlich": False}
        for key, name, opts in defaults
    ]
    result.extend(
        {"id": str(p.id), "name": p.name, "einstellungen": json.loads(p.einstellungen_json), "persoenlich": True}
        for p in db.query(KontaktDruckprofil)
        .filter_by(org_id=org_id, user_id=user_id)
        .order_by(KontaktDruckprofil.name)
    )
    return result


def profil_laden(db: Session, org_id: int, user_id: int, profil_id: str) -> DruckEinstellungen:
    for profil in profile(db, org_id, user_id):
        if profil["id"] == profil_id:
            if profil["einstellungen"]["kategorien"] == [-1]:
                raise ValueError(
                    "Für dieses Profil ist keine aktive Standardkategorie eingerichtet. Bitte Kategorien verwalten."
                )
            return DruckEinstellungen.model_validate(profil["einstellungen"])
    raise ValueError("Druckprofil nicht gefunden.")


def profil_speichern(
    db: Session, org_id: int, user_id: int, name: str, einstellungen: DruckEinstellungen
) -> KontaktDruckprofil:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Profilname muss 1 bis 100 Zeichen enthalten.")
    profil = db.query(KontaktDruckprofil).filter_by(org_id=org_id, user_id=user_id, name=name).first()
    if profil is None:
        profil = KontaktDruckprofil(org_id=org_id, user_id=user_id, name=name)
        db.add(profil)
    profil.einstellungen_json = einstellungen.model_dump_json()
    db.flush()
    return profil


def profil_loeschen(db: Session, org_id: int, user_id: int, profil_id: int) -> None:
    profil = db.query(KontaktDruckprofil).filter_by(org_id=org_id, user_id=user_id, id=profil_id).first()
    if profil is None:
        raise ValueError("Druckprofil nicht gefunden.")
    db.delete(profil)
    db.flush()


def _gueltig(zuordnung) -> bool:
    today = datetime.now(ZoneInfo("Europe/Vienna")).date()
    return bool(
        zuordnung.aktiv
        and (not zuordnung.gueltig_ab or zuordnung.gueltig_ab <= today)
        and (not zuordnung.gueltig_bis or zuordnung.gueltig_bis >= today)
    )


def auswahl(db: Session, org_id: int, opts: DruckEinstellungen) -> dict:
    cats = kategorien(db, org_id)
    selected = (
        {k.id for k in cats if k.aktiv and k.druck_standard and not k.objektkontakte}
        if opts.kategorien is None
        else set(opts.kategorien)
    )
    allowed = {k.id for k in cats if k.aktiv}
    if selected - allowed:
        raise ValueError("Eine ausgewählte Kategorie ist inaktiv oder nicht verfügbar.")
    # Leere Standardauswahl darf niemals versehentlich alle Kontakte freigeben.
    category_filter = opts.kategorien is None or bool(selected)
    query = _mit_details(db.query(Kontakt)).filter(Kontakt.org_id == org_id, Kontakt.archiviert.is_(False))
    if opts.nur_aktiv:
        today = datetime.now(ZoneInfo("Europe/Vienna")).date()
        query = query.filter(
            Kontakt.aktiv.is_(True),
            or_(Kontakt.gueltig_ab.is_(None), Kontakt.gueltig_ab <= today),
            or_(Kontakt.gueltig_bis.is_(None), Kontakt.gueltig_bis >= today),
        )
    if opts.nur_freigegeben:
        query = query.filter(Kontakt.druck_freigegeben.is_(True))
    if opts.nur_einsatzrelevant:
        query = query.filter(Kontakt.einsatzrelevant.is_(True))
    if opts.typ != "all":
        query = query.filter(Kontakt.typ == opts.typ)
    if opts.prioritaet:
        query = query.filter(Kontakt.druck_prioritaet == opts.prioritaet)
    objekt_ids = set()
    if opts.objektkontakte_ausschliessen:
        objekt_ids = {
            row[0]
            for row in db.query(ObjektKontakt.kontakt_id)
            .filter(ObjektKontakt.org_id == org_id, ObjektKontakt.kontakt_id.is_not(None))
            .all()
        }
    rows = []
    for kontakt in query.all():
        zuordnungen = [z for z in kontakt.kategorien if z.org_id == org_id and z.kategorie.org_id == org_id]
        if opts.objektkontakte_ausschliessen and (
            kontakt.id in objekt_ids or any(z.kategorie.objektkontakte for z in zuordnungen)
        ):
            continue
        matches = [
            z.kategorie
            for z in zuordnungen
            if z.kategorie.aktiv and (not category_filter or z.kategorie_id in selected)
        ]
        if category_filter and not matches:
            continue
        functions = [
            f
            for f in kontakt.organisations_funktionen
            if f.org_id == org_id
            and _gueltig(f)
            and f.organisation
            and f.organisation.org_id == org_id
            and f.organisation.aktiv
        ]
        orgs = list(dict.fromkeys(filter(None, [kontakt.organisation] + [f.organisation.name for f in functions])))
        roles = list(dict.fromkeys(filter(None, [kontakt.funktion] + [f.funktion for f in functions])))
        if (
            opts.organisation.casefold() not in " · ".join(orgs).casefold()
            or opts.funktion.casefold() not in " · ".join(roles).casefold()
        ):
            continue
        # Keine Privatnummern, unabhängig von Filter-/Profilwahl. Nur freigegebene berufliche E-Mails.
        phones = [
            {"nummer": p.nummer, "label": p.label or "", "normalisiert": p.nummer_normalisiert}
            for p in kontakt.telefone
            if p.org_id == org_id and p.aktiv and p.verwendung == "dienst"
        ]
        emails = [
            e.email
            for e in kontakt.email_adressen
            if e.org_id == org_id and e.aktiv and e.typ in ("dienst", "dienstlich", "geschaeftlich", "geschäftlich")
        ]
        # Legacy-E-Mail hat keine Datenschutzklassifikation: nur nach ausdrücklicher Kontakt-Druckfreigabe.
        if not kontakt.email_adressen and kontakt.email and kontakt.druck_freigegeben:
            emails = [kontakt.email]
        search = " ".join(
            [
                kontakt.anzeigename,
                *orgs,
                *roles,
                *emails,
                kontakt.erreichbarkeit or "",
                kontakt.druckhinweis or "",
                *[p["nummer"] + " " + p["normalisiert"] for p in phones],
            ]
        )
        if opts.q.strip().casefold() not in search.casefold():
            continue
        if opts.ohne_telefon_ausschliessen and not phones:
            continue
        group = min(matches, key=lambda k: (k.sortierung, k.name.casefold(), k.id)) if matches else None
        rows.append(
            {
                "id": kontakt.id,
                "name": kontakt.anzeigename,
                "organisation": " · ".join(orgs),
                "funktion": " · ".join(roles),
                "telefone": phones,
                "email": " · ".join(emails),
                "erreichbarkeit": kontakt.erreichbarkeit or "",
                "druckhinweis": kontakt.druckhinweis or "",
                "prioritaet": kontakt.druck_prioritaet,
                "sortierung": kontakt.druck_sortierung,
                "kategorie": group.name if group else "Ohne aktive Kategorie",
                "kategorie_sort": (group.sortierung, group.name.casefold(), group.id) if group else (2147483647, "", 0),
                "aktiv": _gueltig(kontakt),
                "ohne_kategorie": not zuordnungen,
            }
        )
    priorities = {"hoch": 0, "normal": 1, "niedrig": 2}

    def sortkey(row):
        group = row["kategorie_sort"] if opts.gruppieren else ()
        value = {
            "prioritaet": (priorities.get(row["prioritaet"], 1), row["sortierung"]),
            "manuell": (row["sortierung"],),
            "organisation": (row["organisation"].casefold(),),
            "name": (),
        }[opts.sortierung]
        return group, value, row["name"].casefold(), row["id"]

    rows.sort(key=sortkey)
    names = Counter(r["name"].casefold() for r in rows)
    numbers = Counter(n for r in rows for n in {p["normalisiert"] for p in r["telefone"] if p["normalisiert"]})
    warnings = []
    for row in rows:
        issues = []
        if not row["telefone"]:
            issues.append("Keine druckbare Telefonnummer")
        if not row["aktiv"]:
            issues.append("Inaktiv oder außerhalb der Gültigkeit")
        if row["ohne_kategorie"]:
            issues.append("Ohne Kategorie")
        if not row["organisation"]:
            issues.append("Ohne Organisationszuordnung")
        if names[row["name"].casefold()] > 1 or any(
            numbers[p["normalisiert"]] > 1 for p in row["telefone"] if p["normalisiert"]
        ):
            issues.append("Mögliche Dublette")
        if issues:
            warnings.append({"id": row["id"], "name": row["name"], "hinweise": issues})
    return {"kontakte": rows, "anzahl": len(rows), "hinweise": warnings}
