"""Verwaltung der Probearten."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.probenplanung import Probeart, ProbeartTerminTyp
from app.models.teilnahme import Termin
from app.models.user import User

router = APIRouter(prefix="/probenplanung/verwaltung", tags=["probenplanung"])


def require_probenplanung_enabled(request: Request) -> None:
    if not getattr(request.state, "probenplanung_enabled", False):
        raise HTTPException(404, "Nicht gefunden")


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
    return RedirectResponse("/probenplanung/verwaltung/probearten", 303)


@router.get("/probearten/{probeart_id}/bearbeiten", response_class=HTMLResponse)
def probeart_bearbeiten(
    probeart_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
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
    return RedirectResponse("/probenplanung/verwaltung/probearten", 303)


@router.post("/probearten/{probeart_id}/loeschen")
def probeart_loeschen(
    probeart_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_probenplanung_enabled),
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
    return RedirectResponse("/probenplanung/verwaltung/probearten", 303)
