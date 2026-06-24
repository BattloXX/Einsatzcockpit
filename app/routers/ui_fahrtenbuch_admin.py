"""Fahrtenbuch-Verwaltung, Stammdaten, QR, Export."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.audit import write_audit
from app.core.permissions import is_fahrtenbuch_admin, require_role
from app.core.timezones import local_date_to_utc
from app.core.templating import templates
from app.db import get_db
from app.models.fahrtenbuch import Fahrt, FahrtBenachrichtigung, FahrtKategorie, FahrtStatus, Fahrtzweck, Zielort
from app.models.master import Member, OrgSettings, VehicleMaster
from app.services.excel_export_service import exportiere_fahrten
from app.services.fahrtenbuch_service import korrigiere_fahrt, recompute_zaehlerstand, stammdaten_korrektur_zaehler, storniere_fahrt
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
    user = _check_fahrtenbuch_admin(request)
    q = (
        db.query(Fahrt)
        .filter(Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck), joinedload(Fahrt.zielort))
    )
    if status and status != "alle":
        try:
            q = q.filter(Fahrt.status == FahrtStatus(status))
        except ValueError:
            pass
    if von:
        dt = local_date_to_utc(von)
        if dt:
            q = q.filter(Fahrt.zeitpunkt >= dt)
    if bis:
        dt = local_date_to_utc(bis, end=True)
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
            VehicleMaster.dept_id == user.org_id,
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
    user = _check_fahrtenbuch_admin(request)
    q = (
        db.query(Fahrt)
        .filter(Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck), joinedload(Fahrt.zielort))
    )
    if status and status != "alle":
        try:
            q = q.filter(Fahrt.status == FahrtStatus(status))
        except ValueError:
            pass
    if von:
        dt = local_date_to_utc(von)
        if dt:
            q = q.filter(Fahrt.zeitpunkt >= dt)
    if bis:
        dt = local_date_to_utc(bis, end=True)
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
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    org_name = (org.org.name if org and org.org else str(user.org_id)).replace(" ", "_")
    dateiname = f"Fahrtenbuch_{org_name}_{von or 'alle'}_{bis or 'alle'}.xlsx"

    xlsx_bytes = exportiere_fahrten(fahrten)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{dateiname}\""},
    )


@router.get("/verwaltung/fahrten/{fahrt_id}", response_class=HTMLResponse)
async def fahrt_detail(request: Request, fahrt_id: int, db: Session = Depends(get_db)):
    user = _check_fahrtenbuch_admin(request)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == user.org_id)
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
        original = db.query(Fahrt).filter(Fahrt.id == fahrt.original_fahrt_id).execution_options(include_all_tenants=True).first()
    ersatz = None
    if fahrt.ersetzt_durch_id:
        ersatz = db.query(Fahrt).filter(Fahrt.id == fahrt.ersetzt_durch_id).execution_options(include_all_tenants=True).first()

    return templates.TemplateResponse(request, "fahrtenbuch/verwaltung/detail.html", {
        "user": user, "fahrt": fahrt, "original": original, "ersatz": ersatz,
        "can_edit": is_fahrtenbuch_admin(user),
    })


@router.post("/verwaltung/fahrten/{fahrt_id}/storno")
async def fahrt_storno(
    request: Request, fahrt_id: int,
    grund: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    if fahrt.status != FahrtStatus.aktiv:
        raise HTTPException(status_code=422, detail="Nur aktive Fahrten können storniert werden")
    storniere_fahrt(fahrt, grund, user.id, db)
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{fahrt_id}?storniert=1", status_code=303)


@router.get("/verwaltung/fahrten/{fahrt_id}/korrektur", response_class=HTMLResponse)
async def fahrt_korrektur_formular(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user = _check_fahrtenbuch_admin(request)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck))
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == user.org_id,
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
    })


@router.post("/verwaltung/fahrten/{fahrt_id}/korrektur")
async def fahrt_korrektur_speichern(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user = _check_fahrtenbuch_admin(request)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)

    from app.routers.ui_fahrtenbuch import _form_zu_daten
    form = await request.form()
    daten = _form_zu_daten(form, org_id=user.org_id, user=user)
    neue_fahrt = korrigiere_fahrt(fahrt, daten, user.id, db)
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{neue_fahrt.id}?korrigiert=1", status_code=303)


@router.post("/verwaltung/fahrten/{fahrt_id}/statistikflag")
async def statistikflag_toggle(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user = _check_fahrtenbuch_admin(request)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    fahrt.nicht_statistikrelevant = not fahrt.nicht_statistikrelevant
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{fahrt_id}", status_code=303)


@router.post("/verwaltung/fahrten/{fahrt_id}/schaden-retry")
async def schaden_retry(
    request: Request, fahrt_id: int, db: Session = Depends(get_db)
):
    user = _check_fahrtenbuch_admin(request)
    fahrt = (
        db.query(Fahrt)
        .filter(Fahrt.id == fahrt_id, Fahrt.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrt:
        raise HTTPException(status_code=404)
    base_url = str(request.base_url).rstrip("/")
    await melde_schaden(fahrt, db, base_url=base_url)
    db.commit()
    return RedirectResponse(f"/verwaltung/fahrten/{fahrt_id}?retry=1", status_code=303)


# ── Stammdaten: Zwecke ────────────────────────────────────────────────────────

@router.get("/admin/fahrtenbuch/zwecke", response_class=HTMLResponse)
async def zwecke_liste(request: Request, db: Session = Depends(get_db)):
    user = _check_fahrtenbuch_admin(request)
    zwecke = db.query(Fahrtzweck).order_by(Fahrtzweck.sort).all()
    return templates.TemplateResponse(request, "fahrtenbuch/admin/zwecke.html", {
        "user": user, "zwecke": zwecke,
    })


@router.post("/admin/fahrtenbuch/zwecke/neu")
async def zweck_neu(
    request: Request,
    name: str = Form(...), kategorie: str = Form(...),
    verlangt_ausbildner: bool = Form(False), verlangt_gruppenkommandant: bool = Form(False),
    sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    db.add(Fahrtzweck(
        org_id=user.org_id,
        name=name, kategorie=FahrtKategorie(kategorie),
        verlangt_ausbildner=verlangt_ausbildner,
        verlangt_gruppenkommandant=verlangt_gruppenkommandant,
        sort=sort,
    ))
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/zwecke?saved=1", status_code=303)


@router.post("/admin/fahrtenbuch/zwecke/{zweck_id}/bearbeiten")
async def zweck_bearbeiten(
    request: Request, zweck_id: int,
    name: str = Form(...), kategorie: str = Form(...),
    verlangt_ausbildner: bool = Form(False), verlangt_gruppenkommandant: bool = Form(False),
    aktiv: bool = Form(True), sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    z = db.query(Fahrtzweck).filter(Fahrtzweck.id == zweck_id).first()
    if not z or z.org_id != user.org_id:
        raise HTTPException(status_code=404)
    z.name = name
    z.kategorie = FahrtKategorie(kategorie)
    z.verlangt_ausbildner = verlangt_ausbildner
    z.verlangt_gruppenkommandant = verlangt_gruppenkommandant
    z.aktiv = aktiv
    z.sort = sort
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/zwecke?saved=1", status_code=303)


# ── Stammdaten: Zielorte ──────────────────────────────────────────────────────

@router.get("/admin/fahrtenbuch/zielorte", response_class=HTMLResponse)
async def zielorte_liste(request: Request, db: Session = Depends(get_db)):
    user = _check_fahrtenbuch_admin(request)
    zielorte = db.query(Zielort).order_by(Zielort.sort).all()
    return templates.TemplateResponse(request, "fahrtenbuch/admin/zielorte.html", {
        "user": user, "zielorte": zielorte,
    })


@router.post("/admin/fahrtenbuch/zielorte/neu")
async def zielort_neu(
    request: Request,
    name: str = Form(...), sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    db.add(Zielort(org_id=user.org_id, name=name, sort=sort))
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/zielorte?saved=1", status_code=303)


@router.post("/admin/fahrtenbuch/zielorte/{zielort_id}/bearbeiten")
async def zielort_bearbeiten(
    request: Request, zielort_id: int,
    name: str = Form(...), aktiv: bool = Form(True), sort: int = Form(0),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    z = db.query(Zielort).filter(Zielort.id == zielort_id).first()
    if not z or z.org_id != user.org_id:
        raise HTTPException(status_code=404)
    z.name = name
    z.aktiv = aktiv
    z.sort = sort
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/zielorte?saved=1", status_code=303)


# ── Fahrzeug-Stammdaten Fahrtenbuch ──────────────────────────────────────────

@router.post("/admin/fahrzeuge/{fahrzeug_id}/fahrtenbuch")
async def fahrzeug_fahrtenbuch_settings(
    request: Request, fahrzeug_id: int,
    kennzeichen: str = Form(""),
    erfasst_km: bool = Form(False),
    erfasst_betriebsstunden: bool = Form(False),
    zweiter_maschinist_pflicht: bool = Form(False),
    seilwinde_abfrage: bool = Form(False),
    warn_schwelle_km: int = Form(50),
    warn_schwelle_bh: str = Form("10"),
    schaden_mail_override: str = Form(""),
    schaden_teams_webhook_override: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    fz = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id, VehicleMaster.dept_id == user.org_id)
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
    fz.warn_schwelle_km = warn_schwelle_km
    fz.warn_schwelle_bh = Decimal(warn_schwelle_bh)
    fz.schaden_mail_override = schaden_mail_override.strip() or None
    fz.schaden_teams_webhook_override = schaden_teams_webhook_override.strip() or None
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/fahrzeuge?saved=1", status_code=303)


@router.post("/admin/fahrzeuge/{fahrzeug_id}/zaehler-korrektur")
async def zaehler_korrektur(
    request: Request, fahrzeug_id: int,
    art: str = Form(...), wert: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    fz = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id, VehicleMaster.dept_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fz:
        raise HTTPException(status_code=404)
    from decimal import Decimal
    val = int(wert) if art == "km" else Decimal(wert)
    stammdaten_korrektur_zaehler(fz, art, val, user.id, db)
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/fahrzeuge?zaehler_saved=1", status_code=303)


@router.post("/admin/fahrzeuge/{fahrzeug_id}/qr")
async def qr_generieren(
    request: Request, fahrzeug_id: int, db: Session = Depends(get_db)
):
    user = _check_fahrtenbuch_admin(request)
    fz = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id, VehicleMaster.dept_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fz:
        raise HTTPException(status_code=404)
    fz.qr_token = secrets.token_urlsafe(24)
    db.commit()

    # Org-Token für den QR-Link ermitteln
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org or not org.fahrtenbuch_token:
        return RedirectResponse(f"/admin/fahrtenbuch/fahrzeuge?qr_kein_org_token=1", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/f/{org.fahrtenbuch_token}/v/{fz.qr_token}"
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
        return RedirectResponse(f"/admin/fahrtenbuch/fahrzeuge?qr_lib_fehlt=1", status_code=303)


@router.post("/admin/fahrtenbuch/token")
async def org_token_generieren(
    request: Request, db: Session = Depends(get_db)
):
    user = _check_fahrtenbuch_admin(request)
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    org.fahrtenbuch_token = secrets.token_urlsafe(24)
    write_audit(db, action="fahrtenbuch_token_rotiert", org_id=user.org_id, user_id=user.id)
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/token?saved=1", status_code=303)


@router.get("/admin/fahrtenbuch/token", response_class=HTMLResponse)
async def org_token_seite(request: Request, db: Session = Depends(get_db)):
    user = _check_fahrtenbuch_admin(request)
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    base_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, "fahrtenbuch/admin/token.html", {
        "user": user, "org": org, "base_url": base_url,
        "saved": request.query_params.get("saved"),
    })


@router.get("/admin/fahrtenbuch/einstellungen", response_class=HTMLResponse)
async def fahrtenbuch_einstellungen(request: Request, db: Session = Depends(get_db)):
    user = _check_fahrtenbuch_admin(request)
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    return templates.TemplateResponse(request, "fahrtenbuch/admin/einstellungen.html", {
        "user": user, "org": org,
        "saved": request.query_params.get("saved"),
    })


@router.post("/admin/fahrtenbuch/einstellungen")
async def fahrtenbuch_einstellungen_speichern(
    request: Request,
    schaden_mail: str = Form(""),
    schaden_teams_webhook_url: str = Form(""),
    fahrt_doppel_minuten: int = Form(10),
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    org.schaden_mail = schaden_mail.strip() or None
    org.schaden_teams_webhook_url = schaden_teams_webhook_url.strip() or None
    org.fahrt_doppel_minuten = max(1, fahrt_doppel_minuten)
    write_audit(db, action="fahrtenbuch.einstellungen_gespeichert", org_id=user.org_id, user_id=user.id)
    db.commit()
    return RedirectResponse("/admin/fahrtenbuch/einstellungen?saved=1", status_code=303)


@router.post("/admin/fahrtenbuch/fahrzeuge/sortierung")
async def fahrzeuge_sortierung(
    request: Request,
    db: Session = Depends(get_db),
):
    user = _check_fahrtenbuch_admin(request)
    try:
        data = await request.json()
        ids = [int(i) for i in data.get("ids", [])]
    except Exception:
        raise HTTPException(status_code=422, detail="Ungültige Reihenfolge")
    for idx, fz_id in enumerate(ids):
        fz = (
            db.query(VehicleMaster)
            .filter(VehicleMaster.id == fz_id, VehicleMaster.dept_id == user.org_id)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if fz:
            fz.display_order = idx
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/admin/fahrtenbuch/fahrzeuge", response_class=HTMLResponse)
async def fahrzeuge_fahrtenbuch(request: Request, db: Session = Depends(get_db)):
    user = _check_fahrtenbuch_admin(request)
    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == user.org_id, VehicleMaster.deleted == False)  # noqa: E712
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    return templates.TemplateResponse(request, "fahrtenbuch/admin/fahrzeuge.html", {
        "user": user, "fahrzeuge": fahrzeuge,
        "saved": request.query_params.get("saved"),
        "zaehler_saved": request.query_params.get("zaehler_saved"),
    })
