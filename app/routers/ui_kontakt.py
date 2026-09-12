"""UI fuer organisationsweite zentrale Kontakte."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.permissions import can_send_manual_sms, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.kontakt import KONTAKT_TYP_PERSON, Kontakt, KontaktTelefon
from app.models.sms import SmsLog
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
    duplicate_candidates: list | None = None,
    merge_konflikte: list[str] | None = None,
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
            "kontakt": selected,
            "kategorien": kontakt_service.list_kategorien(db),
            "q": q,
            "typ": typ,
            "kategorie_id": kategorie_id,
            "page": page,
            "total": total,
            "pro_seite": kontakt_service.PRO_SEITE,
            "form_data": form_data,
            "error": error,
            "duplicate_candidates": duplicate_candidates or [],
            "merge_konflikte": merge_konflikte or [],
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


@router.get("/duplikatcheck", response_class=HTMLResponse)
def duplikate_pruefen(
    request: Request,
    anzeigename: str = "",
    organisation: str = "",
    email: str = "",
    nummer: list[str] | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    kandidaten = kontakt_service.find_duplicate_candidates(
        db, anzeigename=anzeigename, organisation=organisation, email=email, telefone=nummer
    )
    return templates.TemplateResponse(
        request, "kontakte/_duplikate.html", {"kandidaten": kandidaten, "form": None, "user": user}
    )


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db), user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
               _guard: None = Depends(require_kontakte_enabled)):
    from app.services.kontakt_transfer_service import export_csv as build_export
    return Response(build_export(db, _org_id(user)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=kontakte.csv"})


@router.get("/export.xlsx")
def export_xlsx(db: Session = Depends(get_db), user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
                _guard: None = Depends(require_kontakte_enabled)):
    from app.services.kontakt_transfer_service import export_xlsx as build_export
    return Response(build_export(db, _org_id(user)),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=kontakte.xlsx"})


@router.post("/import/vorschau")
async def import_vorschau(datei: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
                          _guard: None = Depends(require_kontakte_enabled)):
    from app.services.kontakt_transfer_service import parse_import, preview_import, save_preview
    try:
        rows = parse_import(await datei.read(), datei.filename or "")
        preview = preview_import(db, _org_id(user), rows)
    except ValueError as exc:
        return RedirectResponse(f"/kontakte?import_error={exc}", status_code=303)
    entry = save_preview(db, _org_id(user), user.id, preview)
    return RedirectResponse(f"/kontakte/import/{entry.id}", status_code=303)


@router.get("/import/{preview_id}", response_class=HTMLResponse)
def import_anzeigen(preview_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
                    _guard: None = Depends(require_kontakte_enabled)):
    from app.services.kontakt_transfer_service import load_preview
    try:
        _entry, preview = load_preview(db, _org_id(user), user.id, preview_id)
    except LookupError:
        raise HTTPException(404, "Importvorschau nicht gefunden") from None
    return templates.TemplateResponse(request, "kontakte/import_vorschau.html", {"user": user, "preview": preview, "preview_id": preview_id})


@router.post("/import/{preview_id}/uebernehmen")
def import_uebernehmen(preview_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
                       _guard: None = Depends(require_kontakte_enabled)):
    from app.services.kontakt_transfer_service import apply_preview
    try:
        changed = apply_preview(db, _org_id(user), user.id, preview_id)
    except LookupError:
        raise HTTPException(404, "Importvorschau nicht gefunden") from None
    return RedirectResponse(f"/kontakte?imported={changed}", status_code=303)


@router.get("/{kontakt_id}", response_class=HTMLResponse)
def detail(
    request: Request,
    kontakt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
    merge_konflikt: list[str] | None = Query(None),
):
    if kontakt_service.get_kontakt(db, kontakt_id) is None:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    sms_gateway_available = False
    if can_send_manual_sms(user):
        from app.services.sms_service import sms_available
        sms_gateway_available = sms_available(_org_id(user), db)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request, "kontakte/_detail.html", {
                "kontakt": kontakt_service.get_kontakt(db, kontakt_id), "user": user,
                "merge_konflikte": merge_konflikt or [], "sms_gateway_available": sms_gateway_available,
            }
        )
    response = _seite(request, db, user, selected_id=kontakt_id, merge_konflikte=merge_konflikt)
    response.context["sms_gateway_available"] = sms_gateway_available
    return response


@router.post("/{kontakt_id}/sms")
async def sms_an_kontakt_senden(
    kontakt_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    telefon_id: int = Form(...),
    text: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    """Queues one gateway SMS to one explicitly selected contact number."""
    if not can_send_manual_sms(user):
        raise HTTPException(status_code=403, detail="Keine Berechtigung zum SMS-Versand")
    org_id = _org_id(user)
    kontakt = db.query(Kontakt).filter(Kontakt.id == kontakt_id, Kontakt.org_id == org_id).first()
    telefon = (
        db.query(KontaktTelefon)
        .filter(
            KontaktTelefon.id == telefon_id,
            KontaktTelefon.kontakt_id == kontakt_id,
            KontaktTelefon.org_id == org_id,
        )
        .first()
    )
    if kontakt is None or telefon is None:
        raise HTTPException(status_code=404, detail="Kontakt oder Telefonnummer nicht gefunden")
    if telefon.sms_eignung is not True:
        raise HTTPException(status_code=400, detail="Diese Telefonnummer ist nicht SMS-faehig")
    text = text.strip()
    if not text:
        return RedirectResponse(f"/kontakte/{kontakt_id}?sms_error=empty", status_code=303)
    from app.services.sms_service import sms_available
    if not sms_available(org_id, db):
        return RedirectResponse(f"/kontakte/{kontakt_id}?sms_error=no_provider", status_code=303)
    if not telefon.nummer_normalisiert:
        return RedirectResponse(f"/kontakte/{kontakt_id}?sms_error=no_recipient", status_code=303)
    log_entry = SmsLog(
        org_id=org_id,
        sent_at=datetime.now(UTC).replace(tzinfo=None),
        completed_at=None,
        source="manual",
        alarm_type_code=None,
        text=text,
        recipient_count=1,
        success_count=0,
        provider=None,
        triggered_by_user_id=user.id,
    )
    db.add(log_entry)
    db.flush()
    db.commit()
    from app.services.sms_dispatch_service import dispatch_manual_sms
    background_tasks.add_task(
        dispatch_manual_sms,
        org_id,
        log_entry.id,
        text,
        {telefon.nummer_normalisiert: (kontakt.anzeigename, None, "kontakt", kontakt.id)},
        user.id,
        "kontakt",
    )
    return RedirectResponse(f"/kontakte/{kontakt_id}?sms_started={log_entry.id}", status_code=303)


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
    duplikate_bestaetigt: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    daten = _form_daten(typ, anzeigename, vorname, nachname, funktion, organisation, email, erreichbarkeit, notizen)
    form_data: dict[str, object] = {
        **daten, "nummer": nummer, "telefon_label": telefon_label, "bevorzugt": bevorzugt,
        "sms_eignung": sms_eignung, "kategorien": kategorien,
    }
    kandidaten = kontakt_service.find_duplicate_candidates(
        db, anzeigename=anzeigename, organisation=organisation, email=email, telefone=nummer
    )
    if kandidaten and duplikate_bestaetigt != "1":
        return _seite(
            request, db, user, form_data=form_data,
            error=(
                "Moegliche doppelte Kontakte gefunden. Bitte bewusst bestaetigen "
                "oder einen vorhandenen Kontakt verwenden."
            ),
            duplicate_candidates=kandidaten,
        )
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
            form_data=form_data,
            error=str(exc),
        )
    return RedirectResponse(f"/kontakte/{kontakt.id}", status_code=303)


_MERGE_FELDER = (
    "typ", "anzeigename", "vorname", "nachname", "funktion", "organisation", "email", "erreichbarkeit", "notizen",
)


@router.get("/{quelle_id}/zusammenfuehren", response_class=HTMLResponse)
def zusammenfuehren_form(
    request: Request, quelle_id: int, ziel: int = 0,
    db: Session = Depends(get_db), user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    quelle = kontakt_service.get_kontakt(db, quelle_id)
    if quelle is None:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    kontakte, _ = kontakt_service.list_kontakte(db, q="", page=1)
    ziel_kontakt = kontakt_service.get_kontakt(db, ziel) if ziel else None
    return templates.TemplateResponse(request, "kontakte/zusammenfuehren.html", {
        "user": user, "quelle": quelle, "ziel": ziel_kontakt, "kontakte": [k for k in kontakte if k.id != quelle.id],
        "felder": _MERGE_FELDER,
    })


@router.post("/{quelle_id}/zusammenfuehren")
def zusammenfuehren(
    quelle_id: int,
    ziel_id: int = Form(...),
    feldwahl_typ: str = Form("ziel"),
    feldwahl_anzeigename: str = Form("ziel"),
    feldwahl_vorname: str = Form("ziel"),
    feldwahl_nachname: str = Form("ziel"),
    feldwahl_funktion: str = Form("ziel"),
    feldwahl_organisation: str = Form("ziel"),
    feldwahl_email: str = Form("ziel"),
    feldwahl_erreichbarkeit: str = Form("ziel"),
    feldwahl_notizen: str = Form("ziel"),
    db: Session = Depends(get_db), user: User = Depends(require_role(*_SCHREIB_ROLLEN)),
    _guard: None = Depends(require_kontakte_enabled),
):
    auswahl = {
        "typ": feldwahl_typ, "anzeigename": feldwahl_anzeigename, "vorname": feldwahl_vorname,
        "nachname": feldwahl_nachname, "funktion": feldwahl_funktion,
        "organisation": feldwahl_organisation, "email": feldwahl_email,
        "erreichbarkeit": feldwahl_erreichbarkeit, "notizen": feldwahl_notizen,
    }
    auswahl = {feld: seite for feld, seite in auswahl.items() if seite in ("quelle", "ziel")}
    try:
        ergebnis = kontakt_service.merge_kontakte(db, quelle_id, ziel_id, auswahl, user_id=user.id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    url = f"/kontakte/{ergebnis.kontakt.id}"
    if ergebnis.freigabe_konflikte:
        url = f"{url}?{urlencode([('merge_konflikt', konflikt) for konflikt in ergebnis.freigabe_konflikte])}"
    return RedirectResponse(url, status_code=303)


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
