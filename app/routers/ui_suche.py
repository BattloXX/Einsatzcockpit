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
    "strassensperre": "Straßensperre", "wasserstelle": "Wasserstelle",
}
FILTER_GRUPPEN = (
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
    return templates.TemplateResponse(request, "suche/index.html", {
        "user": user, "q": "", "ergebnis": SuchErgebnis(), "fehler": None,
        "kontext": kontext, "kontext_id": kontext_id, "kontext_name": kontext_name,
        "typ": typ, "filter_gruppen": FILTER_GRUPPEN, "typ_labels": TYP_LABELS, "markiere": markiere,
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
        ergebnis = suche(db, user, anfrage)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Suchkontext nicht verfügbar") from None
    return _partial(request, user, q, ergebnis)


@router.post("/api/suche", dependencies=[Depends(require_suche)])
@(limiter.limit("60/minute", key_func=_benutzer_key) if limiter else lambda f: f)
def api_suche(request: Request, anfrage: SuchAnfrage,
              user: User = Depends(_leser), db: Session = Depends(get_db)):
    try:
        return suche(db, user, anfrage).model_dump(mode="json")
    except PermissionError:
        raise HTTPException(status_code=404, detail="Suchkontext nicht verfügbar") from None
