"""Liste und Stammdaten der Katastrophenplaene."""

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.templating import templates
from app.core.timezones import now_local
from app.db import get_db
from app.models.katplan import (
    KATPLAN_GEFAHRENARTEN,
    KATPLAN_STATUS_LABELS,
    KatplanAbschnitt,
    KatplanAenderung,
    KatplanElement,
    KatplanElementPhase,
    KatplanPhase,
    KatplanPlan,
    KatplanPruefung,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.major_incident import AUFTRAGSART_LABEL, AUFTRAGSART_WERTE
from app.models.user import User
from app.services import katplan_freigabe_service as freigabe_service
from app.services.katplan_service import (
    KatplanKonflikt,
    abschnitte_baum,
    aktualisiere_element_suchtext,
    bereinige_beschreibung,
    loesche_abschnitt,
    markiere_ueberarbeitung,
    naechste_kennung,
    protokolliere,
    pruefe_version,
    sortiere_baum,
    verschiebe_nachbar,
)
from app.services.katplan_typen import ELEMENT_TYPEN, ELEMENT_VORLAGEN, bereinige_daten
from app.services.katplan_zugriff import (
    darf_bearbeiten,
    darf_freigeben,
    require_katplan_bearbeiten,
    require_katplan_freigeben,
    require_katplan_lesen,
    sieht_vertraulich,
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
    aktuelle_version = None
    freigegeben_von = None
    if plan and plan.aktuelle_version_id:
        try:
            aktuelle_version, _, _ = freigabe_service.lade_version(db, plan)
            freigeber = db.query(User).filter_by(
                id=aktuelle_version.freigegeben_von_id, org_id=plan.org_id,
            ).one_or_none()
            freigegeben_von = freigeber.display_name if freigeber else "Gelöschter Benutzer"
        except ValueError:
            aktuelle_version = None
    if plan:
        protokoll = db.query(KatplanAenderung, User.display_name).outerjoin(
            User, User.id == KatplanAenderung.user_id,
        ).filter(KatplanAenderung.plan_id == plan.id).order_by(
            KatplanAenderung.zeit.desc(), KatplanAenderung.id.desc(),
        ).limit(50).all()
    return {"user": user, "plan": plan, "gefahrenarten": KATPLAN_GEFAHRENARTEN,
            "status_labels": KATPLAN_STATUS_LABELS, "can_edit": darf_bearbeiten(user),
            "can_release": darf_freigeben(user), "protokoll": protokoll,
            "aktuelle_version": aktuelle_version, "freigegeben_von": freigegeben_von,
            "heute": now_local(user.org).date(),
            **(_editor_kontext(db, plan) if plan and darf_bearbeiten(user) else {})}


class Stammdaten:
    def __init__(
        self, titel: str = Form(""), gefahrenarten: list[str] = Form([]),
        beschreibung_html: str = Form(""), geltungsbereich: str = Form(""),
        verantwortliche_stelle: str = Form(""), wiedervorlage_am: str = Form(""),
    ):
        self.form = {"titel": titel, "gefahrenarten": gefahrenarten,
                     "beschreibung_html": bereinige_beschreibung(beschreibung_html) or "",
                     "geltungsbereich": geltungsbereich,
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
                      "beschreibung_html": bereinige_beschreibung(beschreibung_html),
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
    if status and darf_bearbeiten(user):
        query = query.filter(KatplanPlan.status == status)
    elif status and status != "freigegeben":
        query = query.filter(KatplanPlan.id == -1)
    if q.strip() and darf_bearbeiten(user):
        query = query.filter(KatplanPlan.titel.ilike(f"%{q.strip()}%"))
    plaene: list[Any] = query.order_by(KatplanPlan.aktualisiert_am.desc()).all()
    version_ids = [p.aktuelle_version_id for p in plaene if p.aktuelle_version_id]
    versionen = {v.id: v for v in db.query(KatplanVersion).filter(
        KatplanVersion.org_id == user.org_id, KatplanVersion.id.in_(version_ids),
    ).all()} if version_ids else {}
    if not darf_bearbeiten(user):
        leseliste = []
        for plan in plaene:
            v = versionen.get(plan.aktuelle_version_id or 0)
            if v is None:
                continue
            daten = json.loads(v.plan_json)
            termin = daten.get("wiedervorlage_am")
            leseliste.append({**daten, "id": plan.id, "status": "freigegeben",
                              "wiedervorlage_am": date.fromisoformat(termin) if termin else None,
                              "aktualisiert_am": v.freigegeben_am})
        plaene = leseliste
        if q.strip():
            plaene = [p for p in plaene if q.strip().casefold() in p["titel"].casefold()]
    if gefahr:
        plaene = [plan for plan in plaene
                  if gefahr in (plan["gefahrenarten"] if isinstance(plan, dict) else plan.gefahrenarten)]
    context = {**_kontext(db, user), "plaene": plaene, "gefahr": gefahr,
               "status": status, "q": q, "archiv": archiv, "versionen": versionen}
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


def _sichtbarer_plan(db: Session, plan_id: int, user: User) -> KatplanPlan:
    plan = _plan(db, plan_id, user)
    if not darf_bearbeiten(user) and (plan.aktuelle_version_id is None or plan.status == "archiviert"):
        raise HTTPException(404, "Nicht gefunden")
    return plan


def _lade_version(db: Session, plan: KatplanPlan, vid: int | None = None):
    try:
        return freigabe_service.lade_version(db, plan, vid)
    except ValueError as exc:
        raise HTTPException(404, "Nicht gefunden") from exc


def _lesekontext(db: Session, user: User, plan: KatplanPlan, vid: int | None = None) -> dict:
    version, daten, elemente = _lade_version(db, plan, vid)
    elemente = [e for e in elemente if sieht_vertraulich(user) or not e.vertraulich]
    termin = daten.get("wiedervorlage_am")
    return {"lesekopf": {**daten, "wiedervorlage_am": date.fromisoformat(termin) if termin else None},
            "plan_daten": daten, "leseversion": version,
            "version_baum": freigabe_service.version_baum(daten, elemente, mit_vertraulichen=True),
            "typen": ELEMENT_TYPEN,
            "element_daten": {e.id: json.loads(e.daten_json or "{}") for e in elemente},
            "element_tags": {e.id: json.loads(e.tags_json or "[]") for e in elemente},
            "element_gsl": {e.id: json.loads(e.gsl_json or "{}") for e in elemente},
            "element_phasen": {e.id: [p for p in daten.get("phasen", [])
                                      if p["id"] in json.loads(e.phasen_json)] for e in elemente}}


@router.get("/{plan_id}")
def detail(plan_id: int, request: Request, ansicht: str = "", db: Session = Depends(get_db),
           user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    arbeitsstand = darf_bearbeiten(user) and (not plan.aktuelle_version_id or ansicht == "arbeitsstand"
                    or (ansicht != "freigabe" and plan.status != "freigegeben"))
    context = {**_kontext(db, user, plan), "arbeitsstand": arbeitsstand}
    if not arbeitsstand:
        context.update(_lesekontext(db, user, plan))
    return templates.TemplateResponse(request, "katplan/detail.html", context)


@router.get("/{plan_id}/versionen")
def versionen(plan_id: int, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    zeilen = db.query(KatplanVersion, User.display_name).outerjoin(
        User, (User.id == KatplanVersion.freigegeben_von_id) & (User.org_id == plan.org_id),
    ).filter(KatplanVersion.plan_id == plan.id, KatplanVersion.org_id == plan.org_id).order_by(
        KatplanVersion.versionsnummer).all()
    pruefungen = db.query(KatplanPruefung, User.display_name).outerjoin(
        User, (User.id == KatplanPruefung.user_id) & (User.org_id == plan.org_id),
    ).filter(KatplanPruefung.plan_id == plan.id, KatplanPruefung.org_id == plan.org_id).order_by(
        KatplanPruefung.zeit.desc(), KatplanPruefung.id.desc()).all()
    context = {**_kontext(db, user, plan), "versionen_liste": zeilen, "pruefungen": pruefungen}
    if not darf_bearbeiten(user):
        context.update(_lesekontext(db, user, plan))
    return templates.TemplateResponse(request, "katplan/versionen.html", context)


@router.get("/{plan_id}/versionen/{vid}")
def historische_version(plan_id: int, vid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    return templates.TemplateResponse(request, "katplan/detail.html", {
        **_kontext(db, user, plan), **_lesekontext(db, user, plan, vid), "historisch": True})


@router.get("/{plan_id}/vergleich")
def vergleich(plan_id: int, request: Request, von: int | None = None, nach: str = "",
              db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    if nach == "arbeitsstand" and not darf_bearbeiten(user):
        raise HTTPException(403, "Keine Berechtigung")
    ids = [v.id for v in db.query(KatplanVersion).filter_by(plan_id=plan.id, org_id=plan.org_id).order_by(
        KatplanVersion.versionsnummer).all()]
    if not ids:
        raise HTTPException(404, "Nicht gefunden")
    alt, alt_kopf, alt_elemente = _lade_version(db, plan, von if von is not None else ids[max(0, len(ids) - 2)])
    if nach == "arbeitsstand":
        with db.no_autoflush:
            neu_kopf, snapshot = freigabe_service.erstelle_snapshot(db, plan, flush=False)
        neu_elemente = [KatplanVersionElement(org_id=plan.org_id, **daten) for daten in snapshot]
        nach_label = "Arbeitsstand"
    else:
        try:
            nid = int(nach) if nach else ids[-1]
        except ValueError as exc:
            raise HTTPException(404, "Nicht gefunden") from exc
        neu, neu_kopf, neu_elemente = _lade_version(db, plan, nid)
        nach_label = f"Version {neu.versionsnummer}"
    # Bei Vertraulichkeitswechseln weder alte noch neue Inhalte offenlegen.
    verborgen = {e.element_uuid for e in [*alt_elemente, *neu_elemente] if e.vertraulich}
    if not sieht_vertraulich(user):
        alt_elemente = [e for e in alt_elemente if e.element_uuid not in verborgen]
        neu_elemente = [e for e in neu_elemente if e.element_uuid not in verborgen]
    diff = freigabe_service.vergleiche_versionen(alt_elemente, neu_elemente, alt_kopf, neu_kopf)
    feldgruppen = [diff["kopf"], *(e["felder"] for e in diff["geaendert"])]
    for gruppe in feldgruppen:
        for feld in gruppe:
            if feld["feld"] == "wiedervorlage_am":
                for seite in ("alt", "neu"):
                    if feld[seite]:
                        feld[seite] = date.fromisoformat(feld[seite])
    namen = {e.element_uuid: e.titel for e in [*alt_elemente, *neu_elemente]}
    context = {**_kontext(db, user, plan), "vergleich": diff, "element_namen": namen,
               "von_label": f"Version {alt.versionsnummer}", "nach_label": nach_label}
    if not darf_bearbeiten(user):
        context.update(_lesekontext(db, user, plan))
    return templates.TemplateResponse(request, "katplan/vergleich.html", context)


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
            if feld == "wiedervorlage_am":
                plan.wiedervorlage_erinnert_am = None
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
    plan = _plan(db, plan_id, user)
    return _statuswechsel(request, db, user, plan_id,
                          "freigegeben" if plan.aktuelle_version_id else "entwurf", "reaktiviert")


_FARBEN = ("gruen", "gelb", "orange", "rot", "blau", "grau")


def _objekt[Objekt: (KatplanAbschnitt, KatplanElement, KatplanPhase)](
    db: Session, modell: type[Objekt], objekt_id: int, plan: KatplanPlan, user: User,
) -> Objekt:
    obj = db.query(modell).filter_by(id=objekt_id, plan_id=plan.id, org_id=user.org_id).one_or_none()
    if obj is None:
        raise HTTPException(404, "Nicht gefunden")
    return obj


def _editor_kontext(db: Session, plan: KatplanPlan) -> dict[str, Any]:
    phasen = db.query(KatplanPhase).filter_by(org_id=plan.org_id, plan_id=plan.id).order_by(
        KatplanPhase.sort, KatplanPhase.id,
    ).all()
    elemente = db.query(KatplanElement).filter_by(org_id=plan.org_id, plan_id=plan.id).all()
    zuordnungen = db.query(KatplanElementPhase).filter(
        KatplanElementPhase.org_id == plan.org_id,
        KatplanElementPhase.element_id.in_([e.id for e in elemente]),
        KatplanElementPhase.phase_id.in_([p.id for p in phasen]),
    ).all()
    return {"baum": abschnitte_baum(db, plan), "typen": ELEMENT_TYPEN, "phasen": phasen,
            "vorlagen": ELEMENT_VORLAGEN, "auftragsarten": AUFTRAGSART_LABEL,
            "ziel_plaene": db.query(KatplanPlan).filter(
                KatplanPlan.org_id == plan.org_id, KatplanPlan.status != "archiviert",
            ).order_by(KatplanPlan.titel).all(),
            "farben": _FARBEN, "abschnitte": db.query(KatplanAbschnitt).filter_by(
                org_id=plan.org_id, plan_id=plan.id,
            ).order_by(KatplanAbschnitt.sort, KatplanAbschnitt.id).all(),
            "element_daten": {e.id: json.loads(e.daten_json or "{}") for e in elemente},
            "element_tags": {e.id: json.loads(e.tags_json or "[]") for e in elemente},
            "element_phasen": {e.id: [p for p in phasen if any(
                z.element_id == e.id and z.phase_id == p.id for z in zuordnungen
            )] for e in elemente}}


def _antwort(request: Request, db: Session, user: User, plan: KatplanPlan,
             partial: str = "_leer.html", status: int = 200, **werte: Any):
    return templates.TemplateResponse(request, "katplan/_editor_antwort.html", {
        **_kontext(db, user, plan), "partial": partial, "baum_oob": True, **werte,
    }, status_code=status)


def _formular_element(db: Session, element: KatplanElement) -> dict[str, Any]:
    form = {feld: getattr(element, feld) or "" for feld in (
        "titel", "kennung", "abschnitt_id", "beschreibung_html", "prioritaet", "vertraulich",
        "gueltig_bis", "pruefen_am", "strasse", "hausnummer", "plz", "ort", "version",
        "gsl_vorlage", "gsl_auftragsart", "gsl_prioritaet", "gsl_voraussetzungen")}
    form["gsl_dauer_min"] = element.gsl_dauer_min if element.gsl_dauer_min is not None else ""
    form["gsl_ressourcentypen"] = ", ".join(json.loads(element.gsl_ressourcentypen_json or "[]"))
    form["tags"] = ", ".join(json.loads(element.tags_json or "[]"))
    form["phasen"] = [str(z.phase_id) for z in db.query(KatplanElementPhase).filter_by(
        org_id=element.org_id, element_id=element.id,
    ).all()]
    form["version"] = element.version
    return form


def _pflicht(wert: str, maximum: int, name: str) -> str:
    if not wert.strip() or len(wert.strip()) > maximum:
        raise ValueError(f"{name}: Bitte 1 bis {maximum} Zeichen eingeben.")
    return wert.strip()


def _abschnitt_id(db: Session, roh: str, plan: KatplanPlan, user: User) -> int | None:
    return _objekt(db, KatplanAbschnitt, int(roh), plan, user).id if roh else None


@router.get("/{plan_id}/bearbeiten")
def editor(plan_id: int, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    return templates.TemplateResponse(request, "katplan/editor.html", _kontext(db, user, plan))


@router.post("/{plan_id}/abschnitte")
def abschnitt_anlegen(plan_id: int, request: Request, titel: str = Form(""), parent_id: str = Form(""),
                      db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        titel = _pflicht(titel, 200, "Titel")
        parent = _abschnitt_id(db, parent_id, plan, user)
    except ValueError as exc:
        return _antwort(request, db, user, plan, status=422, fehler=str(exc))
    geschwister = db.query(KatplanAbschnitt).filter_by(
        org_id=user.org_id, plan_id=plan.id, parent_id=parent,
    ).all()
    abschnitt = KatplanAbschnitt(org_id=user.org_id, plan_id=plan.id, parent_id=parent,
                                titel=titel, sort=max((a.sort for a in geschwister), default=0) + 10)
    db.add(abschnitt)
    protokolliere(db, plan, "abschnitt", "angelegt", user, neu=titel)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_abschnitt_form.html", abschnitt=abschnitt)


@router.get("/{plan_id}/abschnitte/{aid}")
def abschnitt_form(plan_id: int, aid: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    return _antwort(request, db, user, plan, "_abschnitt_form.html",
                    abschnitt=_objekt(db, KatplanAbschnitt, aid, plan, user), baum_oob=False)


@router.post("/{plan_id}/abschnitte/{aid}")
def abschnitt_speichern(plan_id: int, aid: int, request: Request, titel: str = Form(""),
                        beschreibung_html: str = Form(""), db: Session = Depends(get_db),
                        user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    abschnitt = _objekt(db, KatplanAbschnitt, aid, plan, user)
    try:
        werte = {"titel": _pflicht(titel, 200, "Titel"), "beschreibung_html": bereinige_beschreibung(beschreibung_html)}
    except ValueError as exc:
        return _antwort(request, db, user, plan, "_abschnitt_form.html", status=422,
                        abschnitt=abschnitt, fehler=str(exc),
                        form={"titel": titel, "beschreibung_html": bereinige_beschreibung(beschreibung_html) or ""})
    for feld, wert in werte.items():
        alt = getattr(abschnitt, feld)
        if alt != wert:
            setattr(abschnitt, feld, wert)
            protokolliere(db, plan, "abschnitt", "geaendert", user, feld=feld, alt=alt, neu=wert)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_abschnitt_form.html", abschnitt=abschnitt, erfolg="Kapitel gespeichert.")


@router.post("/{plan_id}/abschnitte/{aid}/loeschen")
def abschnitt_loeschen(plan_id: int, aid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    loesche_abschnitt(db, plan, _objekt(db, KatplanAbschnitt, aid, plan, user), user)
    db.commit()
    return _antwort(request, db, user, plan)


@router.get("/{plan_id}/phasen")
def phasen_form(plan_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_katplan_bearbeiten)):
    return _antwort(request, db, user, _plan(db, plan_id, user), "_phasen.html", baum_oob=False)


def _phase_speichern(request: Request, db: Session, user: User, plan: KatplanPlan, phase: KatplanPhase | None):
    roh = run_async(request.form)
    form = {k: str(v) for k, v in roh.items()}
    try:
        name = _pflicht(form.get("name", ""), 120, "Name")
        farbe = form.get("farbe", "grau")
        if farbe not in _FARBEN:
            raise ValueError("Bitte eine gültige Farbe wählen.")
        werte = {"name": name, "beschreibung": form.get("beschreibung", ""),
                 "ausloeser": form.get("ausloeser", ""), "farbe": farbe, "sort": int(form.get("sort", "0"))}
    except ValueError as exc:
        return _antwort(request, db, user, plan, "_phasen.html", status=422,
                        fehler=str(exc), phase_form=form, phase_fehler_id=phase.id if phase else 0)
    neu = phase is None
    if phase is None:
        phase = KatplanPhase(org_id=user.org_id, plan_id=plan.id)
        db.add(phase)
    for feld, wert in werte.items():
        alt = getattr(phase, feld)
        if not neu and alt != wert:
            protokolliere(db, plan, "phase", "geaendert", user, feld=feld,
                          alt=str(alt) if alt is not None else None, neu=str(wert))
        setattr(phase, feld, wert)
    if neu:
        protokolliere(db, plan, "phase", "angelegt", user, neu=name)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_phasen.html", erfolg="Phase gespeichert.")


@router.post("/{plan_id}/phasen")
def phase_anlegen(plan_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_katplan_bearbeiten)):
    return _phase_speichern(request, db, user, _plan(db, plan_id, user), None)


@router.post("/{plan_id}/phasen/{pid}")
def phase_speichern(plan_id: int, pid: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    return _phase_speichern(request, db, user, plan, _objekt(db, KatplanPhase, pid, plan, user))


@router.post("/{plan_id}/phasen/{pid}/loeschen")
def phase_loeschen(plan_id: int, pid: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    phase = _objekt(db, KatplanPhase, pid, plan, user)
    for zuordnung in db.query(KatplanElementPhase).join(
        KatplanElement, KatplanElement.id == KatplanElementPhase.element_id,
    ).filter(KatplanElementPhase.org_id == user.org_id, KatplanElementPhase.phase_id == phase.id,
             KatplanElement.org_id == user.org_id, KatplanElement.plan_id == plan.id).all():
        element = _objekt(db, KatplanElement, zuordnung.element_id, plan, user)
        element.version += 1
        element.aktualisiert_von_id = user.id
        aktualisiere_element_suchtext(element)
        protokolliere(db, plan, "element", "geaendert", user, feld="phasen",
                      alt=phase.name, element_id=element.id)
        db.delete(zuordnung)
    db.delete(phase)
    protokolliere(db, plan, "phase", "geloescht", user, alt=phase.name)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_phasen.html")


@router.post("/{plan_id}/elemente")
def element_anlegen(plan_id: int, request: Request, typ: str = Form(""), titel: str = Form(""),
                    abschnitt_id: str = Form(""), vorlage: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    muster = ELEMENT_VORLAGEN.get(vorlage)
    try:
        if vorlage and muster is None:
            raise ValueError("Unbekannte Elementvorlage.")
        if muster:
            typ = muster.typ
            titel = titel.strip() or muster.titel
        titel = _pflicht(titel, 200, "Titel")
        if typ not in ELEMENT_TYPEN:
            raise ValueError("Unbekannter Elementtyp.")
        abschnitt = _abschnitt_id(db, abschnitt_id, plan, user)
    except ValueError as exc:
        return _antwort(request, db, user, plan, status=422, fehler=str(exc))
    geschwister = db.query(KatplanElement).filter_by(
        org_id=user.org_id, plan_id=plan.id, abschnitt_id=abschnitt,
    ).all()
    element = KatplanElement(org_id=user.org_id, plan_id=plan.id, typ=typ, titel=titel,
                            abschnitt_id=abschnitt, kennung=naechste_kennung(db, plan, typ),
                            sort=max((e.sort for e in geschwister), default=0) + 10,
                            herkunft="manual", daten_json=json.dumps(
                                bereinige_daten(typ, muster.daten) if muster else {}, ensure_ascii=False),
                            beschreibung_html=bereinige_beschreibung(muster.beschreibung_html) if muster else None,
                            tags_json="[]",
                            erstellt_von_id=user.id, aktualisiert_von_id=user.id)
    aktualisiere_element_suchtext(element)
    db.add(element)
    db.flush()
    protokolliere(db, plan, "element", "angelegt", user, element_id=element.id)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element),
                    daten=json.loads(element.daten_json or "{}"))


@router.get("/{plan_id}/elemente/{eid}")
def element_form(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element),
                    daten=json.loads(element.daten_json or "{}"))


@router.post("/{plan_id}/elemente/{eid}")
def element_speichern(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    roh = run_async(request.form)
    form: dict[str, Any] = {k: str(v) for k, v in roh.items()}
    form["beschreibung_html"] = bereinige_beschreibung(form.get("beschreibung_html", "")) or ""
    form["phasen"] = [str(v) for v in roh.getlist("phasen")]
    form["vertraulich"] = bool(roh.get("vertraulich"))
    form["gsl_vorlage"] = bool(roh.get("gsl_vorlage"))
    daten = json.loads(element.daten_json or "{}")
    try:
        titel = _pflicht(form.get("titel", ""), 200, "Titel")
        kennung = _pflicht(form.get("kennung", ""), 30, "Kennung")
        if db.query(KatplanElement).filter(
            KatplanElement.org_id == user.org_id, KatplanElement.plan_id == plan.id,
            KatplanElement.kennung == kennung, KatplanElement.id != element.id,
        ).first():
            raise ValueError("Diese Kennung ist im Plan bereits vergeben.")
        eingabe = json.loads(form.get("daten_json", "{}"))
        if not isinstance(eingabe, dict):
            raise ValueError("Daten müssen ein JSON-Objekt sein.")
        daten = bereinige_daten(element.typ, eingabe)
        prioritaet = int(form.get("prioritaet", "2"))
        if prioritaet not in (1, 2, 3):
            raise ValueError("Priorität muss 1, 2 oder 3 sein.")
        tags = [t.strip() for t in form.get("tags", "").split(",") if t.strip()]
        if len(tags) > 20 or any(len(t) > 50 for t in tags):
            raise ValueError("Höchstens 20 Tags mit je 50 Zeichen eingeben.")
        phase_ids = {int(v) for v in form["phasen"]}
        for pid in phase_ids:
            _objekt(db, KatplanPhase, pid, plan, user)
        werte = {"titel": titel, "kennung": kennung,
                 "abschnitt_id": _abschnitt_id(db, form.get("abschnitt_id", ""), plan, user),
                 "beschreibung_html": bereinige_beschreibung(form.get("beschreibung_html", "")),
                 "prioritaet": prioritaet, "vertraulich": form["vertraulich"],
                 "tags_json": json.dumps(tags, ensure_ascii=False),
                 "daten_json": json.dumps(daten, ensure_ascii=False)}
        for feld in ("gueltig_bis", "pruefen_am"):
            werte[feld] = date.fromisoformat(form[feld]) if form.get(feld) else None
        for feld, maximum in (("strasse", 200), ("hausnummer", 20), ("plz", 10), ("ort", 100)):
            wert = form.get(feld, "").strip()
            if len(wert) > maximum:
                raise ValueError(f"{feld}: höchstens {maximum} Zeichen.")
            werte[feld] = wert or None
        auftragsart = form.get("gsl_auftragsart", "").strip() or None
        if auftragsart is not None and auftragsart not in AUFTRAGSART_WERTE:
            raise ValueError("Ungültige GSL-Auftragsart.")
        gsl_prioritaet = int(form["gsl_prioritaet"]) if form.get("gsl_prioritaet") else None
        if gsl_prioritaet is not None and gsl_prioritaet not in (1, 2, 3):
            raise ValueError("GSL-Priorität muss 1, 2 oder 3 sein.")
        dauer = int(form["gsl_dauer_min"]) if form.get("gsl_dauer_min") else None
        if dauer is not None and not 0 <= dauer <= 10080:
            raise ValueError("GSL-Dauer muss zwischen 0 und 10080 Minuten liegen.")
        ressourcen = [r.strip() for r in form.get("gsl_ressourcentypen", "").split(",") if r.strip()]
        if len(ressourcen) > 20 or any(len(r) > 50 for r in ressourcen):
            raise ValueError("Höchstens 20 Ressourcentypen mit je 50 Zeichen eingeben.")
        voraussetzungen = form.get("gsl_voraussetzungen", "").strip()
        if len(voraussetzungen) > 2000:
            raise ValueError("GSL-Voraussetzungen: höchstens 2000 Zeichen.")
        werte.update(gsl_vorlage=form["gsl_vorlage"], gsl_auftragsart=auftragsart,
                     gsl_prioritaet=gsl_prioritaet, gsl_dauer_min=dauer,
                     gsl_ressourcentypen_json=json.dumps(ressourcen, ensure_ascii=False) if ressourcen else None,
                     gsl_voraussetzungen=voraussetzungen or None)
        pruefe_version(element, int(form.get("version", "-1")))
    except (ValueError, KatplanKonflikt) as exc:
        return _antwort(request, db, user, plan, "_element_form.html",
                        status=409 if isinstance(exc, KatplanKonflikt) else 422,
                        element=element, aktiv_element_id=element.id, form=form, daten=daten,
                        fehler=str(exc))
    for feld, wert in werte.items():
        alt = getattr(element, feld)
        if alt != wert:
            setattr(element, feld, wert)
            protokolliere(db, plan, "element", "geaendert", user,
                          feld="daten" if feld == "daten_json" else feld,
                          alt=str(alt) if alt is not None else None,
                          neu=str(wert) if wert is not None else None, element_id=element.id)
    zuordnungen = db.query(KatplanElementPhase).filter_by(org_id=user.org_id, element_id=element.id).all()
    alte_ids = {z.phase_id for z in zuordnungen}
    if alte_ids != phase_ids:
        for z in zuordnungen:
            if z.phase_id not in phase_ids:
                db.delete(z)
        for pid in phase_ids - alte_ids:
            db.add(KatplanElementPhase(org_id=user.org_id, element_id=element.id, phase_id=pid))
        protokolliere(db, plan, "element", "geaendert", user, feld="phasen",
                      alt=json.dumps(sorted(alte_ids)), neu=json.dumps(sorted(phase_ids)), element_id=element.id)
    element.version += 1
    element.aktualisiert_am = datetime.now(UTC).replace(tzinfo=None)
    element.aktualisiert_von_id = user.id
    aktualisiere_element_suchtext(element)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element), daten=daten,
                    erfolg="Element gespeichert.")


@router.post("/{plan_id}/elemente/{eid}/loeschen")
def element_loeschen(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    for z in db.query(KatplanElementPhase).filter_by(org_id=user.org_id, element_id=element.id).all():
        db.delete(z)
    protokolliere(db, plan, "element", "geloescht", user, element_id=element.id, alt=element.titel)
    db.delete(element)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan)


class AbschnittSortierung(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int
    parent_id: int | None
    sort: int


class ElementSortierung(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int
    abschnitt_id: int | None
    sort: int


class BaumSortierung(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    abschnitte: list[AbschnittSortierung]
    elemente: list[ElementSortierung]


@router.post("/{plan_id}/sortierung")
def sortierung(plan_id: int, request: Request, daten: BaumSortierung, db: Session = Depends(get_db),
               user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        sortiere_baum(db, plan, user, [a.model_dump() for a in daten.abschnitte],
                      [e.model_dump() for e in daten.elemente])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return templates.TemplateResponse(request, "katplan/_baum.html", {
        **_kontext(db, user, plan), "sortierung_status": True})


@router.post("/{plan_id}/elemente/{eid}/verschieben")
def element_verschieben(plan_id: int, eid: int, request: Request, richtung: str = Form(""),
                        db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    try:
        verschiebe_nachbar(db, plan, user, element, richtung)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element),
                    daten=json.loads(element.daten_json or "{}"))


@router.post("/{plan_id}/abschnitte/{aid}/verschieben")
def abschnitt_verschieben(plan_id: int, aid: int, request: Request, richtung: str = Form(""),
                          db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    abschnitt = _objekt(db, KatplanAbschnitt, aid, plan, user)
    try:
        verschiebe_nachbar(db, plan, user, abschnitt, richtung)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _antwort(request, db, user, plan, "_abschnitt_form.html", abschnitt=abschnitt)


@router.post("/{plan_id}/elemente/{eid}/duplizieren")
def element_duplizieren(plan_id: int, eid: int, request: Request, ziel_plan_id: int | None = Form(None),
                        db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    quelle = _objekt(db, KatplanElement, eid, plan, user)
    ziel = _plan(db, ziel_plan_id if ziel_plan_id is not None else plan.id, user)
    if ziel.status == "archiviert":
        raise HTTPException(422, "In archivierte Pläne kann nicht dupliziert werden.")
    gleicher_plan = ziel.id == plan.id
    inhalt = {feld: getattr(quelle, feld) for feld in (
        "typ", "beschreibung_html", "daten_json", "prioritaet", "status", "tags_json",
        "gueltig_bis", "pruefen_am", "vertraulich", "strasse", "hausnummer", "plz", "ort",
        "lat", "lng", "geometry_json", "geo_status", "herkunft", "quelle_dokument_id", "quelle_seiten",
        "gsl_vorlage", "gsl_auftragsart", "gsl_prioritaet", "gsl_ressourcentypen_json",
        "gsl_dauer_min", "gsl_voraussetzungen",
    )}
    abschnitt_id = quelle.abschnitt_id if gleicher_plan else None
    sort = max((e.sort for e in db.query(KatplanElement).filter_by(
        org_id=user.org_id, plan_id=ziel.id, abschnitt_id=abschnitt_id,
    ).all()), default=0) + 10
    kopie = KatplanElement(org_id=user.org_id, plan_id=ziel.id, abschnitt_id=abschnitt_id,
                          kennung=naechste_kennung(db, ziel, quelle.typ),
                          titel=(quelle.titel[:192] + " (Kopie)") if gleicher_plan else quelle.titel,
                          version=0, sort=sort, erstellt_von_id=user.id, aktualisiert_von_id=user.id, **inhalt)
    aktualisiere_element_suchtext(kopie)
    db.add(kopie)
    db.flush()
    if gleicher_plan:
        for z in db.query(KatplanElementPhase).join(
            KatplanPhase, KatplanPhase.id == KatplanElementPhase.phase_id,
        ).filter(KatplanElementPhase.org_id == user.org_id, KatplanElementPhase.element_id == quelle.id,
                 KatplanPhase.org_id == user.org_id, KatplanPhase.plan_id == plan.id).all():
            db.add(KatplanElementPhase(org_id=user.org_id, element_id=kopie.id, phase_id=z.phase_id))
    verweis = f"Quelle: Plan {plan.id}, Element {quelle.uuid}; Ziel: Plan {ziel.id}, Element {kopie.uuid}"
    protokolliere(db, ziel, "element", "dupliziert", user, neu=verweis, element_id=kopie.id)
    if not gleicher_plan:
        protokolliere(db, plan, "element", "dupliziert", user, neu=verweis, element_id=quelle.id)
    markiere_ueberarbeitung(db, ziel, user)
    db.commit()
    if gleicher_plan:
        return _antwort(request, db, user, plan, "_element_form.html", element=kopie,
                        aktiv_element_id=kopie.id, form=_formular_element(db, kopie),
                        daten=json.loads(kopie.daten_json or "{}"))
    return _antwort(request, db, user, plan, "_dupliziert.html", ziel_plan=ziel, baum_oob=False)


def _workflow(request: Request, db: Session, user: User, plan_id: int, aktion: str, text: str):
    plan = _plan(db, plan_id, user)
    try:
        getattr(freigabe_service, aktion)(db, plan, user, text)
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "katplan/_kopf.html", {
            **_kontext(db, user, plan), "fehler": str(exc)}, status_code=422)
    db.commit()
    return templates.TemplateResponse(request, "katplan/_kopf.html", {
        **_kontext(db, user, plan), "protokoll_oob": True})


@router.post("/{plan_id}/einreichen")
def einreichen(plan_id: int, request: Request, kommentar: str = Form(""),
               db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    return _workflow(request, db, user, plan_id, "einreichen", kommentar)


@router.post("/{plan_id}/zurueckweisen")
def zurueckweisen(plan_id: int, request: Request, kommentar: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_katplan_freigeben)):
    return _workflow(request, db, user, plan_id, "zurueckweisen", kommentar)


@router.post("/{plan_id}/freigeben")
def freigeben(plan_id: int, request: Request, aenderungsnotiz: str = Form(""),
              db: Session = Depends(get_db), user: User = Depends(require_katplan_freigeben)):
    return _workflow(request, db, user, plan_id, "freigeben", aenderungsnotiz)
