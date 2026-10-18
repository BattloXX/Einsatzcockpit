"""Authentifizierte Druckansicht im bestehenden Kontaktmodul."""

from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.permissions import require_kontakt_directory, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept
from app.models.user import User
from app.routers.ui_kontakt import _LESE_ROLLEN, _SCHREIB_ROLLEN, _org_id, require_kontakte_enabled
from app.services import kontakt_druck_service as service

router = APIRouter(
    prefix="/kontakte",
    tags=["kontakte"],
    dependencies=[Depends(require_kontakt_directory), Depends(require_kontakte_enabled)],
)
PRIVATE = {"Cache-Control": "private, no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}


def _optionen(params, db, user):
    try:
        if params.get("einstellungen"):
            return service.DruckEinstellungen.model_validate_json(params["einstellungen"])
        if params.get("angepasst"):
            values = {
                key: params.get(key, "")
                for key in (
                    "q",
                    "typ",
                    "organisation",
                    "funktion",
                    "prioritaet",
                    "layout",
                    "papier",
                    "ausrichtung",
                    "sortierung",
                )
            }
            values.update(
                {
                    key: params.get(key) == "1"
                    for key in (
                        "nur_aktiv",
                        "nur_freigegeben",
                        "nur_einsatzrelevant",
                        "objektkontakte_ausschliessen",
                        "ohne_telefon_ausschliessen",
                        "gruppieren",
                        "logo_anzeigen",
                    )
                }
            )
            values["kategorien"] = [int(k) for k in params.getlist("kategorien")]
            values["felder"] = params.getlist("felder")
            return service.DruckEinstellungen.model_validate(values)
        opts = service.profil_laden(db, _org_id(user), user.id, params.get("profil", "einsatz"))
        values = opts.model_dump()
        if params.get("q"):
            values["q"] = params["q"]
        if params.get("typ") in ("person", "stelle"):
            values["typ"] = params["typ"]
        if params.get("kategorie"):
            values["kategorien"] = [int(params["kategorie"])]
        return service.DruckEinstellungen.model_validate(values)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from None


def _daten(db, user, opts):
    try:
        return service.auswahl(db, _org_id(user), opts)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/drucken", response_class=HTMLResponse)
def dialog(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(*_LESE_ROLLEN))):
    opts = _optionen(request.query_params, db, user)
    data = _daten(db, user, opts)
    cats = service.kategorien(db, _org_id(user))
    selected = (
        opts.kategorien
        if opts.kategorien is not None
        else [k.id for k in cats if k.aktiv and k.druck_standard and not k.objektkontakte]
    )
    query = urlencode({"einstellungen": opts.model_dump_json()})
    return templates.TemplateResponse(
        request,
        "kontakte/drucken.html",
        {
            "user": user,
            "opts": opts,
            "kategorien": cats,
            "selected": selected,
            "profile": service.profile(db, _org_id(user), user.id),
            "profil_id": "" if request.query_params.get("angepasst") else request.query_params.get("profil", "einsatz"),
            "felder": service.FELDER,
            "query": query,
            **data,
        },
        headers=PRIVATE,
    )


@router.get("/druckvorschau", response_class=HTMLResponse)
@router.get("/druck.pdf")
def ausgabe(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(*_LESE_ROLLEN))):
    opts = _optionen(request.query_params, db, user)
    data = _daten(db, user, opts)
    org = db.get(FireDept, _org_id(user))
    if org is None:
        raise HTTPException(404, "Organisation nicht gefunden")
    now = datetime.now(ZoneInfo(org.timezone or "Europe/Vienna"))
    logo_data_uri = service.organisationslogo_data_uri(db, org) if opts.logo_anzeigen else ""
    html = templates.env.get_template("kontakte/druckausgabe.html").render(
        opts=opts, org=org, jetzt=now, logo_data_uri=logo_data_uri, **data
    )
    if request.url.path.endswith(".pdf"):
        from app.services.pdf_service import render_kontaktverzeichnis_pdf

        return Response(
            render_kontaktverzeichnis_pdf(html, logo_data_uri=logo_data_uri),
            media_type="application/pdf",
            headers={**PRIVATE, "Content-Disposition": 'attachment; filename="einsatzkontakte.pdf"'},
        )
    return HTMLResponse(html, headers=PRIVATE)


@router.post("/druckprofile")
async def profil_speichern(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
):
    form = await request.form()
    opts = _optionen(form, db, user)
    _daten(db, user, opts)
    try:
        profil = service.profil_speichern(db, _org_id(user), user.id, name, opts)
        db.commit()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return RedirectResponse(f"/kontakte/drucken?profil={profil.id}", status_code=303)


@router.post("/druckprofile/{profil_id}/loeschen")
def profil_loeschen(profil_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(*_LESE_ROLLEN))):
    try:
        service.profil_loeschen(db, _org_id(user), user.id, profil_id)
        db.commit()
    except ValueError:
        raise HTTPException(404, "Druckprofil nicht gefunden") from None
    return RedirectResponse("/kontakte/drucken", status_code=303)


@router.get("/druckkategorien", response_class=HTMLResponse)
def kategorien(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(*_SCHREIB_ROLLEN))):
    return templates.TemplateResponse(
        request, "kontakte/kategorien.html", {"user": user, "kategorien": service.kategorien(db, _org_id(user))}
    )


@router.post("/druckkategorien/standard")
def standard(db: Session = Depends(get_db), user: User = Depends(require_role(*_SCHREIB_ROLLEN))):
    service.standardkategorien_anlegen(db, _org_id(user))
    db.commit()
    return RedirectResponse("/kontakte/druckkategorien", status_code=303)


@router.post("/druckkategorien")
def kategorie_speichern(
    name: str = Form(...),
    kategorie_id: int | None = Form(None),
    aktiv: str = Form(""),
    sortierung: int = Form(0),
    druck_standard: str = Form(""),
    objektkontakte: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
):
    try:
        service.kategorie_speichern(
            db,
            _org_id(user),
            name=name,
            kategorie_id=kategorie_id,
            aktiv=aktiv == "1",
            sortierung=sortierung,
            druck_standard=druck_standard == "1",
            objektkontakte=objektkontakte == "1",
        )
        db.commit()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return RedirectResponse("/kontakte/druckkategorien", status_code=303)
