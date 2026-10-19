"""Liste und Stammdaten der Katastrophenplaene."""

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.html_utils import sanitize_html
from app.core.templating import templates
from app.core.timezones import now_local
from app.db import get_db
from app.models.katplan import (
    KATPLAN_GEFAHRENARTEN,
    KATPLAN_STATUS_LABELS,
    KatplanAenderung,
    KatplanPlan,
    KatplanPruefung,
)
from app.models.user import User
from app.services.katplan_service import KatplanKonflikt, protokolliere, pruefe_version
from app.services.katplan_zugriff import (
    darf_bearbeiten,
    darf_freigeben,
    require_katplan_bearbeiten,
    require_katplan_freigeben,
    require_katplan_lesen,
)

router = APIRouter(prefix="/katastrophenplaene", tags=["Katastrophenplaene"])


def _plan(db: Session, plan_id: int, user: User) -> KatplanPlan:
    plan = db.query(KatplanPlan).filter(
        KatplanPlan.id == plan_id, KatplanPlan.org_id == user.org_id,
    ).one_or_none()
    if plan is None:
        raise HTTPException(404, "Nicht gefunden")
    return plan


def _kontext(db: Session, user: User, plan: KatplanPlan | None = None) -> dict[str, Any]:
    protokoll = []
    if plan:
        protokoll = db.query(KatplanAenderung, User.display_name).outerjoin(
            User, User.id == KatplanAenderung.user_id,
        ).filter(KatplanAenderung.plan_id == plan.id).order_by(
            KatplanAenderung.zeit.desc(), KatplanAenderung.id.desc(),
        ).limit(50).all()
    return {"user": user, "plan": plan, "gefahrenarten": KATPLAN_GEFAHRENARTEN,
            "status_labels": KATPLAN_STATUS_LABELS, "can_edit": darf_bearbeiten(user),
            "can_release": darf_freigeben(user), "protokoll": protokoll,
            "heute": now_local(user.org).date()}


class Stammdaten:
    def __init__(
        self, titel: str = Form(""), gefahrenarten: list[str] = Form([]),
        beschreibung_html: str = Form(""), geltungsbereich: str = Form(""),
        verantwortliche_stelle: str = Form(""), wiedervorlage_am: str = Form(""),
    ):
        self.form = {"titel": titel, "gefahrenarten": gefahrenarten,
                     "beschreibung_html": beschreibung_html, "geltungsbereich": geltungsbereich,
                     "verantwortliche_stelle": verantwortliche_stelle, "wiedervorlage_am": wiedervorlage_am}
        self.fehler: str | None = None
        termin = None
        try:
            termin = date.fromisoformat(wiedervorlage_am) if wiedervorlage_am else None
        except ValueError:
            self.fehler = "Bitte ein gültiges Wiedervorlagedatum eingeben."
        if not titel.strip() or len(titel.strip()) > 200:
            self.fehler = "Bitte einen Titel mit höchstens 200 Zeichen eingeben."
        if not gefahrenarten or any(code not in KATPLAN_GEFAHRENARTEN for code in gefahrenarten):
            self.fehler = "Bitte mindestens eine gültige Gefahrenart auswählen."
        if len(geltungsbereich) > 300 or len(verantwortliche_stelle) > 200:
            self.fehler = "Geltungsbereich (300) oder verantwortliche Stelle (200) ist zu lang."
        self.werte = {"titel": titel.strip(), "gefahrenarten_json": json.dumps(sorted(set(gefahrenarten))),
                      "beschreibung_html": sanitize_html(beschreibung_html),
                      "geltungsbereich": geltungsbereich.strip() or None,
                      "verantwortliche_stelle": verantwortliche_stelle.strip() or None,
                      "wiedervorlage_am": termin}


@router.get("")
def liste(request: Request, gefahr: str = "", status: str = "", q: str = "", archiv: int = 0,
          db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    query = db.query(KatplanPlan).filter(KatplanPlan.org_id == user.org_id)
    if not darf_bearbeiten(user):
        query = query.filter(KatplanPlan.aktuelle_version_id.is_not(None), KatplanPlan.status != "archiviert")
    elif archiv != 1:
        query = query.filter(KatplanPlan.status != "archiviert")
    if status:
        query = query.filter(KatplanPlan.status == status)
    if q.strip():
        query = query.filter(KatplanPlan.titel.ilike(f"%{q.strip()}%"))
    plaene = query.order_by(KatplanPlan.aktualisiert_am.desc()).all()
    if gefahr:
        plaene = [plan for plan in plaene if gefahr in plan.gefahrenarten]
    context = {**_kontext(db, user), "plaene": plaene, "gefahr": gefahr,
               "status": status, "q": q, "archiv": archiv}
    name = "_liste.html" if request.headers.get("HX-Request") == "true" else "index.html"
    return templates.TemplateResponse(request, f"katplan/{name}", context)


@router.get("/neu")
def neu(request: Request, db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    return templates.TemplateResponse(request, "katplan/formular.html", {**_kontext(db, user), "form": {}})


@router.post("/neu")
def anlegen(request: Request, daten: Stammdaten = Depends(), db: Session = Depends(get_db),
            user: User = Depends(require_katplan_bearbeiten)):
    if daten.fehler:
        return templates.TemplateResponse(request, "katplan/formular.html", {
            **_kontext(db, user), "form": daten.form, "fehler": daten.fehler}, status_code=422)
    plan = KatplanPlan(org_id=user.org_id, erstellt_von_id=user.id, aktualisiert_von_id=user.id, **daten.werte)
    db.add(plan)
    db.flush()
    protokolliere(db, plan, "plan", "angelegt", user)
    db.commit()
    return RedirectResponse(f"/katastrophenplaene/{plan.id}", status_code=303)


@router.get("/{plan_id}")
def detail(plan_id: int, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_katplan_lesen)):
    plan = _plan(db, plan_id, user)
    if not darf_bearbeiten(user) and (plan.aktuelle_version_id is None or plan.status == "archiviert"):
        raise HTTPException(404, "Nicht gefunden")
    return templates.TemplateResponse(request, "katplan/detail.html", _kontext(db, user, plan))


@router.get("/{plan_id}/stammdaten")
def stammdaten(plan_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    form = {feld: getattr(plan, feld) or "" for feld in (
        "titel", "beschreibung_html", "geltungsbereich", "verantwortliche_stelle", "wiedervorlage_am")}
    form["gefahrenarten"] = plan.gefahrenarten
    return templates.TemplateResponse(request, "katplan/_formular.html", {
        **_kontext(db, user, plan), "form": form, "formular_version": plan.version})


@router.post("/{plan_id}/stammdaten")
def speichern(plan_id: int, request: Request, version: int = Form(-1), daten: Stammdaten = Depends(),
              db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    fehler = daten.fehler
    status_code = 422
    try:
        pruefe_version(plan, version)
    except KatplanKonflikt as exc:
        fehler = str(exc)
        status_code = 409
    if fehler:
        return templates.TemplateResponse(request, "katplan/_formular.html", {
            **_kontext(db, user, plan), "form": daten.form, "fehler": fehler,
            "formular_version": version}, status_code=status_code)
    for feld, wert in daten.werte.items():
        alt = getattr(plan, feld)
        if alt != wert:
            setattr(plan, feld, wert)
            protokolliere(db, plan, "plan", "geaendert", user, feld=feld,
                          alt=str(alt) if alt is not None else None, neu=str(wert) if wert is not None else None)
    if plan.status == "freigegeben":
        protokolliere(db, plan, "plan", "geaendert", user, feld="status",
                      alt=plan.status, neu="in_ueberarbeitung")
        plan.status = "in_ueberarbeitung"
    plan.version += 1
    plan.aktualisiert_am = datetime.now(UTC).replace(tzinfo=None)
    plan.aktualisiert_von_id = user.id
    db.commit()
    return templates.TemplateResponse(request, "katplan/_kopf.html", {
        **_kontext(db, user, plan), "protokoll_oob": True})


def _statuswechsel(request: Request, db: Session, user: User, plan_id: int, status: str, aktion: str):
    plan = _plan(db, plan_id, user)
    alt = plan.status
    plan.status = status
    plan.version += 1
    plan.aktualisiert_am = datetime.now(UTC).replace(tzinfo=None)
    plan.aktualisiert_von_id = user.id
    protokolliere(db, plan, "plan", aktion, user, feld="status", alt=alt, neu=status)
    db.add(KatplanPruefung(org_id=plan.org_id, plan_id=plan.id, aktion=aktion, user_id=user.id))
    db.commit()
    return templates.TemplateResponse(request, "katplan/_kopf.html", {
        **_kontext(db, user, plan), "protokoll_oob": True})


@router.post("/{plan_id}/archivieren")
def archivieren(plan_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_katplan_freigeben)):
    return _statuswechsel(request, db, user, plan_id, "archiviert", "archiviert")


@router.post("/{plan_id}/reaktivieren")
def reaktivieren(plan_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_katplan_freigeben)):
    return _statuswechsel(request, db, user, plan_id, "entwurf", "reaktiviert")
