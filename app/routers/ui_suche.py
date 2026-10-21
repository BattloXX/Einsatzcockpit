"""Zentrale Suche mit Session-Auth und CSRF fuer HTML und JSON."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.permissions import ROLES, require_role
from app.core.rate_limit import limiter
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.user import User
from app.services.katplan_suche_service import markiere
from app.services.kontakt_access_service import kontakt_zugriff_fuer_request
from app.services.suche.abfrage import suche
from app.services.suche.live import pruefe_kontext
from app.services.suche.vertrag import SuchAnfrage, SuchErgebnis

router = APIRouter(tags=["Suche"])
# require_role prueft Mitgliedschaft, keine Hierarchie: hoehere Rollen explizit.
_leser = require_role(*(rolle for rolle, rang in ROLES.items() if rang >= ROLES["readonly"]))
TYP_LABELS = {
    "objekt": "Objekt", "objekt_dokument_seite": "Objektdokument",
    "katplan_element": "Planelement", "katplan_seite": "Planseite",
    "gsl_stelle": "Einsatzstelle", "gsl_meldung": "Meldung", "gsl_einheit": "Einheit",
    "einsatz": "Einsatz", "einsatz_meldung": "Einsatzmeldung",
    "strassensperre": "Straßensperre", "wasserstelle": "Wasserstelle", "kontakt": "Kontakt",
}
FILTER_GRUPPEN = (
    ("Kontakte", ("kontakt",)),
    ("Objekte", ("objekt", "objekt_dokument_seite")),
    ("Pläne", ("katplan_element", "katplan_seite")),
    ("Einsätze", ("einsatz", "einsatz_meldung")),
    ("GSL", ("gsl_stelle", "gsl_meldung", "gsl_einheit")),
    ("Sperren", ("strassensperre",)), ("Wasserstellen", ("wasserstelle",)),
)


def require_suche(request: Request) -> None:
    if not getattr(request.state, "suche_aktiv", False):
        raise HTTPException(status_code=404, detail="Suche nicht verfügbar")


def _benutzer_key(request: Request) -> str:
    return f"suche:{request.state.user.id}"


def _pruefe_kontext(db: Session, user: User, anfrage: SuchAnfrage) -> None:
    try:
        pruefe_kontext(db, user, anfrage)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Suchkontext nicht verfügbar") from None


def _partial(request: Request, user: User, q: str, ergebnis: SuchErgebnis | None = None,
             fehler: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "suche/_treffer.html", {
        "user": user, "q": q, "ergebnis": ergebnis or SuchErgebnis(), "fehler": fehler,
        "typ_labels": TYP_LABELS, "markiere": markiere,
    })


@router.get("/suche", response_class=HTMLResponse, dependencies=[Depends(require_suche)])
def index(request: Request, kontext: Literal["global", "einsatz", "gsl"] = "global",
          kontext_id: int | None = None, typ: str | None = None,
          user: User = Depends(_leser), db: Session = Depends(get_db)):
    try:
        anfrage = SuchAnfrage(q="Kontextprüfung", kontext=kontext, kontext_id=kontext_id,
                              typen=[typ] if typ else [])
    except ValidationError:
        raise HTTPException(status_code=422, detail="Ungültiger Suchkontext oder Typ") from None
    _pruefe_kontext(db, user, anfrage)
    kontext_name = ""
    if kontext == "gsl":
        lage = db.get(MajorIncident, kontext_id)
        kontext_name = lage.name if lage else ""
    elif kontext == "einsatz":
        incident = db.get(Incident, kontext_id)
        kontext_name = f"#{kontext_id} {incident.alarm_type_code}" if incident else ""
    kontakt_zugriff = kontakt_zugriff_fuer_request(db, request, anfrage)
    filter_gruppen = [g for g in FILTER_GRUPPEN if g[0] != "Kontakte" or kontakt_zugriff != "keiner"]
    return templates.TemplateResponse(request, "suche/index.html", {
        "user": user, "q": "", "ergebnis": SuchErgebnis(), "fehler": None,
        "kontext": kontext, "kontext_id": kontext_id, "kontext_name": kontext_name,
        "typ": typ, "filter_gruppen": filter_gruppen, "typ_labels": TYP_LABELS, "markiere": markiere,
    })


@router.post("/suche/treffer", response_class=HTMLResponse, dependencies=[Depends(require_suche)])
@(limiter.limit("60/minute", key_func=_benutzer_key) if limiter else lambda f: f)
def treffer(request: Request, user: User = Depends(_leser), db: Session = Depends(get_db)):
    form = run_async(request.form)
    q = str(form.get("q", ""))
    daten = {key: form[key] for key in (
        "kontext", "kontext_id", "inkl_erledigt", "ganze_org", "lat", "lng", "radius_m",
    ) if key in form and form[key] != ""}
    typen = []
    for gruppe in form.getlist("typen"):
        typen.extend(str(gruppe).split(","))
    try:
        anfrage = SuchAnfrage.model_validate({**daten, "q": q if q.strip() else "Kontextprüfung", "typen": typen})
        _pruefe_kontext(db, user, anfrage)
        if not q.strip():
            return _partial(request, user, "")
    except ValidationError:
        return _partial(request, user, q, fehler=(
            "Bitte Suchtext mit 1 bis 500 Zeichen, gültige Filter und einen gültigen Kontext angeben. "
            "Koordinaten und Radius (1 bis 5000 m) müssen gemeinsam gesetzt sein."
        ))
    try:
        ergebnis = suche(db, user, anfrage, kontakt_zugriff=kontakt_zugriff_fuer_request(db, request, anfrage))
    except PermissionError:
        raise HTTPException(status_code=404, detail="Suchkontext nicht verfügbar") from None
    return _partial(request, user, q, ergebnis)


@router.post("/api/suche", dependencies=[Depends(require_suche)])
@(limiter.limit("60/minute", key_func=_benutzer_key) if limiter else lambda f: f)
def api_suche(request: Request, anfrage: SuchAnfrage,
              user: User = Depends(_leser), db: Session = Depends(get_db)):
    try:
        kontakt_zugriff = kontakt_zugriff_fuer_request(db, request, anfrage)
        return suche(db, user, anfrage, kontakt_zugriff=kontakt_zugriff).model_dump(mode="json")
    except PermissionError:
        raise HTTPException(status_code=404, detail="Suchkontext nicht verfügbar") from None


# Die Administration bleibt auch bei ausgeschaltetem Org-Schalter erreichbar.
def _admin_org(user: User) -> int:
    from app.config import settings

    if not settings.SEARCH_ENABLED or user.org_id is None:
        raise HTTPException(status_code=404, detail="Suchadministration nicht verfügbar")
    return user.org_id


def _admin_status(request: Request, user: User, db: Session, *, partial: bool = False) -> HTMLResponse:
    from app.services.suche.admin import indexstatus

    org_id = _admin_org(user)
    return templates.TemplateResponse(request, "suche/_admin_status.html" if partial else "suche/admin.html", {
        "user": user, **indexstatus(db, org_id),
    })


@router.get("/einstellungen/suche", response_class=HTMLResponse)
def admin_index(request: Request, user: User = Depends(require_role("org_admin", "admin")),
                db: Session = Depends(get_db)):
    return _admin_status(request, user, db)


@router.post("/einstellungen/suche/neuaufbau", response_class=HTMLResponse)
def admin_neuaufbau(request: Request, user: User = Depends(require_role("org_admin", "admin")),
                    db: Session = Depends(get_db)):
    from app.services.suche.outbox import plane_neuaufbau

    plane_neuaufbau(db, _admin_org(user))
    db.commit()
    return _admin_status(request, user, db, partial=True)


@router.post("/einstellungen/suche/fehler-zuruecksetzen", response_class=HTMLResponse)
def admin_fehler_zuruecksetzen(request: Request, user: User = Depends(require_role("org_admin", "admin")),
                              db: Session = Depends(get_db)):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.suche import SearchOutbox

    org_id = _admin_org(user)
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    for job in db.scalars(select(SearchOutbox).where(
        SearchOutbox.org_id == org_id, SearchOutbox.status == "failed",
    )).all():
        job.status = "retry"
        job.versuche = 0
        job.naechster_versuch_am = jetzt
        job.gesperrt_bis = None
    db.commit()
    return _admin_status(request, user, db, partial=True)
