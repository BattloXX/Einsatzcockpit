"""Fahrtenbuch-Verwaltung, Stammdaten, QR, Export."""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, joinedload

from app.core.audit import write_audit
from app.core.permissions import is_fahrtenbuch_admin, is_system_admin, require_system_admin
from app.core.templating import templates
from app.core.timezones import local_date_to_utc
from app.db import get_db
from app.core.tenant import set_tenant_context
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, FahrtStatus, Fahrtzweck, Zielort
from app.models.master import FireDept, OrgSettings, VehicleMaster
from app.services.excel_export_service import exportiere_fahrten
from app.services.fahrtenbuch_service import (
    korrigiere_fahrt,
    stammdaten_korrektur_zaehler,
    storniere_fahrt,
)
from app.services.schaden_service import melde_schaden

router = APIRouter()
logger = logging.getLogger("einsatzleiter.fahrtenbuch_admin")


def _check_fahrtenbuch_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if not is_fahrtenbuch_admin(user):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return user


def _fb_admin(request: Request, db: Session):
    """Prüft die Fahrtenbuch-Berechtigung und ermittelt die effektive Org.

    system_admin kann via ?org=<id> eine beliebige Organisation ansehen/verändern
    (Impersonation – der globale _resolve_current_org-Dependency setzt bei ?org bereits
    den Tenant-Context und schreibt ein Audit). Ohne ?org arbeitet auch der Sysadmin in
    seiner eigenen Org. Reguläre Fahrtenbuch-Admins sind fest auf ihre Org beschränkt.

    Rückgabe: (user, org_id, org_obj).
    """
    user = _check_fahrtenbuch_admin(request)
    org_id = user.org_id
    if is_system_admin(user):
        org_param = request.query_params.get("org")
        if org_param:
            try:
                org_id = int(org_param)
            except ValueError:
                raise HTTPException(status_code=400, detail="Ungültiger org-Parameter")
        # Tenant-Context auf die effektive Org fixieren: bei ?org bereits gesetzt,
        # ohne ?org auf die eigene Org begrenzen (statt "alle Orgs sichtbar").
        set_tenant_context(db, org_id)
    org = (
        db.query(FireDept)
        .filter(FireDept.id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation nicht gefunden")
    return user, org_id, org


def _redirect_q(request: Request, **params) -> str:
    """Baut ein '?a=1&org=5'-Query-Fragment und erhält dabei den aktuellen ?org-Parameter
    (Sysadmin-Impersonation), damit Redirects im gewählten Org-Kontext bleiben."""
    q = {str(k): str(v) for k, v in params.items()}
    org = request.query_params.get("org")
    if org:
        q["org"] = org
    return "?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else ""


def _sysadmin_org_context(request: Request, user, org, db: Session) -> dict:
    """Template-Kontext für die Org-Auswahl (nur system_admin sieht den Umschalter)."""
    sysadmin = is_system_admin(user)
    orgs = []
    if sysadmin:
        orgs = (
            db.query(FireDept)
            .execution_options(include_all_tenants=True)
            .order_by(FireDept.name)
            .all()
        )
    return {
        "is_sysadmin": sysadmin,
        "acting_org": org,
        "sysadmin_orgs": orgs,
        # Query-Fragment zum Weiterreichen der gewählten Org an Links/Formulare.
        "org_q": f"?org={org.id}" if sysadmin else "",
    }


# ── Verwaltungsliste ──────────────────────────────────────────────────────────

@router.get("/verwaltung/fahrten", response_class=HTMLResponse)
async def fahrten_liste(
    request: Request,
    db: Session = Depends(get_db),
    von: str = "", bis: str = "",
    fahrzeug_id: int = 0, fahrttyp: str = "",
    zweck_id: int = 0, status: str = "aktiv",
    nur_statistikrelevant: bool = False,
    seite: int = 1,
):
    user, org_id, org = _fb_admin(request, db)
    q = (
        db.query(Fahrt)
        .filter(Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck), joinedload(Fahrt.zielort))
    )
    if status and status != "alle":
        try:
            q = q.filter(Fahrt.status == FahrtStatus(status))
        except ValueError:
            pass
    if von:
        dt = local_date_to_utc(von, org=org)
        if dt:
            q = q.filter(Fahrt.zeitpunkt >= dt)
    if bis:
        dt = local_date_to_utc(bis, end=True, org=org)
        if dt:
            q = q.filter(Fahrt.zeitpunkt <= dt)
    if fahrzeug_id:
        q = q.filter(Fahrt.fahrzeug_id == fahrzeug_id)
    if fahrttyp:
        try:
            q = q.filter(Fahrt.fahrttyp == FahrtKategorie(fahrttyp))
        except ValueError:
            pass
    if zweck_id:
        q = q.filter(Fahrt.zweck_id == zweck_id)
    if nur_statistikrelevant:
        q = q.filter(Fahrt.nicht_statistikrelevant == False)  # noqa: E712

    gesamt = q.count()
    pro_seite = 50
    fahrten = q.order_by(Fahrt.zeitpunkt.desc()).offset((seite - 1) * pro_seite).limit(pro_seite).all()

    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.is_adhoc == False,  # noqa: E712
            VehicleMaster.is_external == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    zwecke = db.query(Fahrtzweck).filter(Fahrtzweck.aktiv == True).order_by(Fahrtzweck.sort).all()  # noqa: E712

    return templates.TemplateResponse(request, "fahrtenbuch/verwaltung/liste.html", {
        "user": user,
        "fahrten": fahrten,
        "gesamt": gesamt,
        "seite": seite,
        "pro_seite": pro_seite,
        "fahrzeuge": fahrzeuge,
        "zwecke": zwecke,
        "filter": {
            "von": von, "bis": bis, "fahrzeug_id": fahrzeug_id,
            "fahrttyp": fahrttyp, "zweck_id": zweck_id, "status": status,
            "nur_statistikrelevant": nur_statistikrelevant,
        },
        **_sysadmin_org_context(request, user, org, db),
    })


@router.get("/verwaltung/fahrten/export.xlsx")
async def fahrten_export(
    request: Request,
    db: Session = Depends(get_db),
    von: str = "", bis: str = "",
    fahrzeug_id: int = 0, fahrttyp: str = "",
    zweck_id: int = 0, status: str = "aktiv",
    nur_statistikrelevant: bool = False,
):
    user, org_id, org = _fb_admin(request, db)
    q = (
        db.query(Fahrt)
        .filter(Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck), joinedload(Fahrt.zielort))
    )
    if status and status != "alle":
        try:
            q = q.filter(Fahrt.status == FahrtStatus(status))
        except ValueError:
            pass
    if von:
        dt = local_date_to_utc(von, org=org)
        if dt:
            q = q.filter(Fahrt.zeitpunkt >= dt)
    if bis:
        dt = local_date_to_utc(bis, end=True, org=org)
        if dt:
            q = q.filter(Fahrt.zeitpunkt <= dt)
    if fahrzeug_id:
        q = q.filter(Fahrt.fahrzeug_id == fahrzeug_id)
    if fahrttyp:
        try:
            q = q.filter(Fahrt.fahrttyp == FahrtKategorie(fahrttyp))
        except ValueError:
            pass
    if zweck_id:
        q = q.filter(Fahrt.zweck_id == zweck_id)
    if nur_statistikrelevant:
        q = q.filter(Fahrt.nicht_statistikrelevant == False)  # noqa: E712

    fahrten = q.order_by(Fahrt.zeitpunkt.desc()).all()
    org_name = (org.name if org else str(org_id)).replace(" ", "_")
    dateiname = f"Fahrtenbuch_{org_name}_{von or 'alle'}_{bis or 'alle'}.xlsx"

    xlsx_bytes = exportiere_fahrten(fahrten, org=org)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{dateiname}\""},
    )


@router.get("/verwaltung/fahrten/{fahrt_id}", response_class=HTMLResponse)
async def fahrt_detail(request: Request, fahrt_id: int, db: Session = Depends(get_db)):
    user, org_id, org = _fb_admin(request, db)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .options(
            joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck),
            joinedload(Fahrt.zielort), joinedload(Fahrt.benachrichtigungen),
        )
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404, detail="Fahrt nicht gefunden")

    original = None
    if fahrt.original_fahrt_id:
        original = (
            db.query(Fahrt)
            .filter(Fahrt.id == fahrt.original_fahrt_id)
            .execution_options(include_all_tenants=True)
            .first()
        )
    ersatz = None
    if fahrt.ersetzt_durch_id:
        ersatz = (
            db.query(Fahrt)
            .filter(Fahrt.id == fahrt.ersetzt_durch_id)
            .execution_options(include_all_tenants=True)
            .first()
        )

    return templates.TemplateResponse(request, "fahrtenbuch/verwaltung/detail.html", {
        "user": user, "fahrt": fahrt, "original": original, "ersatz": ersatz,
        "can_edit": is_fahrtenbuch_admin(user),
        **_sysadmin_org_context(request, user, org, db),
    })


@router.post("/verwaltung/fahrten/{fahrt_id}/storno")
async def fahrt_storno(
    request: Request, fahrt_id: int,
    grund: str = Form(""),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    if fahrt.status != FahrtStatus.aktiv:
        raise HTTPException(status_code=422, detail="Nur aktive Fahrten können storniert werden")
    storniere_fahrt(fahrt, grund, user.id, db)
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{fahrt_id}{_redirect_q(request, storniert=1)}", status_code=303)


@router.post("/verwaltung/fahrten/loeschen")
async def fahrten_loeschen(request: Request, db: Session = Depends(get_db)):
    """Endgültiges Löschen markierter Fahrten – nur für Systemadministratoren."""
    user = require_system_admin(request)
    from app.services.fahrtenbuch_service import loesche_fahrten
    # Effektive Org: system_admin kann via ?org=<id> die Fahrten einer anderen Org löschen.
    org_id = user.org_id
    org_param = request.query_params.get("org")
    if org_param:
        try:
            org_id = int(org_param)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ungültiger org-Parameter")
        set_tenant_context(db, org_id)
    form = await request.form()
    ids: list[int] = []
    for raw in form.getlist("ids"):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    anzahl = loesche_fahrten(ids, org_id, user.id, db)
    db.commit()
    zurueck = (form.get("zurueck") or "").lstrip("?")
    ziel = f"/verwaltung/fahrten?{zurueck}" if zurueck else "/verwaltung/fahrten"
    sep = "&" if "?" in ziel else "?"
    return RedirectResponse(f"{ziel}{sep}geloescht={anzahl}", status_code=303)


@router.get("/verwaltung/fahrten/{fahrt_id}/korrektur", response_class=HTMLResponse)
async def fahrt_korrektur_formular(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user, org_id, org = _fb_admin(request, db)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck))
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.is_adhoc == False,  # noqa: E712
            VehicleMaster.is_external == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    zwecke = db.query(Fahrtzweck).filter(Fahrtzweck.aktiv == True).order_by(Fahrtzweck.sort).all()  # noqa: E712
    zielorte = db.query(Zielort).filter(Zielort.aktiv == True).order_by(Zielort.sort).all()  # noqa: E712
    return templates.TemplateResponse(request, "fahrtenbuch/verwaltung/korrektur.html", {
        "user": user, "fahrt": fahrt, "fahrzeuge": fahrzeuge, "zwecke": zwecke, "zielorte": zielorte,
        **_sysadmin_org_context(request, user, org, db),
    })


@router.post("/verwaltung/fahrten/{fahrt_id}/korrektur")
async def fahrt_korrektur_speichern(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user, org_id, org = _fb_admin(request, db)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)

    from app.routers.ui_fahrtenbuch import _form_zu_daten
    form = await request.form()
    daten = _form_zu_daten(form, org_id=org_id, user=user, org=org)
    neue_fahrt = korrigiere_fahrt(fahrt, daten, user.id, db)
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{neue_fahrt.id}{_redirect_q(request, korrigiert=1)}", status_code=303)


@router.post("/verwaltung/fahrten/{fahrt_id}/statistikflag")
async def statistikflag_toggle(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user, org_id, org = _fb_admin(request, db)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    fahrt.nicht_statistikrelevant = not fahrt.nicht_statistikrelevant
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{fahrt_id}{_redirect_q(request)}", status_code=303)


@router.post("/verwaltung/fahrten/{fahrt_id}/schaden-retry")
async def schaden_retry(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user, org_id, org = _fb_admin(request, db)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    base_url = str(request.base_url).rstrip("/")
    await melde_schaden(fahrt, db, base_url=base_url)
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{fahrt_id}{_redirect_q(request, retry=1)}", status_code=303)


# ── Stammdaten: Zwecke ────────────────────────────────────────────────────────

@router.get("/admin/fahrtenbuch/zwecke", response_class=HTMLResponse)
async def zwecke_liste(request: Request, db: Session = Depends(get_db)):
    user, org_id, org = _fb_admin(request, db)
    zwecke = db.query(Fahrtzweck).filter(Fahrtzweck.org_id == org_id).execution_options(include_all_tenants=True).order_by(Fahrtzweck.sort).all()
    return templates.TemplateResponse(request, "fahrtenbuch/admin/zwecke.html", {
        "user": user, "zwecke": zwecke,
        **_sysadmin_org_context(request, user, org, db),
    })


@router.post("/admin/fahrtenbuch/zwecke/neu")
async def zweck_neu(
    request: Request,
    name: str = Form(...), kategorie: str = Form(...),
    verlangt_ausbildner: bool = Form(False), verlangt_gruppenkommandant: bool = Form(False),
    optional_einsatzleiter: bool = Form(False),
    sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    db.add(Fahrtzweck(
        org_id=org_id,
        name=name, kategorie=FahrtKategorie(kategorie),
        verlangt_ausbildner=verlangt_ausbildner,
        verlangt_gruppenkommandant=verlangt_gruppenkommandant,
        optional_einsatzleiter=optional_einsatzleiter,
        sort=sort,
    ))
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/zwecke{_redirect_q(request, saved=1)}", status_code=303)


@router.post("/admin/fahrtenbuch/zwecke/{zweck_id}/bearbeiten")
async def zweck_bearbeiten(
    request: Request, zweck_id: int,
    name: str = Form(...), kategorie: str = Form(...),
    verlangt_ausbildner: bool = Form(False), verlangt_gruppenkommandant: bool = Form(False),
    optional_einsatzleiter: bool = Form(False),
    aktiv: bool = Form(True), sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    z = db.query(Fahrtzweck).filter(Fahrtzweck.id == zweck_id).execution_options(include_all_tenants=True).first()
    if not z or z.org_id != org_id:
        raise HTTPException(status_code=404)
    z.name = name
    z.kategorie = FahrtKategorie(kategorie)
    z.verlangt_ausbildner = verlangt_ausbildner
    z.verlangt_gruppenkommandant = verlangt_gruppenkommandant
    z.optional_einsatzleiter = optional_einsatzleiter
    z.aktiv = aktiv
    z.sort = sort
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/zwecke{_redirect_q(request, saved=1)}", status_code=303)


# ── Stammdaten: Zielorte ──────────────────────────────────────────────────────

@router.get("/admin/fahrtenbuch/zielorte", response_class=HTMLResponse)
async def zielorte_liste(request: Request, db: Session = Depends(get_db)):
    user, org_id, org = _fb_admin(request, db)
    zielorte = db.query(Zielort).filter(Zielort.org_id == org_id).execution_options(include_all_tenants=True).order_by(Zielort.sort).all()
    return templates.TemplateResponse(request, "fahrtenbuch/admin/zielorte.html", {
        "user": user, "zielorte": zielorte,
        **_sysadmin_org_context(request, user, org, db),
    })


@router.post("/admin/fahrtenbuch/zielorte/neu")
async def zielort_neu(
    request: Request,
    name: str = Form(...), sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    db.add(Zielort(org_id=org_id, name=name, sort=sort))
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/zielorte{_redirect_q(request, saved=1)}", status_code=303)


@router.post("/admin/fahrtenbuch/zielorte/{zielort_id}/bearbeiten")
async def zielort_bearbeiten(
    request: Request, zielort_id: int,
    name: str = Form(...), aktiv: bool = Form(True), sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    z = db.query(Zielort).filter(Zielort.id == zielort_id).execution_options(include_all_tenants=True).first()
    if not z or z.org_id != org_id:
        raise HTTPException(status_code=404)
    z.name = name
    z.aktiv = aktiv
    z.sort = sort
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/zielorte{_redirect_q(request, saved=1)}", status_code=303)


# ── Fahrzeug-Stammdaten Fahrtenbuch ──────────────────────────────────────────

@router.post("/admin/fahrzeuge/{fahrzeug_id}/fahrtenbuch")
async def fahrzeug_fahrtenbuch_settings(
    request: Request, fahrzeug_id: int,
    kennzeichen: str = Form(""),
    erfasst_km: bool = Form(False),
    erfasst_betriebsstunden: bool = Form(False),
    zweiter_maschinist_pflicht: bool = Form(False),
    seilwinde_abfrage: bool = Form(False),
    einsatzleiter_abfrage: bool = Form(False),
    warn_schwelle_km: int = Form(50),
    warn_schwelle_bh: str = Form("10"),
    schaden_mail_override: str = Form(""),
    schaden_teams_webhook_override: str = Form(""),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    fz = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id, VehicleMaster.dept_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fz:
        raise HTTPException(status_code=404)
    from decimal import Decimal
    fz.kennzeichen = kennzeichen.strip() or None
    fz.erfasst_km = erfasst_km
    fz.erfasst_betriebsstunden = erfasst_betriebsstunden
    fz.zweiter_maschinist_pflicht = zweiter_maschinist_pflicht
    fz.seilwinde_abfrage = seilwinde_abfrage
    fz.einsatzleiter_abfrage = einsatzleiter_abfrage
    fz.warn_schwelle_km = warn_schwelle_km
    fz.warn_schwelle_bh = Decimal(warn_schwelle_bh)
    from app.services.mail_service import normalize_email_list
    fz.schaden_mail_override = normalize_email_list(schaden_mail_override) or None
    fz.schaden_teams_webhook_override = schaden_teams_webhook_override.strip() or None
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/fahrzeuge{_redirect_q(request, saved=1)}", status_code=303)


@router.post("/admin/fahrzeuge/{fahrzeug_id}/zaehler-korrektur")
async def zaehler_korrektur(
    request: Request, fahrzeug_id: int,
    art: str = Form(...), wert: str = Form(...),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    fz = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id, VehicleMaster.dept_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fz:
        raise HTTPException(status_code=404)
    from decimal import Decimal
    val = int(wert) if art == "km" else Decimal(wert)
    stammdaten_korrektur_zaehler(fz, art, val, user.id, db)
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/fahrzeuge{_redirect_q(request, zaehler_saved=1)}", status_code=303)


@router.post("/admin/fahrzeuge/{fahrzeug_id}/qr")
async def qr_generieren(
    request: Request, fahrzeug_id: int, db: Session = Depends(get_db)
):
    user, org_id, org = _fb_admin(request, db)
    fz = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id, VehicleMaster.dept_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fz:
        raise HTTPException(status_code=404)
    fz.qr_token = secrets.token_urlsafe(24)
    db.commit()

    # Org-Token für den QR-Link ermitteln
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).execution_options(include_all_tenants=True).first()
    if not org_s or not org_s.fahrtenbuch_token:
        return RedirectResponse(f"/admin/fahrtenbuch/fahrzeuge{_redirect_q(request, qr_kein_org_token=1)}", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/f/{org_s.fahrtenbuch_token}/v/{fz.qr_token}"
    # QR-Code als PNG erzeugen
    try:
        import io

        import qrcode  # type: ignore
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Content-Disposition": f"attachment; filename=\"qr_{fz.code}.png\""},
        )
    except ImportError:
        return RedirectResponse(f"/admin/fahrtenbuch/fahrzeuge{_redirect_q(request, qr_lib_fehlt=1)}", status_code=303)


@router.post("/admin/fahrtenbuch/token")
async def org_token_generieren(
    request: Request, db: Session = Depends(get_db)
):
    user, org_id, org = _fb_admin(request, db)
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).execution_options(include_all_tenants=True).first()
    if not org_s:
        raise HTTPException(status_code=404)
    org_s.fahrtenbuch_token = secrets.token_urlsafe(24)
    write_audit(db, action="fahrtenbuch_token_rotiert", org_id=org_id, user_id=user.id)
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/token{_redirect_q(request, saved=1)}", status_code=303)


@router.get("/admin/fahrtenbuch/token", response_class=HTMLResponse)
async def org_token_seite(request: Request, db: Session = Depends(get_db)):
    user, org_id, org = _fb_admin(request, db)
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).execution_options(include_all_tenants=True).first()
    base_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, "fahrtenbuch/admin/token.html", {
        "user": user, "org": org_s, "base_url": base_url,
        "saved": request.query_params.get("saved"),
        **_sysadmin_org_context(request, user, org, db),
    })


@router.get("/admin/fahrtenbuch/einstellungen", response_class=HTMLResponse)
async def fahrtenbuch_einstellungen(request: Request, db: Session = Depends(get_db)):
    user, org_id, org = _fb_admin(request, db)
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).execution_options(include_all_tenants=True).first()
    return templates.TemplateResponse(request, "fahrtenbuch/admin/einstellungen.html", {
        "user": user, "org": org_s,
        "saved": request.query_params.get("saved"),
        **_sysadmin_org_context(request, user, org, db),
    })


@router.post("/admin/fahrtenbuch/einstellungen")
async def fahrtenbuch_einstellungen_speichern(
    request: Request,
    schaden_mail: str = Form(""),
    schaden_teams_webhook_url: str = Form(""),
    fahrt_doppel_minuten: int = Form(10),
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).execution_options(include_all_tenants=True).first()
    if not org_s:
        raise HTTPException(status_code=404)
    from app.services.mail_service import normalize_email_list
    org_s.schaden_mail = normalize_email_list(schaden_mail) or None
    org_s.schaden_teams_webhook_url = schaden_teams_webhook_url.strip() or None
    org_s.fahrt_doppel_minuten = max(1, fahrt_doppel_minuten)
    write_audit(db, action="fahrtenbuch.einstellungen_gespeichert", org_id=org_id, user_id=user.id)
    db.commit()
    return RedirectResponse(f"/admin/fahrtenbuch/einstellungen{_redirect_q(request, saved=1)}", status_code=303)


@router.post("/admin/fahrtenbuch/fahrzeuge/sortierung")
async def fahrzeuge_sortierung(
    request: Request,
    db: Session = Depends(get_db),
):
    user, org_id, org = _fb_admin(request, db)
    try:
        data = await request.json()
        ids = [int(i) for i in data.get("ids", [])]
    except Exception:
        raise HTTPException(status_code=422, detail="Ungültige Reihenfolge")
    for idx, fz_id in enumerate(ids):
        fz = (
            db.query(VehicleMaster)
            .filter(VehicleMaster.id == fz_id, VehicleMaster.dept_id == org_id)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if fz:
            fz.display_order = idx
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/admin/fahrtenbuch/fahrzeuge", response_class=HTMLResponse)
async def fahrzeuge_fahrtenbuch(request: Request, db: Session = Depends(get_db)):
    user, org_id, org = _fb_admin(request, db)
    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == org_id, VehicleMaster.deleted == False)  # noqa: E712
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    return templates.TemplateResponse(request, "fahrtenbuch/admin/fahrzeuge.html", {
        "user": user, "fahrzeuge": fahrzeuge,
        "saved": request.query_params.get("saved"),
        "zaehler_saved": request.query_params.get("zaehler_saved"),
        **_sysadmin_org_context(request, user, org, db),
    })
