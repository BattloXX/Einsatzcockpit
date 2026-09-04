"""Snapshot, Fortschritt und Statuslogik fuer Proben."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import is_proben_admin
from app.core.timezones import now_local, to_org_tz
from app.models.probenplanung import (
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateSection,
    ChecklistTemplateVersion,
    Probeart,
    ProbeCheckliste,
    ProbeChecklistItem,
    ProbeChecklistSection,
    TerminStatus,
)
from app.models.teilnahme import Termin
from app.models.user import User
from app.services.probe_history import write_probe_change


@dataclass(frozen=True)
class ProbeFortschritt:
    gesamt: int
    erledigt: int
    prozent: int
    pflicht_gesamt: int
    pflicht_erledigt: int
    optionale_gesamt: int
    optionale_erledigt: int
    offene_pflichtpunkte: int
    ueberfaellig: int


ERLAUBTE_STATUSWECHSEL: dict[str, frozenset[str]] = {
    TerminStatus.entwurf: frozenset({TerminStatus.geplant, TerminStatus.abgesagt}),
    TerminStatus.geplant: frozenset({TerminStatus.entwurf, TerminStatus.in_vorbereitung, TerminStatus.abgesagt}),
    TerminStatus.in_vorbereitung: frozenset(
        {TerminStatus.geplant, TerminStatus.vorbereitung_abgeschlossen, TerminStatus.abgesagt}
    ),
    TerminStatus.vorbereitung_abgeschlossen: frozenset(
        {TerminStatus.in_vorbereitung, TerminStatus.durchfuehrung_laeuft, TerminStatus.abgesagt}
    ),
    TerminStatus.durchfuehrung_laeuft: frozenset({TerminStatus.durchgefuehrt}),
    TerminStatus.durchgefuehrt: frozenset({TerminStatus.abgeschlossen}),
    TerminStatus.abgeschlossen: frozenset(),
    TerminStatus.abgesagt: frozenset({TerminStatus.entwurf, TerminStatus.geplant}),
}


def snapshot_erzeugen(db: Session, termin: Termin, org: object | None) -> ProbeCheckliste | None:
    """Kopiert die aktive Vorlagenversion einmalig und unabhaengig zur Probe."""
    if termin.id is None:
        db.flush()
    existing = db.query(ProbeCheckliste).filter(ProbeCheckliste.termin_id == termin.id).first()
    if existing:
        return existing
    probeart = db.get(Probeart, termin.probeart_id) if termin.probeart_id else None
    if not probeart or not probeart.checklist_template_id:
        return None
    template = db.get(ChecklistTemplate, probeart.checklist_template_id)
    if not template or not template.aktiv or template.aktive_version_id is None:
        return None
    version = (
        db.query(ChecklistTemplateVersion)
        .filter(
            ChecklistTemplateVersion.id == template.aktive_version_id,
            ChecklistTemplateVersion.template_id == template.id,
            ChecklistTemplateVersion.veroeffentlicht_am.is_not(None),
        )
        .first()
    )
    if not version:
        return None
    snapshot = ProbeCheckliste(
        org_id=termin.org_id,
        termin_id=termin.id,
        template_id=template.id,
        template_version_id=version.id,
        template_name=template.name,
        template_version=version.version,
    )
    db.add(snapshot)
    db.flush()
    sections = (
        db.query(ChecklistTemplateSection)
        .filter(ChecklistTemplateSection.version_id == version.id)
        .order_by(ChecklistTemplateSection.sortierung, ChecklistTemplateSection.id)
        .all()
    )
    for section in sections:
        copied_section = ProbeChecklistSection(
            org_id=termin.org_id,
            checkliste_id=snapshot.id,
            titel=section.titel,
            sortierung=section.sortierung,
        )
        db.add(copied_section)
        db.flush()
        items = (
            db.query(ChecklistTemplateItem)
            .filter(ChecklistTemplateItem.section_id == section.id)
            .order_by(ChecklistTemplateItem.sortierung, ChecklistTemplateItem.id)
            .all()
        )
        for item in items:
            faellig_am = None
            if item.faellig_tage_vorher is not None:
                lokaler_beginn = to_org_tz(termin.beginn, org)
                if lokaler_beginn is not None:
                    faellig_am = lokaler_beginn.date() - timedelta(days=item.faellig_tage_vorher)
            db.add(
                ProbeChecklistItem(
                    org_id=termin.org_id,
                    checkliste_id=snapshot.id,
                    section_id=copied_section.id,
                    quelle="vorlage",
                    template_item_id=item.id,
                    titel=item.titel,
                    hilfetext=item.hilfetext,
                    typ=item.typ,
                    optionen=item.optionen,
                    pflicht=item.pflicht,
                    sortierung=item.sortierung,
                    verantwortlich_member_id=item.default_verantwortlich_member_id,
                    faellig_am=faellig_am,
                )
            )
    return snapshot


def fortschritt(checkliste: ProbeCheckliste, org: object | None, *, heute: date | None = None) -> ProbeFortschritt:
    items = [item for item in checkliste.items if item.zustand != "nicht_relevant"]
    erledigt = sum(item.zustand == "erledigt" for item in items)
    pflicht = [item for item in items if item.pflicht]
    optionale = [item for item in items if not item.pflicht]
    stichtag = heute or now_local(org).date()
    ueberfaellig = sum(
        item.zustand != "erledigt" and item.faellig_am is not None and item.faellig_am < stichtag for item in items
    )
    return ProbeFortschritt(
        gesamt=len(items),
        erledigt=erledigt,
        prozent=round(erledigt * 100 / len(items)) if items else 100,
        pflicht_gesamt=len(pflicht),
        pflicht_erledigt=sum(item.zustand == "erledigt" for item in pflicht),
        optionale_gesamt=len(optionale),
        optionale_erledigt=sum(item.zustand == "erledigt" for item in optionale),
        offene_pflichtpunkte=sum(item.zustand != "erledigt" for item in pflicht),
        ueberfaellig=ueberfaellig,
    )


def darf_vorbereitung_abschliessen(checkliste: ProbeCheckliste | None, org: object | None) -> bool:
    return checkliste is None or fortschritt(checkliste, org).offene_pflichtpunkte == 0


def uebersteuern(db: Session, termin: Termin, user: User, grund: str, *, ip: str | None = None) -> None:
    if not is_proben_admin(user):
        raise ValueError("Administrative Berechtigung erforderlich")
    begruendung = grund.strip()
    if not begruendung:
        raise ValueError("Eine Begründung ist erforderlich")
    before = {
        "von": termin.vorbereitung_uebersteuert_von,
        "am": termin.vorbereitung_uebersteuert_am,
        "grund": termin.vorbereitung_uebersteuert_grund,
    }
    now = datetime.now(UTC).replace(tzinfo=None)
    termin.vorbereitung_uebersteuert_von = user.id
    termin.vorbereitung_uebersteuert_am = now
    termin.vorbereitung_uebersteuert_grund = begruendung
    after = {"von": user.id, "am": now, "grund": begruendung}
    write_probe_change(
        db,
        termin.id,
        "vorbereitung.uebersteuert",
        "vorbereitung",
        "abschluss",
        before,
        after,
        user_id=user.id,
        ip=ip,
    )
    write_audit(
        db,
        "probenplanung.vorbereitung.uebersteuert",
        org_id=termin.org_id,
        user_id=user.id,
        entity_type="termin",
        entity_id=termin.id,
        payload={"grund": begruendung},
        ip=ip,
    )


def statuswechsel(db: Session, termin: Termin, neuer_status: str, user: User, *, ip: str | None = None) -> None:
    if neuer_status not in {status.value for status in TerminStatus}:
        raise ValueError("Unbekannter Probenstatus")
    if neuer_status not in ERLAUBTE_STATUSWECHSEL.get(termin.status, frozenset()):
        raise ValueError(f"Statuswechsel von {termin.status} nach {neuer_status} ist nicht erlaubt")
    if neuer_status == TerminStatus.vorbereitung_abgeschlossen:
        checkliste = db.query(ProbeCheckliste).filter(ProbeCheckliste.termin_id == termin.id).first()
        if not darf_vorbereitung_abschliessen(checkliste, user.org) and termin.vorbereitung_uebersteuert_am is None:
            raise ValueError("Offene Pflichtpunkte verhindern den Abschluss der Vorbereitung")
    vorher = termin.status
    termin.status = neuer_status
    termin.ics_sequence = (termin.ics_sequence or 0) + 1
    write_probe_change(
        db,
        termin.id,
        "status.geaendert",
        "probe",
        "status",
        {"status": vorher},
        {"status": neuer_status},
        user_id=user.id,
        ip=ip,
    )
