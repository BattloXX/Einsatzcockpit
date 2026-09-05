"""Verwaltung der Probearten."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.dependencies import CurrentOrgId
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import Member
from app.models.probenplanung import (
    ChecklistItemTyp,
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateSection,
    ChecklistTemplateVersion,
    Probeart,
    ProbeartTerminTyp,
)
from app.models.teilnahme import Termin
from app.models.user import User
from app.services.checklist_template_service import (
    neue_version,
    require_entwurf,
    standardvorlage_importieren,
    veroeffentlichen,
)

legacy_router = APIRouter(prefix="/probenplanung/verwaltung", tags=["probenplanung"])
router = APIRouter(prefix="/admin/probenplanung", tags=["probenplanung"])


def require_probenplanung_enabled(request: Request) -> None:
    if not getattr(request.state, "probenplanung_enabled", False):
        raise HTTPException(404, "Nicht gefunden")


@legacy_router.get("/probearten")
@legacy_router.get("/vorlagen")
@legacy_router.get("/oeffentlich")
def verwaltung_redirect(
    request: Request,
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
):
    ziel = request.url.path.replace("/probenplanung/verwaltung", "/admin/probenplanung", 1)
    if request.url.query:
        ziel += "?" + request.url.query
    return RedirectResponse(ziel, status_code=307)


def _probeart_or_404(db: Session, org_id: int | None, probeart_id: int) -> Probeart:
    row = db.query(Probeart).filter(Probeart.id == probeart_id, Probeart.org_id == org_id).first()
    if not row:
        raise HTTPException(404, "Probeart nicht gefunden")
    return row


def _formularwerte(
    name: str,
    kurz: str,
    farbe: str,
    sortierung: int,
    standarddauer_minuten: int | None,
    druckgruppe: str,
    termin_typ: str,
    checkliste_erforderlich: str,
    teilnahme_erforderlich: str,
    nachbereitung_erforderlich: str,
    uebungseinsatz_erlaubt: str,
) -> tuple[dict, list[str]]:
    fehler: list[str] = []
    if not name.strip():
        fehler.append("Name ist erforderlich.")
    if not kurz.strip():
        fehler.append("Kurzbezeichnung ist erforderlich.")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", farbe):
        fehler.append("Farbe muss ein Hex-Farbwert sein.")
    if termin_typ not in {x.value for x in ProbeartTerminTyp}:
        fehler.append("Ungültiger Termin-Typ.")
    return {
        "name": name.strip()[:150],
        "kurz": kurz.strip()[:20],
        "farbe": farbe.lower(),
        "sortierung": sortierung,
        "standarddauer_minuten": standarddauer_minuten,
        "druckgruppe": druckgruppe.strip()[:50] or None,
        "termin_typ": termin_typ,
        "checkliste_erforderlich": bool(checkliste_erforderlich),
        "teilnahme_erforderlich": bool(teilnahme_erforderlich),
        "nachbereitung_erforderlich": bool(nachbereitung_erforderlich),
        "uebungseinsatz_erlaubt": bool(uebungseinsatz_erlaubt),
    }, fehler


def _render_form(
    request: Request, user: User, probeart: Probeart | None, form: dict | None = None, fehler: list[str] | None = None
):
    return templates.TemplateResponse(
        request,
        "probenplanung/_probeart_formular.html",
        {
            "user": user,
            "probeart": probeart,
            "form": form or {},
            "fehler": fehler or [],
        },
    )


@router.get("/probearten", response_class=HTMLResponse)
def probearten_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    rows = db.query(Probeart).filter(Probeart.org_id == user.org_id).order_by(Probeart.sortierung, Probeart.name).all()
    return templates.TemplateResponse(
        request, "probenplanung/verwaltung_probearten.html", {"user": user, "probearten": rows}
    )


@router.get("/probearten/neu", response_class=HTMLResponse)
def probeart_neu(
    request: Request,
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    return _render_form(request, user, None)


@router.post("/probearten", response_class=HTMLResponse)
def probeart_anlegen(
    request: Request,
    name: str = Form(""),
    kurz: str = Form(""),
    farbe: str = Form("#2563eb"),
    sortierung: int = Form(0),
    standarddauer_minuten: int | None = Form(None),
    druckgruppe: str = Form(""),
    termin_typ: str = Form("uebung"),
    checkliste_erforderlich: str = Form(""),
    teilnahme_erforderlich: str = Form(""),
    nachbereitung_erforderlich: str = Form(""),
    uebungseinsatz_erlaubt: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    values, fehler = _formularwerte(
        name,
        kurz,
        farbe,
        sortierung,
        standarddauer_minuten,
        druckgruppe,
        termin_typ,
        checkliste_erforderlich,
        teilnahme_erforderlich,
        nachbereitung_erforderlich,
        uebungseinsatz_erlaubt,
    )
    if db.query(Probeart).filter(Probeart.org_id == user.org_id, Probeart.name == values["name"]).first():
        fehler.append("Eine Probeart mit diesem Namen existiert bereits.")
    if fehler:
        return _render_form(request, user, None, values, fehler)
    row = Probeart(org_id=user.org_id, **values)
    db.add(row)
    db.flush()
    write_audit(
        db,
        "probenplanung.probeart.erstellt",
        org_id=user.org_id,
        user_id=user.id,
        entity_type="probeart",
        entity_id=row.id,
        payload={"name": row.name},
    )
    db.commit()
    return RedirectResponse("/admin/probenplanung/probearten", 303)


@router.get("/probearten/{probeart_id}/bearbeiten", response_class=HTMLResponse)
def probeart_bearbeiten(
    probeart_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    return _render_form(request, user, _probeart_or_404(db, user.org_id, probeart_id))


@router.post("/probearten/{probeart_id}", response_class=HTMLResponse)
def probeart_speichern(
    probeart_id: int,
    request: Request,
    name: str = Form(""),
    kurz: str = Form(""),
    farbe: str = Form("#2563eb"),
    sortierung: int = Form(0),
    standarddauer_minuten: int | None = Form(None),
    druckgruppe: str = Form(""),
    termin_typ: str = Form("uebung"),
    checkliste_erforderlich: str = Form(""),
    teilnahme_erforderlich: str = Form(""),
    nachbereitung_erforderlich: str = Form(""),
    uebungseinsatz_erlaubt: str = Form(""),
    aktiv: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    row = _probeart_or_404(db, user.org_id, probeart_id)
    values, fehler = _formularwerte(
        name,
        kurz,
        farbe,
        sortierung,
        standarddauer_minuten,
        druckgruppe,
        termin_typ,
        checkliste_erforderlich,
        teilnahme_erforderlich,
        nachbereitung_erforderlich,
        uebungseinsatz_erlaubt,
    )
    duplicate = (
        db.query(Probeart)
        .filter(Probeart.org_id == user.org_id, Probeart.name == values["name"], Probeart.id != row.id)
        .first()
    )
    if duplicate:
        fehler.append("Eine Probeart mit diesem Namen existiert bereits.")
    values["aktiv"] = bool(aktiv)
    if fehler:
        return _render_form(request, user, row, values, fehler)
    before = {"name": row.name, "aktiv": row.aktiv}
    for key, value in values.items():
        setattr(row, key, value)
    write_audit(
        db,
        "probenplanung.probeart.geaendert",
        org_id=user.org_id,
        user_id=user.id,
        entity_type="probeart",
        entity_id=row.id,
        payload={"vorher": before, "name": row.name, "aktiv": row.aktiv},
    )
    db.commit()
    return RedirectResponse("/admin/probenplanung/probearten", 303)


@router.post("/probearten/{probeart_id}/loeschen")
def probeart_loeschen(
    probeart_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    row = _probeart_or_404(db, user.org_id, probeart_id)
    if db.query(Termin).filter(Termin.org_id == user.org_id, Termin.probeart_id == row.id).first():
        raise HTTPException(409, "Verwendete Probearten können nur deaktiviert werden")
    row_id, row_name = row.id, row.name
    db.delete(row)
    write_audit(
        db,
        "probenplanung.probeart.geloescht",
        org_id=user.org_id,
        user_id=user.id,
        entity_type="probeart",
        entity_id=row_id,
        payload={"name": row_name},
    )
    db.commit()
    return RedirectResponse("/admin/probenplanung/probearten", 303)


def _template_or_404(db: Session, org_id: int | None, template_id: int) -> ChecklistTemplate:
    row = (
        db.query(ChecklistTemplate)
        .filter(ChecklistTemplate.id == template_id, ChecklistTemplate.org_id == org_id)
        .first()
    )
    if not row:
        raise HTTPException(404, "Vorlage nicht gefunden")
    return row


def _version_or_404(db: Session, org_id: int | None, template_id: int, version_id: int) -> ChecklistTemplateVersion:
    row = (
        db.query(ChecklistTemplateVersion)
        .filter(
            ChecklistTemplateVersion.id == version_id,
            ChecklistTemplateVersion.template_id == template_id,
            ChecklistTemplateVersion.org_id == org_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, "Vorlagenversion nicht gefunden")
    return row


def _section_or_404(
    db: Session, org_id: int | None, template_id: int, version_id: int, section_id: int
) -> ChecklistTemplateSection:
    row = (
        db.query(ChecklistTemplateSection)
        .join(ChecklistTemplateVersion, ChecklistTemplateSection.version_id == ChecklistTemplateVersion.id)
        .filter(
            ChecklistTemplateSection.id == section_id,
            ChecklistTemplateSection.version_id == version_id,
            ChecklistTemplateSection.org_id == org_id,
            ChecklistTemplateVersion.template_id == template_id,
            ChecklistTemplateVersion.org_id == org_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, "Bereich nicht gefunden")
    return row


def _item_or_404(
    db: Session,
    org_id: int | None,
    template_id: int,
    version_id: int,
    section_id: int,
    item_id: int,
) -> ChecklistTemplateItem:
    _section_or_404(db, org_id, template_id, version_id, section_id)
    row = (
        db.query(ChecklistTemplateItem)
        .filter(
            ChecklistTemplateItem.id == item_id,
            ChecklistTemplateItem.section_id == section_id,
            ChecklistTemplateItem.org_id == org_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, "Punkt nicht gefunden")
    return row


def _parse_order(raw: str) -> list[int]:
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or any(not isinstance(item, int) for item in value):
            raise ValueError
        return value
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(422, "sortierung muss eine JSON-Liste von IDs sein") from exc


def _detail_context(
    db: Session, request: Request, user: User, template: ChecklistTemplate, version_id: int | None = None
):
    versions = (
        db.query(ChecklistTemplateVersion)
        .filter(
            ChecklistTemplateVersion.template_id == template.id,
            ChecklistTemplateVersion.org_id == user.org_id,
        )
        .order_by(ChecklistTemplateVersion.version.desc())
        .all()
    )
    selected = next((row for row in versions if row.id == version_id), None)
    if version_id is not None and selected is None:
        raise HTTPException(404, "Vorlagenversion nicht gefunden")
    selected = selected or (versions[0] if versions else None)
    sections: list[tuple[ChecklistTemplateSection, list[ChecklistTemplateItem]]] = []
    if selected:
        section_rows = (
            db.query(ChecklistTemplateSection)
            .filter(
                ChecklistTemplateSection.version_id == selected.id,
                ChecklistTemplateSection.org_id == user.org_id,
            )
            .order_by(ChecklistTemplateSection.sortierung, ChecklistTemplateSection.id)
            .all()
        )
        for section in section_rows:
            items = (
                db.query(ChecklistTemplateItem)
                .filter(
                    ChecklistTemplateItem.section_id == section.id,
                    ChecklistTemplateItem.org_id == user.org_id,
                )
                .order_by(ChecklistTemplateItem.sortierung, ChecklistTemplateItem.id)
                .all()
            )
            sections.append((section, items))
    members = db.query(Member).filter(Member.org_id == user.org_id).order_by(Member.lastname, Member.firstname).all()
    return {
        "user": user,
        "template": template,
        "versions": versions,
        "version": selected,
        "sections": sections,
        "item_types": [item.value for item in ChecklistItemTyp],
        "members": members,
    }


@router.get("/vorlagen", response_class=HTMLResponse)
def vorlagen_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    rows = (
        db.query(ChecklistTemplate)
        .filter(ChecklistTemplate.org_id == user.org_id)
        .order_by(ChecklistTemplate.name)
        .all()
    )
    return templates.TemplateResponse(
        request, "probenplanung/verwaltung_vorlagen.html", {"user": user, "vorlagen": rows}
    )


@router.post("/vorlagen")
def vorlage_anlegen(
    request: Request,
    name: str = Form(""),
    beschreibung: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    clean_name = name.strip()[:150]
    if not clean_name:
        raise HTTPException(422, "Name ist erforderlich")
    if (
        db.query(ChecklistTemplate)
        .filter(ChecklistTemplate.org_id == user.org_id, ChecklistTemplate.name == clean_name)
        .first()
    ):
        raise HTTPException(409, "Eine Vorlage mit diesem Namen existiert bereits")
    template = ChecklistTemplate(
        org_id=user.org_id,
        name=clean_name,
        beschreibung=beschreibung.strip() or None,
        erstellt_von=user.id,
    )
    db.add(template)
    db.flush()
    version = ChecklistTemplateVersion(org_id=user.org_id, template_id=template.id, version=1, erstellt_von=user.id)
    db.add(version)
    db.flush()
    write_audit(
        db,
        "probenplanung.vorlage.erstellt",
        org_id=user.org_id,
        user_id=user.id,
        entity_type="checklist_template",
        entity_id=template.id,
        payload={"name": template.name},
    )
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template.id}?version={version.id}", 303)


@router.post("/vorlagen/standard-import")
def standardvorlage_import(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    if user.org_id is None:
        raise HTTPException(400, "Organisation auswählen")
    template, created = standardvorlage_importieren(db, user.org_id, user.id)
    write_audit(
        db,
        "probenplanung.vorlage.standard_import",
        org_id=user.org_id,
        user_id=user.id,
        entity_type="checklist_template",
        entity_id=template.id,
        payload={"erstellt": created},
    )
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template.id}", 303)


@router.get("/vorlagen/{template_id}", response_class=HTMLResponse)
def vorlage_detail(
    template_id: int,
    request: Request,
    version: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    template = _template_or_404(db, user.org_id, template_id)
    return templates.TemplateResponse(
        request,
        "probenplanung/verwaltung_vorlage_detail.html",
        _detail_context(db, request, user, template, version),
    )


@router.post("/vorlagen/{template_id}/version/neu")
def vorlage_neue_version(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    template = _template_or_404(db, user.org_id, template_id)
    version = neue_version(db, template, user.id)
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template.id}?version={version.id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/veroeffentlichen")
def vorlage_veroeffentlichen(
    template_id: int,
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    template = _template_or_404(db, user.org_id, template_id)
    version = _version_or_404(db, user.org_id, template_id, version_id)
    veroeffentlichen(db, template, version)
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template.id}?version={version.id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche")
def bereich_anlegen(
    template_id: int,
    version_id: int,
    request: Request,
    titel: str = Form(""),
    beschreibung: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    _template_or_404(db, user.org_id, template_id)
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    clean_title = titel.strip()[:200]
    if not clean_title:
        raise HTTPException(422, "Titel ist erforderlich")
    next_sort = (
        db.query(func.max(ChecklistTemplateSection.sortierung))
        .filter(ChecklistTemplateSection.version_id == version.id, ChecklistTemplateSection.org_id == user.org_id)
        .scalar()
    )
    db.add(
        ChecklistTemplateSection(
            org_id=user.org_id,
            version_id=version.id,
            titel=clean_title,
            beschreibung=beschreibung.strip() or None,
            sortierung=(next_sort or 0) + 1,
        )
    )
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/sortieren")
def bereiche_sortieren(
    template_id: int,
    version_id: int,
    request: Request,
    sortierung: str = Form("[]"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    _template_or_404(db, user.org_id, template_id)
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    rows = (
        db.query(ChecklistTemplateSection)
        .filter(ChecklistTemplateSection.version_id == version.id, ChecklistTemplateSection.org_id == user.org_id)
        .all()
    )
    order = _parse_order(sortierung)
    if set(order) != {row.id for row in rows}:
        raise HTTPException(422, "sortierung muss alle Bereichs-IDs enthalten")
    by_id = {row.id: row for row in rows}
    for index, row_id in enumerate(order):
        by_id[row_id].sortierung = index
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/{section_id}")
def bereich_speichern(
    template_id: int,
    version_id: int,
    section_id: int,
    request: Request,
    titel: str = Form(""),
    beschreibung: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    row = _section_or_404(db, user.org_id, template_id, version_id, section_id)
    if not titel.strip():
        raise HTTPException(422, "Titel ist erforderlich")
    row.titel, row.beschreibung = titel.strip()[:200], beschreibung.strip() or None
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/{section_id}/loeschen")
def bereich_loeschen(
    template_id: int,
    version_id: int,
    section_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    db.delete(_section_or_404(db, user.org_id, template_id, version_id, section_id))
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


def _item_values(
    titel: str,
    hilfetext: str,
    typ: str,
    optionen: str,
    pflicht: str,
    default_verantwortlich_member_id: int | None,
    faellig_tage_vorher: int | None,
) -> dict:
    if not titel.strip():
        raise HTTPException(422, "Titel ist erforderlich")
    if typ not in {item.value for item in ChecklistItemTyp}:
        raise HTTPException(422, "Ungültiger Punkt-Typ")
    if typ in {ChecklistItemTyp.auswahl.value, ChecklistItemTyp.mehrfachauswahl.value}:
        try:
            parsed_options = json.loads(optionen)
        except json.JSONDecodeError:
            parsed_options = None
        if isinstance(parsed_options, list) and all(isinstance(choice, str) for choice in parsed_options):
            choices = [choice.strip() for choice in parsed_options if choice.strip()]
        else:
            choices = [choice.strip() for choice in optionen.splitlines() if choice.strip()]
        if not choices:
            raise HTTPException(422, "Auswahltypen benötigen Optionen")
        stored_options = json.dumps(choices, ensure_ascii=False)
    else:
        stored_options = None
    return {
        "titel": titel.strip()[:200],
        "hilfetext": hilfetext.strip() or None,
        "typ": typ,
        "optionen": stored_options,
        "pflicht": bool(pflicht),
        "default_verantwortlich_member_id": default_verantwortlich_member_id,
        "faellig_tage_vorher": faellig_tage_vorher,
    }


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/{section_id}/punkte")
def punkt_anlegen(
    template_id: int,
    version_id: int,
    section_id: int,
    request: Request,
    titel: str = Form(""),
    hilfetext: str = Form(""),
    typ: str = Form("checkbox"),
    optionen: str = Form(""),
    pflicht: str = Form(""),
    default_verantwortlich_member_id: int | None = Form(None),
    faellig_tage_vorher: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    section = _section_or_404(db, user.org_id, template_id, version_id, section_id)
    values = _item_values(
        titel, hilfetext, typ, optionen, pflicht, default_verantwortlich_member_id, faellig_tage_vorher
    )
    next_sort = (
        db.query(func.max(ChecklistTemplateItem.sortierung))
        .filter(ChecklistTemplateItem.section_id == section.id, ChecklistTemplateItem.org_id == user.org_id)
        .scalar()
    )
    db.add(ChecklistTemplateItem(org_id=user.org_id, section_id=section.id, sortierung=(next_sort or 0) + 1, **values))
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/{section_id}/punkte/sortieren")
def punkte_sortieren(
    template_id: int,
    version_id: int,
    section_id: int,
    request: Request,
    sortierung: str = Form("[]"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    section = _section_or_404(db, user.org_id, template_id, version_id, section_id)
    rows = (
        db.query(ChecklistTemplateItem)
        .filter(ChecklistTemplateItem.section_id == section.id, ChecklistTemplateItem.org_id == user.org_id)
        .all()
    )
    order = _parse_order(sortierung)
    if set(order) != {row.id for row in rows}:
        raise HTTPException(422, "sortierung muss alle Punkt-IDs enthalten")
    by_id = {row.id: row for row in rows}
    for index, row_id in enumerate(order):
        by_id[row_id].sortierung = index
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/{section_id}/punkte/{item_id}")
def punkt_speichern(
    template_id: int,
    version_id: int,
    section_id: int,
    item_id: int,
    request: Request,
    titel: str = Form(""),
    hilfetext: str = Form(""),
    typ: str = Form("checkbox"),
    optionen: str = Form(""),
    pflicht: str = Form(""),
    default_verantwortlich_member_id: int | None = Form(None),
    faellig_tage_vorher: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    row = _item_or_404(db, user.org_id, template_id, version_id, section_id, item_id)
    for key, value in _item_values(
        titel, hilfetext, typ, optionen, pflicht, default_verantwortlich_member_id, faellig_tage_vorher
    ).items():
        setattr(row, key, value)
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


@router.post("/vorlagen/{template_id}/version/{version_id}/bereiche/{section_id}/punkte/{item_id}/loeschen")
def punkt_loeschen(
    template_id: int,
    version_id: int,
    section_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    version = _version_or_404(db, user.org_id, template_id, version_id)
    require_entwurf(version)
    db.delete(_item_or_404(db, user.org_id, template_id, version_id, section_id, item_id))
    db.commit()
    return RedirectResponse(f"/admin/probenplanung/vorlagen/{template_id}?version={version_id}", 303)


def _public_token_liste(db: Session, org_id: int | None):
    from app.models.probenplanung import ProbePublicToken
    return (db.query(ProbePublicToken).filter(ProbePublicToken.org_id == org_id)
            .order_by(ProbePublicToken.erstellt_am.desc()).all())


def _public_verwaltung_response(request: Request, db: Session, user: User, plain: str | None = None):
    from app.config import settings
    from app.models.master import OrgSettings

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    basis = (settings.PUBLIC_BASE_URL or settings.APP_BASE_URL).rstrip("/")
    url = f"{basis}/p/probenplan/{plain}" if plain else None
    return templates.TemplateResponse(request, "probenplanung/verwaltung_oeffentlich.html", {
        "user": user, "tokens": _public_token_liste(db, user.org_id),
        "public_aktiv": bool(org_settings and org_settings.probenplanung_public_aktiv),
        "public_url": url, "ics_url": f"{url}.ics" if url else None,
        "webcal_url": "webcal://" + url.split("://", 1)[1] + ".ics" if url else None,
    }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/oeffentlich", response_class=HTMLResponse)
def public_verwaltung(
    request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled), _: CurrentOrgId = None,
):
    return _public_verwaltung_response(request, db, user)


@router.post("/oeffentlich/freigabe")
def public_freigabe(
    request: Request, public_aktiv: str = Form(""), db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled), _: CurrentOrgId = None,
):
    from app.models.master import OrgSettings
    row = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if row is None:
        raise HTTPException(404, "Nicht gefunden")
    row.probenplanung_public_aktiv = bool(public_aktiv)
    write_audit(db, "probenplanung.public.freigabe", org_id=user.org_id, user_id=user.id,
                payload={"aktiv": bool(public_aktiv)})
    db.commit()
    return RedirectResponse("/admin/probenplanung/oeffentlich", 303)


@router.post("/oeffentlich")
def public_token_erzeugen(
    request: Request, bezeichnung: str = Form(""), db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled), _: CurrentOrgId = None,
):
    import hashlib
    import secrets

    from app.models.probenplanung import ProbePublicToken
    plain = secrets.token_urlsafe(32)
    db.add(ProbePublicToken(org_id=user.org_id, art="plan", bezeichnung=bezeichnung.strip()[:150] or None,
                            token_hash=hashlib.sha256(plain.encode()).hexdigest()))
    write_audit(db, "probenplanung.public.token_erzeugt", org_id=user.org_id, user_id=user.id)
    db.commit()
    return _public_verwaltung_response(request, db, user, plain)


@router.post("/oeffentlich/{token_id}/{aktion}")
def public_token_aendern(
    token_id: int, aktion: str, request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled), _: CurrentOrgId = None,
):
    import hashlib
    import secrets
    from datetime import UTC, datetime

    from app.models.probenplanung import ProbePublicToken
    row = (db.query(ProbePublicToken)
           .filter(ProbePublicToken.id == token_id, ProbePublicToken.org_id == user.org_id).first())
    if row is None or aktion not in {"widerrufen", "regenerieren"}:
        raise HTTPException(404, "Nicht gefunden")
    row.widerrufen_am = datetime.now(UTC).replace(tzinfo=None)
    plain = None
    if aktion == "regenerieren":
        plain = secrets.token_urlsafe(32)
        db.add(ProbePublicToken(
            org_id=user.org_id, art=row.art, termin_id=row.termin_id, jahr=row.jahr,
            filter_probeart_ids=row.filter_probeart_ids, bezeichnung=row.bezeichnung,
            token_hash=hashlib.sha256(plain.encode()).hexdigest(),
        ))
    write_audit(db, "probenplanung.public.token_" + aktion, org_id=user.org_id, user_id=user.id,
                entity_type="probe_public_token", entity_id=row.id)
    db.commit()
    if plain:
        return _public_verwaltung_response(request, db, user, plain)
    return RedirectResponse("/admin/probenplanung/oeffentlich", 303)
