"""UI fuer organisationsweite zentrale Kontakte."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.kontakt import KONTAKT_TYP_PERSON
from app.models.user import User
from app.services import kontakt_service

router = APIRouter(prefix="/kontakte", tags=["kontakte"])
_LESE_ROLLEN = (
    "readonly",
    "recorder",
    "breathing_supervisor",
    "incident_leader",
    "fahrtenbuch_admin",
    "objekt_verwalter",
    "kontakt_verwalter",
)
_SCHREIB_ROLLEN = ("kontakt_verwalter", "objekt_verwalter")


def require_kontakte_enabled(request: Request) -> None:
    """Guard: HTTP 404 wenn das Kontakte-Modul nicht effektiv aktiv ist."""
    if not getattr(request.state, "kontakte_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _form_daten(
    typ: str,
    anzeigename: str,
    vorname: str,
    nachname: str,
    funktion: str,
    organisation: str,
    email: str,
    erreichbarkeit: str,
    notizen: str,
) -> dict[str, str]:
    return {
        "typ": typ,
        "anzeigename": anzeigename,
        "vorname": vorname,
        "nachname": nachname,
        "funktion": funktion,
        "organisation": organisation,
        "email": email,
        "erreichbarkeit": erreichbarkeit,
        "notizen": notizen,
    }


def _org_id(user: User) -> int:
    if user.org_id is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewaehlt")
    return user.org_id


def _telefone(
    nummer: list[str], telefon_label: list[str], bevorzugt: list[str], sms_eignung: list[str]
) -> list[dict[str, object]]:
    bevorzugt_set, sms_set = set(bevorzugt), set(sms_eignung)
    return [
        {
            "nummer": value,
            "label": telefon_label[index] if index < len(telefon_label) else "",
            "bevorzugt": str(index) in bevorzugt_set,
            "sms_eignung": str(index) in sms_set,
        }
        for index, value in enumerate(nummer)
    ]


def _seite(
    request: Request,
    db: Session,
    user: User,
    *,
    selected_id: int | None = None,
    q: str = "",
    typ: str = "all",
    kategorie: str = "",
    page: int = 1,
    form_data: dict[str, object] | None = None,
    error: str | None = None,
):
    kategorie_id = int(kategorie) if kategorie.isdigit() else None
    kontakte, total = kontakt_service.list_kontakte(db, q=q, typ=typ, kategorie_id=kategorie_id, page=page)
    selected = kontakt_service.get_kontakt(db, selected_id) if selected_id else (kontakte[0] if kontakte else None)
    return templates.TemplateResponse(
        request,
        "kontakte/liste.html",
        {
            "user": user,
            "kontakte": kontakte,
            "selected": selected,
            "kategorien": kontakt_service.list_kategorien(db),
            "q": q,
            "typ": typ,
            "kategorie_id": kategorie_id,
            "page": page,
            "total": total,
            "pro_seite": kontakt_service.PRO_SEITE,
            "form_data": form_data,
            "error": error,
        },
    )


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
def liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
    q: str = "",
    typ: str = "all",
    kategorie: str = "",
    page: int = 1,
):
    return _seite(request, db, user, q=q, typ=typ, kategorie=kategorie, page=page)


@router.get("/liste", response_class=HTMLResponse)
def liste_partial(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
    q: str = "",
    typ: str = "all",
    kategorie: str = "",
    page: int = 1,
):
    kategorie_id = int(kategorie) if kategorie.isdigit() else None
    kontakte, total = kontakt_service.list_kontakte(db, q=q, typ=typ, kategorie_id=kategorie_id, page=page)
    return templates.TemplateResponse(
        request,
        "kontakte/_liste.html",
        {
            "kontakte": kontakte,
            "selected": None,
            "q": q,
            "typ": typ,
            "kategorie_id": kategorie_id,
            "page": page,
            "total": total,
            "pro_seite": kontakt_service.PRO_SEITE,
        },
    )


@router.get("/{kontakt_id}", response_class=HTMLResponse)
def detail(
    request: Request,
    kontakt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    if kontakt_service.get_kontakt(db, kontakt_id) is None:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request, "kontakte/_detail.html", {"kontakt": kontakt_service.get_kontakt(db, kontakt_id), "user": user}
        )
    return _seite(request, db, user, selected_id=kontakt_id)


@router.post("/", response_class=HTMLResponse)
def create(
    request: Request,
    typ: str = Form(KONTAKT_TYP_PERSON),
    anzeigename: str = Form(""),
    vorname: str = Form(""),
    nachname: str = Form(""),
    funktion: str = Form(""),
    organisation: str = Form(""),
    email: str = Form(""),
    erreichbarkeit: str = Form(""),
    notizen: str = Form(""),
    nummer: list[str] = Form([]),
    telefon_label: list[str] = Form([]),
    bevorzugt: list[str] = Form([]),
    sms_eignung: list[str] = Form([]),
    kategorien: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    daten = _form_daten(typ, anzeigename, vorname, nachname, funktion, organisation, email, erreichbarkeit, notizen)
    try:
        kontakt = kontakt_service.create_kontakt(
            db,
            daten,
            _telefone(nummer, telefon_label, bevorzugt, sms_eignung),
            kategorien.split(","),
            org_id=_org_id(user),
            user_id=user.id,
        )
    except ValueError as exc:
        return _seite(
            request,
            db,
            user,
            form_data={
                **daten,
                "nummer": nummer,
                "telefon_label": telefon_label,
                "bevorzugt": bevorzugt,
                "sms_eignung": sms_eignung,
                "kategorien": kategorien,
            },
            error=str(exc),
        )
    return RedirectResponse(f"/kontakte/{kontakt.id}", status_code=303)


@router.post("/{kontakt_id}", response_class=HTMLResponse)
def update(
    request: Request,
    kontakt_id: int,
    version: int = Form(...),
    typ: str = Form(KONTAKT_TYP_PERSON),
    anzeigename: str = Form(""),
    vorname: str = Form(""),
    nachname: str = Form(""),
    funktion: str = Form(""),
    organisation: str = Form(""),
    email: str = Form(""),
    erreichbarkeit: str = Form(""),
    notizen: str = Form(""),
    nummer: list[str] = Form([]),
    telefon_label: list[str] = Form([]),
    bevorzugt: list[str] = Form([]),
    sms_eignung: list[str] = Form([]),
    kategorien: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    daten = _form_daten(typ, anzeigename, vorname, nachname, funktion, organisation, email, erreichbarkeit, notizen)
    form_data = {
        **daten,
        "nummer": nummer,
        "telefon_label": telefon_label,
        "bevorzugt": bevorzugt,
        "sms_eignung": sms_eignung,
        "kategorien": kategorien,
        "version": version,
        "id": kontakt_id,
    }
    try:
        kontakt_service.update_kontakt(
            db,
            kontakt_id,
            daten,
            _telefone(nummer, telefon_label, bevorzugt, sms_eignung),
            kategorien.split(","),
            version=version,
            org_id=_org_id(user),
            user_id=user.id,
        )
    except kontakt_service.KontaktKonflikt:
        return _seite(
            request,
            db,
            user,
            selected_id=kontakt_id,
            form_data=form_data,
            error="Der Kontakt wurde inzwischen geaendert. Ihre Eingaben wurden nicht gespeichert.",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    except ValueError as exc:
        return _seite(request, db, user, selected_id=kontakt_id, form_data=form_data, error=str(exc))
    return RedirectResponse(f"/kontakte/{kontakt_id}", status_code=303)


@router.post("/{kontakt_id}/archivieren")
def archive(
    kontakt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    try:
        kontakt_service.archive_kontakt(db, kontakt_id, user_id=user.id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    return RedirectResponse("/kontakte", status_code=303)
