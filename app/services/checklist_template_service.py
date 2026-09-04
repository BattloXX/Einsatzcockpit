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
STANDARD_SECTIONS: tuple[tuple[str, tuple[tuple[str, str, bool], ...]], ...] = (
    (
        "Stammdaten",
        (
            ("Ausführende / Organisatoren", "person", False),
            ("Termin", "datum", False),
            ("Ort", "text", False),
            ("Objekt", "text", False),
        ),
    ),
    (
        "Abstimmungen",
        (
            ("mit Eigentümer vereinbart", "checkbox", True),
            ("Datum der Vereinbarung", "datum", False),
            ("Bewohner verständigt", "checkbox", True),
            ("Datum", "datum", False),
            ("Sondervereinbarungen", "langtext", False),
        ),
    ),
    (
        "Darsteller / Verletzte",
        (
            ("Feuerwehrjugend eingeplant", "checkbox", False),
            ("Jugendleiter verständigt", "checkbox", False),
            ("externe Personen eingeplant", "checkbox", False),
            ("Kantine verständigt", "checkbox", False),
            ("Verletzte gekennzeichnet", "checkbox", False),
            ("Anzahl Verletzte / Darsteller", "text", False),
            ("Bemerkungen", "langtext", False),
        ),
    ),
    (
        "Hilfsmittel",
        (
            ("Fahnen", "checkbox", False),
            ("Blitzleuchte", "checkbox", False),
            ("Nebelmaschine", "checkbox", False),
            ("Farbnebel", "checkbox", False),
            ("weitere Hilfsmittel", "langtext", False),
        ),
    ),
    (
        "Unterlagen",
        (
            ("Brandschutzplan vorhanden", "checkbox", False),
            ("Objektplan vorhanden", "checkbox", False),
            ("Übungsskizze vorhanden", "checkbox", False),
            ("sonstige Unterlagen", "langtext", False),
        ),
    ),
    (
        "Aufgabenplanung",
        (
            ("Einsatzleiter", "person", False),
            ("Fahrzeug-/Gruppenaufgaben", "langtext", False),
            ("besondere Aufgaben", "langtext", False),
            ("Aufgabenvorstellung durchgeführt", "checkbox", True),
        ),
    ),
    (
        "Information",
        (
            ("Dienstgrade informiert", "checkbox", True),
            ("Alarmtext vorbereitet", "checkbox", True),
            ("besondere Gefahren dokumentiert", "checkbox", True),
            ("gefährliche Stoffe", "langtext", False),
            ("verschlossene Türen", "text", False),
            ("Schlüssel", "text", False),
            ("geplante Lageänderungen", "langtext", False),
            ("sonstige Besonderheiten", "langtext", False),
        ),
    ),
    (
        "Lage / Skizze",
        (("Übungsskizze", "bild", False), ("Lageplan", "datei", False), ("Bild oder Plan", "datei", False)),
    ),
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
            for item_index, (item_title, item_type, item_required) in enumerate(items):
                db.add(
                    ChecklistTemplateItem(
                        org_id=org_id,
                        section_id=section.id,
                        titel=item_title,
                        typ=item_type,
                        pflicht=item_required,
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
