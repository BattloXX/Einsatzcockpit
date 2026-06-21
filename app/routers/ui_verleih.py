"""Geräteverleih-Router: Admin-Inventar + GSL-Verleih-CRUD + SMS/PIN."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import has_role, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus
from app.models.verleih import (
    VerleihArtikel,
    VerleihAusleihe,
    VerleihFoto,
    VerleihPosition,
    VerleihStatus,
    VerleihStueckliste,
    VerleihStuecklistePosition,
)
from app.services import verleih_service as svc
from app.services.broadcast import broadcast_lage

router = APIRouter()
logger = logging.getLogger("einsatzleiter.verleih")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org(user, lage: MajorIncident) -> None:
    if not same_org_or_system_admin(user, lage.org_id):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Lage")


def _ausleihe_or_404(ausleihe_id: int, db: Session) -> VerleihAusleihe:
    a = db.query(VerleihAusleihe).options(
        selectinload(VerleihAusleihe.positionen),
        selectinload(VerleihAusleihe.fotos),
    ).filter_by(id=ausleihe_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Ausleihe nicht gefunden")
    return a


def _can_edit(user) -> bool:
    return has_role(user, "incident_leader", "admin", "org_admin", "recorder")


def _detail_ctx(db: Session, user, lage, ausleihe, **extra) -> dict:
    site = db.get(IncidentSite, ausleihe.site_id) if ausleihe.site_id else None
    artikel = svc.get_artikel_aktiv(db)
    artikel_data = [
        {
            "id": a.id,
            "artikel_nr": a.artikel_nr or "",
            "bezeichnung": a.bezeichnung,
            "ist_mengenartikel": a.ist_mengenartikel,
        }
        for a in artikel if a.artikel_nr
    ]
    ctx = {
        "user": user,
        "lage": lage,
        "a": ausleihe,
        "site": site,
        "can_edit": _can_edit(user),
        "sms_text": svc.get_sms_ausleih_text(db, lage.org_id, ausleihe),
        "erinnerung_text": svc.get_sms_erinnerung_text(db, lage.org_id, ausleihe),
        "artikel": artikel,
        "artikel_data": artikel_data,
    }
    ctx.update(extra)
    return ctx


# ── Admin: Artikelstammdaten ──────────────────────────────────────────────────

@router.get("/admin/verleih-artikel", response_class=HTMLResponse)
async def verleih_artikel_liste(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    artikel = svc.get_artikel_aktiv(db)
    return templates.TemplateResponse(request, "verleih/artikel_list.html", {
        "user": user,
        "artikel": artikel,
    })


@router.post("/admin/verleih-artikel/neu", response_class=HTMLResponse)
async def verleih_artikel_neu(
    request: Request,
    bezeichnung: str = Form(...),
    artikel_nr: str = Form(""),
    ist_mengenartikel: str = Form(""),
    lagerbestand: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    org_id = user.org_id
    a = VerleihArtikel(
        org_id=org_id,
        artikel_nr=artikel_nr.strip() or None,
        bezeichnung=bezeichnung.strip(),
        ist_mengenartikel=bool(ist_mengenartikel),
        lagerbestand=int(lagerbestand) if lagerbestand.strip().isdigit() else None,
        notizen=notizen.strip() or None,
    )
    db.add(a)
    db.commit()
    artikel = svc.get_artikel_aktiv(db)
    return templates.TemplateResponse(request, "verleih/artikel_list.html", {
        "user": user,
        "artikel": artikel,
        "saved": True,
    })


@router.post("/admin/verleih-artikel/{artikel_id}/bearbeiten", response_class=HTMLResponse)
async def verleih_artikel_bearbeiten(
    request: Request,
    artikel_id: int,
    bezeichnung: str = Form(...),
    artikel_nr: str = Form(""),
    ist_mengenartikel: str = Form(""),
    lagerbestand: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    a = db.get(VerleihArtikel, artikel_id)
    if not a:
        raise HTTPException(404, "Artikel nicht gefunden")
    a.bezeichnung = bezeichnung.strip()
    a.artikel_nr = artikel_nr.strip() or None
    a.ist_mengenartikel = bool(ist_mengenartikel)
    a.lagerbestand = int(lagerbestand) if lagerbestand.strip().isdigit() else None
    a.notizen = notizen.strip() or None
    db.commit()
    db.refresh(a)
    return templates.TemplateResponse(request, "verleih/_artikel_row.html", {
        "a": a,
    })


@router.post("/admin/verleih-artikel/{artikel_id}/loeschen", response_class=HTMLResponse)
async def verleih_artikel_loeschen(
    request: Request,
    artikel_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    a = db.get(VerleihArtikel, artikel_id)
    if a:
        a.aktiv = False
        db.commit()
    return Response(content="", status_code=200)


@router.get("/admin/verleih-artikel/autocomplete")
async def verleih_artikel_autocomplete(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin", "incident_leader", "recorder")),
):
    q = q.strip().lower()
    artikel = svc.get_artikel_aktiv(db)
    result = [
        {
            "id": a.id,
            "artikel_nr": a.artikel_nr or "",
            "bezeichnung": a.bezeichnung,
            "ist_mengenartikel": a.ist_mengenartikel,
            "lagerbestand": a.lagerbestand,
        }
        for a in artikel
        if not q or q in a.bezeichnung.lower() or (a.artikel_nr and q in a.artikel_nr.lower())
    ][:20]
    return JSONResponse(result)


# ── Admin: Stücklisten ────────────────────────────────────────────────────────

@router.get("/admin/verleih-stuecklisten", response_class=HTMLResponse)
async def stuecklisten_liste(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    stuecklisten = svc.get_stuecklisten_aktiv(db)
    artikel = svc.get_artikel_aktiv(db)
    return templates.TemplateResponse(request, "verleih/stueckliste_list.html", {
        "user": user,
        "stuecklisten": stuecklisten,
        "artikel": artikel,
    })


@router.post("/admin/verleih-stuecklisten/neu", response_class=HTMLResponse)
async def stueckliste_neu(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    org_id = user.org_id
    form = await request.form()
    bezeichnung = str(form.get("bezeichnung", "")).strip()
    notizen = str(form.get("notizen", "")).strip()
    if not bezeichnung:
        raise HTTPException(400, "Bezeichnung erforderlich")

    sl = VerleihStueckliste(org_id=org_id, bezeichnung=bezeichnung, notizen=notizen or None)
    db.add(sl)
    db.flush()

    # Positionen aus form (positionen_artikel_id[], positionen_menge[], positionen_bezeichnung[])
    artikel_ids = form.getlist("positionen_artikel_id[]")
    mengen = form.getlist("positionen_menge[]")
    pos_bezs = form.getlist("positionen_bezeichnung[]")
    artikel_nrs = form.getlist("positionen_artikel_nr[]")
    for i, bz in enumerate(pos_bezs):
        bz = bz.strip()
        if not bz:
            continue
        aid = artikel_ids[i] if i < len(artikel_ids) else ""
        menge = int(mengen[i]) if i < len(mengen) and str(mengen[i]).isdigit() else 1
        anr = artikel_nrs[i] if i < len(artikel_nrs) else ""
        pos = VerleihStuecklistePosition(
            stueckliste_id=sl.id,
            artikel_id=int(aid) if aid.isdigit() else None,
            bezeichnung=bz,
            artikel_nr=anr.strip() or None,
            menge=menge,
        )
        db.add(pos)

    db.commit()
    stuecklisten = svc.get_stuecklisten_aktiv(db)
    artikel = svc.get_artikel_aktiv(db)
    return templates.TemplateResponse(request, "verleih/stueckliste_list.html", {
        "user": user,
        "stuecklisten": stuecklisten,
        "artikel": artikel,
        "saved": True,
    })


@router.post("/admin/verleih-stuecklisten/{sl_id}/loeschen", response_class=HTMLResponse)
async def stueckliste_loeschen(
    request: Request,
    sl_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    sl = db.get(VerleihStueckliste, sl_id)
    if sl:
        sl.aktiv = False
        db.commit()
    return Response(content="", status_code=200)


# ── GSL: Ausleihe-Liste ───────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/verleih", response_class=HTMLResponse)
async def verleih_liste(
    request: Request,
    lage_id: int,
    filter: str = "alle",
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    from app.routers.ui_major_incident import _get_mi_features, _nav_counts, _can_manage
    features = _get_mi_features(db, lage.org_id)
    if not features.get("geraeteverleih"):
        raise HTTPException(403, "Geräteverleih nicht aktiviert")

    ausleihen = svc.get_ausleihen_fuer_lage(db, lage_id)
    if filter == "ausgeliehen":
        ausleihen = [a for a in ausleihen if a.status == VerleihStatus.ausgeliehen]
    elif filter == "zurueck":
        ausleihen = [a for a in ausleihen if a.status == VerleihStatus.zurueckgegeben]

    sites = db.query(IncidentSite).filter_by(major_incident_id=lage_id).order_by(IncidentSite.bezeichnung).all()

    return templates.TemplateResponse(request, "verleih/list.html", {
        "user": user,
        "lage": lage,
        "ausleihen": ausleihen,
        "sites": sites,
        "filter": filter,
        "can_edit": _can_edit(user),
        "can_manage": _can_manage(user),
        "mi_features": features,
        **_nav_counts(lage_id, lage, db),
    })


@router.get("/lage/{lage_id}/verleih/neu", response_class=HTMLResponse)
async def verleih_neu_form(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    sites = db.query(IncidentSite).filter_by(major_incident_id=lage_id).order_by(IncidentSite.bezeichnung).all()
    stuecklisten = svc.get_stuecklisten_aktiv(db)
    artikel = svc.get_artikel_aktiv(db)
    sites_data = [{"id": s.id, "bezeichnung": s.bezeichnung} for s in sites]
    artikel_data = [
        {
            "id": a.id,
            "artikel_nr": a.artikel_nr or "",
            "bezeichnung": a.bezeichnung,
            "ist_mengenartikel": a.ist_mengenartikel,
        }
        for a in artikel
        if a.artikel_nr
    ]

    return templates.TemplateResponse(request, "verleih/_ausleihe_form.html", {
        "user": user,
        "lage": lage,
        "sites": sites,
        "sites_data": sites_data,
        "stuecklisten": stuecklisten,
        "artikel": artikel,
        "artikel_data": artikel_data,
    })


@router.post("/lage/{lage_id}/verleih/neu", response_class=HTMLResponse)
async def verleih_neu(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    if lage.status != MajorIncidentStatus.active:
        raise HTTPException(400, "Lage nicht aktiv")

    form = await request.form()
    name = str(form.get("name", "")).strip()
    adresse = str(form.get("adresse", "")).strip()
    telefon = str(form.get("telefon", "")).strip()
    notizen = str(form.get("notizen", "")).strip()
    pin_raw = str(form.get("pin", "")).strip()
    send_pin_flag = str(form.get("send_pin", "")).strip() == "1"
    site_id_raw = str(form.get("site_id", "")).strip()
    site_id = int(site_id_raw) if site_id_raw.isdigit() else None
    pin_to_use = pin_raw if pin_raw else (svc.generate_pin() if send_pin_flag else None)

    # Positionen aus dynamischen Feldern
    bez_list = form.getlist("pos_bezeichnung[]")
    nr_list = form.getlist("pos_artikel_nr[]")
    menge_list = form.getlist("pos_menge[]")
    aid_list = form.getlist("pos_artikel_id[]")

    positionen = []
    for i, bz in enumerate(bez_list):
        bz = bz.strip()
        if not bz:
            continue
        positionen.append({
            "bezeichnung": bz,
            "artikel_nr": nr_list[i].strip() if i < len(nr_list) else None,
            "menge": int(menge_list[i]) if i < len(menge_list) and str(menge_list[i]).isdigit() else 1,
            "artikel_id": int(aid_list[i]) if i < len(aid_list) and str(aid_list[i]).isdigit() else None,
        })

    if not name or not positionen:
        raise HTTPException(400, "Name und mindestens eine Position erforderlich")

    ausleihe = svc.create_ausleihe(
        db=db,
        lage_id=lage_id,
        org_id=lage.org_id,
        name=name,
        adresse=adresse or None,
        telefon=telefon or None,
        site_id=site_id,
        positionen=positionen,
        user_id=user.id,
        pin=pin_to_use,
        notizen=notizen or None,
    )

    # Journal-Eintrag
    try:
        from app.core.security import get_author_name
        from app.models.major_incident import LageJournalEntry
        artikel_text = ", ".join(p["bezeichnung"] for p in positionen) or "Material"
        journal = LageJournalEntry(
            major_incident_id=lage_id,
            category="sonstiges",
            text=f"Geraeteverleih: {artikel_text} an {name} ausgeliehen",
            author_name=get_author_name(request),
            user_id=getattr(user, "id", None),
        )
        db.add(journal)
        db.commit()
    except Exception:
        logger.warning("Journal-Eintrag fuer Verleih fehlgeschlagen", exc_info=True)

    # PIN per SMS versenden
    pin_nachricht = None
    if send_pin_flag and pin_to_use:
        if telefon:
            from app.services.sms_service import send_sms
            try:
                from app.models.master import FireDept
                dept = db.get(FireDept, lage.org_id)
                org_name = dept.name if dept else "Feuerwehr"
            except Exception:
                org_name = "Feuerwehr"
            sms_text_pin = f"Ihr Geraeteausleihe-PIN: {pin_to_use} - {org_name}"
            sms_ok = await send_sms(lage.org_id, telefon, sms_text_pin)
            pin_nachricht = "PIN-SMS erfolgreich versendet" if sms_ok else "PIN generiert - SMS fehlgeschlagen (Gateway pruefen)"
        else:
            pin_nachricht = f"PIN generiert: {pin_to_use}"

    # Fotos speichern
    fotos_raw = form.getlist("fotos")
    for f in fotos_raw:
        if hasattr(f, "filename") and f.filename:
            try:
                await svc.save_verleih_foto(f, ausleihe.id, lage.org_id, user.id, db)
            except Exception:
                logger.warning("Foto-Upload beim Anlegen fehlgeschlagen", exc_info=True)

    await broadcast_lage(lage_id, {"type": "verleih:changed", "lage_id": lage_id})

    trigger = {"verleihChanged": True}
    if pin_nachricht:
        trigger["verleihNachricht"] = pin_nachricht

    return templates.TemplateResponse(request, "verleih/_ausleihe_card.html", {
        "a": ausleihe,
        "lage": lage,
        "can_edit": _can_edit(user),
    }, headers={"HX-Trigger": json.dumps(trigger)})


# ── GSL: Ausleihe-Detail ──────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/verleih/{ausleihe_id}", response_class=HTMLResponse)
async def verleih_detail(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe))


# ── GSL: Rückgabe ─────────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/verleih/{ausleihe_id}/card", response_class=HTMLResponse)
async def verleih_card(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)
    return templates.TemplateResponse(request, "verleih/_ausleihe_card.html", {
        "a": ausleihe,
        "lage": lage,
        "can_edit": _can_edit(user),
    })


@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/position/{position_id}/zurueck", response_class=HTMLResponse)
async def position_zurueck(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    position_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    ausleihe = svc.return_position(db, position_id)
    await broadcast_lage(lage_id, {"type": "verleih:changed", "lage_id": lage_id})

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe),
        headers={"HX-Trigger": json.dumps({"verleihKarteAktualisieren": str(ausleihe.id)})})


@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/alle-zurueck", response_class=HTMLResponse)
async def alle_zurueck(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    ausleihe = svc.return_all(db, ausleihe_id)
    await broadcast_lage(lage_id, {"type": "verleih:changed", "lage_id": lage_id})

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe),
        headers={"HX-Trigger": json.dumps({"verleihKarteAktualisieren": str(ausleihe.id)})})


# ── GSL: Stückliste laden (HTMX-JSON) ────────────────────────────────────────

@router.get("/lage/{lage_id}/verleih/stueckliste/{sl_id}")
async def stueckliste_positionen(
    lage_id: int,
    sl_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    sl = db.query(VerleihStueckliste).options(
        selectinload(VerleihStueckliste.positionen)
    ).filter_by(id=sl_id, aktiv=True).first()
    if not sl:
        return JSONResponse([])
    return JSONResponse([
        {
            "bezeichnung": p.bezeichnung or (p.artikel.bezeichnung if p.artikel else ""),
            "artikel_nr": p.artikel_nr or (p.artikel.artikel_nr if p.artikel else "") or "",
            "menge": p.menge,
            "artikel_id": p.artikel_id or "",
            "ist_mengenartikel": p.artikel.ist_mengenartikel if p.artikel else True,
        }
        for p in sl.positionen
    ])


# ── GSL: PIN + SMS ────────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/pin-senden", response_class=HTMLResponse)
async def pin_senden(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    pin = svc.generate_pin()
    ausleihe.pin = pin
    db.commit()

    sms_ok = False
    if ausleihe.telefon:
        from app.services.sms_service import send_sms
        try:
            from app.models.master import FireDept
            dept = db.get(FireDept, lage.org_id)
            org_name = dept.name if dept else "Feuerwehr"
        except Exception:
            org_name = "Feuerwehr"
        sms_text = f"Ihr Geraeteausleihe-PIN: {pin} - {org_name}"
        sms_ok = await send_sms(lage.org_id, ausleihe.telefon, sms_text)

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe,
                    pin_sms_ok=sms_ok, pin_sms_sent=bool(ausleihe.telefon)))


@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/sms-ausleih", response_class=HTMLResponse)
async def sms_ausleih_senden(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    sms_text: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    sms_ok = False
    if ausleihe.telefon:
        from app.services.sms_service import send_sms
        sms_ok = await send_sms(lage.org_id, ausleihe.telefon, sms_text.strip())
        if sms_ok:
            ausleihe.sms_ausleih_gesendet = True
            db.commit()

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe,
                    sms_text=sms_text,
                    sms_ausleih_ok=sms_ok,
                    sms_ausleih_no_phone=not ausleihe.telefon))


@router.post("/lage/{lage_id}/verleih/schnell-einsatzstelle")
async def schnell_einsatzstelle(
    request: Request,
    lage_id: int,
    bezeichnung: str = Form(...),
    ort: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    """Erzeugt eine Einsatzstelle direkt aus dem Verleih-Formular und gibt {id, bezeichnung} zurueck."""
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    if lage.status != MajorIncidentStatus.active:
        raise HTTPException(400, "Lage nicht aktiv")
    bezeichnung = bezeichnung.strip()
    if not bezeichnung:
        raise HTTPException(400, "Bezeichnung fehlt")
    from app.services.major_incident_service import create_site
    site = create_site(db, lage, bezeichnung=bezeichnung, ort=ort.strip()[:120] or None, created_by=user.id)
    db.commit()
    await broadcast_lage(lage_id, {"type": "site_created", "reload_board": True})
    return JSONResponse({"id": site.id, "bezeichnung": site.bezeichnung})


@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/foto", response_class=HTMLResponse)
async def verleih_foto_upload(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    foto: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    await svc.save_verleih_foto(foto, ausleihe_id, lage.org_id, user.id, db)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe, foto_ok=True))


@router.get("/lage/{lage_id}/verleih/{ausleihe_id}/foto/{foto_id}/bild")
async def verleih_foto_bild(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    foto_id: int,
    thumb: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    foto = db.get(VerleihFoto, foto_id)
    if not foto or foto.ausleihe_id != ausleihe_id:
        raise HTTPException(404)
    p = svc.foto_thumb_path(foto) if thumb else svc.foto_path(foto)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/jpeg")


@router.get("/lage/{lage_id}/verleih/{ausleihe_id}/drucken", response_class=HTMLResponse)
async def verleih_drucken(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)
    site = db.get(IncidentSite, ausleihe.site_id) if ausleihe.site_id else None

    try:
        from app.core.security import get_author_name
        from app.models.major_incident import LageJournalEntry
        journal = LageJournalEntry(
            major_incident_id=lage_id,
            category="sonstiges",
            text=f"Verleihschein gedruckt: {ausleihe.artikel_bezeichnungen or 'Material'} an {ausleihe.name}",
            author_name=get_author_name(request),
            user_id=getattr(user, "id", None),
        )
        db.add(journal)
        db.commit()
    except Exception:
        logger.warning("Journal-Eintrag fuer Druckvorgang fehlgeschlagen", exc_info=True)

    from app.models.master import FireDept
    dept = db.get(FireDept, lage.org_id)
    org_name = dept.name if dept else "Feuerwehr"

    return templates.TemplateResponse(request, "verleih/druck.html", {
        "user": user,
        "lage": lage,
        "a": ausleihe,
        "site": site,
        "org_name": org_name,
    })


@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/erinnerung-manuell", response_class=HTMLResponse)
async def erinnerung_manuell(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    sms_text: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    sms_ok = False
    if ausleihe.telefon:
        from app.services.sms_service import send_sms
        sms_ok = await send_sms(lage.org_id, ausleihe.telefon, sms_text.strip())
        if sms_ok:
            ausleihe.erinnerung_gesendet_at = datetime.now(UTC)
            db.commit()

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe,
                    erinnerung_text=sms_text,
                    erinnerung_ok=sms_ok,
                    erinnerung_no_phone=not ausleihe.telefon))


# ── GSL: Positionen nachträglich hinzufügen ───────────────────────────────────

@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/positionen-hinzufuegen", response_class=HTMLResponse)
async def positionen_hinzufuegen(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)

    form = await request.form()
    bez_list = form.getlist("pos_bezeichnung[]")
    nr_list  = form.getlist("pos_artikel_nr[]")
    menge_list = form.getlist("pos_menge[]")
    aid_list = form.getlist("pos_artikel_id[]")

    positionen = []
    for i, bz in enumerate(bez_list):
        bz = bz.strip()
        if not bz:
            continue
        positionen.append({
            "bezeichnung": bz,
            "artikel_nr": nr_list[i].strip() if i < len(nr_list) else None,
            "menge": int(menge_list[i]) if i < len(menge_list) and str(menge_list[i]).isdigit() else 1,
            "artikel_id": int(aid_list[i]) if i < len(aid_list) and str(aid_list[i]).isdigit() else None,
        })

    if not positionen:
        return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
            _detail_ctx(db, user, lage, ausleihe, add_error="Mindestens eine Position erforderlich"))

    ausleihe = svc.add_positionen(db, ausleihe_id, lage.org_id, positionen)

    try:
        from app.core.security import get_author_name
        from app.models.major_incident import LageJournalEntry
        artikel_text = ", ".join(p["bezeichnung"] for p in positionen)
        journal = LageJournalEntry(
            major_incident_id=lage_id,
            category="sonstiges",
            text=f"Geraeteverleih Nachtrag: {artikel_text} an {ausleihe.name} hinzugefuegt",
            author_name=get_author_name(request),
            user_id=getattr(user, "id", None),
        )
        db.add(journal)
        db.commit()
    except Exception:
        logger.warning("Journal-Eintrag fuer Verleih-Nachtrag fehlgeschlagen", exc_info=True)

    await broadcast_lage(lage_id, {"type": "verleih:changed", "lage_id": lage_id})

    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe, add_ok=True),
        headers={"HX-Trigger": json.dumps({"verleihKarteAktualisieren": str(ausleihe.id)})})


@router.post("/lage/{lage_id}/verleih/{ausleihe_id}/notizen", response_class=HTMLResponse)
async def notizen_aktualisieren(
    request: Request,
    lage_id: int,
    ausleihe_id: int,
    notizen: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    ausleihe = _ausleihe_or_404(ausleihe_id, db)
    ausleihe.notizen = notizen.strip() or None
    db.commit()
    return templates.TemplateResponse(request, "verleih/_ausleihe_detail.html",
        _detail_ctx(db, user, lage, ausleihe, notizen_ok=True))
