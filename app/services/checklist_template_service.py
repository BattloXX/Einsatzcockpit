"""Fachlogik fuer versionierte Checklisten-Vorlagen."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.probenplanung import (
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateSection,
    ChecklistTemplateVersion,
    Probeart,
)

STANDARD_TEMPLATE_NAME = "Vollprobe Standard"

# Titel gemaess Konzept Abschnitt 11. Die Typisierung bildet die fachlich naheliegende
# Eingabeart ab und kann nach dem Import wie jede andere Entwurfsversion angepasst werden.
STANDARD_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Stammdaten",
        (("Ausführende / Organisatoren", "person"), ("Termin", "datum"), ("Ort", "text"), ("Objekt", "text")),
    ),
    (
        "Abstimmungen",
        (
            ("mit Eigentümer vereinbart", "checkbox"),
            ("Datum der Vereinbarung", "datum"),
            ("Bewohner verständigt", "checkbox"),
            ("Datum", "datum"),
            ("Sondervereinbarungen", "langtext"),
        ),
    ),
    (
        "Darsteller / Verletzte",
        (
            ("Feuerwehrjugend eingeplant", "checkbox"),
            ("Jugendleiter verständigt", "checkbox"),
            ("externe Personen eingeplant", "checkbox"),
            ("Kantine verständigt", "checkbox"),
            ("Verletzte gekennzeichnet", "checkbox"),
            ("Anzahl Verletzte / Darsteller", "text"),
            ("Bemerkungen", "langtext"),
        ),
    ),
    (
        "Hilfsmittel",
        (
            ("Fahnen", "checkbox"),
            ("Blitzleuchte", "checkbox"),
            ("Nebelmaschine", "checkbox"),
            ("Farbnebel", "checkbox"),
            ("weitere Hilfsmittel", "langtext"),
        ),
    ),
    (
        "Unterlagen",
        (
            ("Brandschutzplan vorhanden", "checkbox"),
            ("Objektplan vorhanden", "checkbox"),
            ("Übungsskizze vorhanden", "checkbox"),
            ("sonstige Unterlagen", "langtext"),
        ),
    ),
    (
        "Aufgabenplanung",
        (
            ("Einsatzleiter", "person"),
            ("Fahrzeug-/Gruppenaufgaben", "langtext"),
            ("besondere Aufgaben", "langtext"),
            ("Aufgabenvorstellung durchgeführt", "checkbox"),
        ),
    ),
    (
        "Information",
        (
            ("Dienstgrade informiert", "checkbox"),
            ("Alarmtext vorbereitet", "checkbox"),
            ("besondere Gefahren dokumentiert", "checkbox"),
            ("gefährliche Stoffe", "langtext"),
            ("verschlossene Türen", "text"),
            ("Schlüssel", "text"),
            ("geplante Lageänderungen", "langtext"),
            ("sonstige Besonderheiten", "langtext"),
        ),
    ),
    ("Lage / Skizze", (("Übungsskizze", "bild"), ("Lageplan", "datei"), ("Bild oder Plan", "datei"))),
)


def aktive_version(db: Session, template: ChecklistTemplate) -> ChecklistTemplateVersion | None:
    if template.aktive_version_id is None:
        return None
    return (
        db.query(ChecklistTemplateVersion)
        .filter(
            ChecklistTemplateVersion.id == template.aktive_version_id,
            ChecklistTemplateVersion.template_id == template.id,
            ChecklistTemplateVersion.org_id == template.org_id,
        )
        .first()
    )


def require_entwurf(version: ChecklistTemplateVersion) -> None:
    if version.veroeffentlicht_am is not None:
        raise HTTPException(409, "Veröffentlichte Versionen sind unveränderlich")


def neue_version(db: Session, template: ChecklistTemplate, user_id: int | None) -> ChecklistTemplateVersion:
    source = aktive_version(db, template)
    max_version = (
        db.query(func.max(ChecklistTemplateVersion.version))
        .filter(
            ChecklistTemplateVersion.template_id == template.id,
            ChecklistTemplateVersion.org_id == template.org_id,
        )
        .scalar()
        or 0
    )
    version = ChecklistTemplateVersion(
        org_id=template.org_id, template_id=template.id, version=max_version + 1, erstellt_von=user_id
    )
    db.add(version)
    db.flush()
    if source:
        sections = (
            db.query(ChecklistTemplateSection)
            .filter(
                ChecklistTemplateSection.version_id == source.id,
                ChecklistTemplateSection.org_id == template.org_id,
            )
            .order_by(ChecklistTemplateSection.sortierung, ChecklistTemplateSection.id)
            .all()
        )
        for section in sections:
            clone_section = ChecklistTemplateSection(
                org_id=template.org_id,
                version_id=version.id,
                titel=section.titel,
                beschreibung=section.beschreibung,
                sortierung=section.sortierung,
            )
            db.add(clone_section)
            db.flush()
            items = (
                db.query(ChecklistTemplateItem)
                .filter(
                    ChecklistTemplateItem.section_id == section.id,
                    ChecklistTemplateItem.org_id == template.org_id,
                )
                .order_by(ChecklistTemplateItem.sortierung, ChecklistTemplateItem.id)
                .all()
            )
            for item in items:
                db.add(
                    ChecklistTemplateItem(
                        org_id=template.org_id,
                        section_id=clone_section.id,
                        titel=item.titel,
                        hilfetext=item.hilfetext,
                        typ=item.typ,
                        optionen=item.optionen,
                        pflicht=item.pflicht,
                        sortierung=item.sortierung,
                        default_verantwortlich_member_id=item.default_verantwortlich_member_id,
                        faellig_tage_vorher=item.faellig_tage_vorher,
                    )
                )
    return version


def veroeffentlichen(db: Session, template: ChecklistTemplate, version: ChecklistTemplateVersion) -> None:
    require_entwurf(version)
    item_count = (
        db.query(ChecklistTemplateItem)
        .join(ChecklistTemplateSection, ChecklistTemplateItem.section_id == ChecklistTemplateSection.id)
        .filter(
            ChecklistTemplateSection.version_id == version.id,
            ChecklistTemplateSection.org_id == template.org_id,
            ChecklistTemplateItem.org_id == template.org_id,
        )
        .count()
    )
    if item_count == 0:
        raise HTTPException(409, "Eine Version ohne Punkte kann nicht veröffentlicht werden")
    version.veroeffentlicht_am = datetime.now(UTC)
    template.aktive_version_id = version.id


def standardvorlage_importieren(db: Session, org_id: int, user_id: int | None) -> tuple[ChecklistTemplate, bool]:
    existing = (
        db.query(ChecklistTemplate)
        .filter(ChecklistTemplate.org_id == org_id, ChecklistTemplate.name == STANDARD_TEMPLATE_NAME)
        .first()
    )
    if existing:
        template = existing
        created = False
    else:
        template = ChecklistTemplate(
            org_id=org_id,
            name=STANDARD_TEMPLATE_NAME,
            beschreibung="Standard-Checkliste für Vollproben gemäß Konzept Abschnitt 11.",
            erstellt_von=user_id,
        )
        db.add(template)
        db.flush()
        version = ChecklistTemplateVersion(org_id=org_id, template_id=template.id, version=1, erstellt_von=user_id)
        db.add(version)
        db.flush()
        for section_index, (title, items) in enumerate(STANDARD_SECTIONS):
            section = ChecklistTemplateSection(
                org_id=org_id, version_id=version.id, titel=title, sortierung=section_index
            )
            db.add(section)
            db.flush()
            for item_index, (item_title, item_type) in enumerate(items):
                db.add(
                    ChecklistTemplateItem(
                        org_id=org_id,
                        section_id=section.id,
                        titel=item_title,
                        typ=item_type,
                        sortierung=item_index,
                    )
                )
        db.flush()
        veroeffentlichen(db, template, version)
        created = True
    vollprobe = db.query(Probeart).filter(Probeart.org_id == org_id, Probeart.name == "Vollprobe").first()
    if vollprobe and vollprobe.checklist_template_id != template.id:
        vollprobe.checklist_template_id = template.id
    return template, created
