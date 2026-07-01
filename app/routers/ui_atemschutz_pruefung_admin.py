"""Atemschutzgeräteprüfung – Verwaltung: Geräte-Stammdaten, Einstellungen, Token/QR.

Struktur analog app/routers/ui_fahrtenbuch_admin.py.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.atemschutz_pruefung import AtemschutzGeraet
from app.models.master import OrgSettings

router = APIRouter()
logger = logging.getLogger("einsatzleiter.atemschutz_pruefung_admin")

_require_admin = require_role("admin")


# ── Stammdaten: Geräte ───────────────────────────────────────────────────────

@router.get("/admin/atemschutz-pruefung/geraete", response_class=HTMLResponse)
async def geraete_liste(request: Request, db: Session = Depends(get_db), user=Depends(_require_admin)):
    geraete = (
        db.query(AtemschutzGeraet)
        .filter(AtemschutzGeraet.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .order_by(AtemschutzGeraet.nummer)
        .all()
    )
    return templates.TemplateResponse(request, "admin/atemschutz_pruefung/geraete.html", {
        "user": user, "geraete": geraete,
    })


@router.post("/admin/atemschutz-pruefung/geraete/neu")
async def geraet_neu(
    request: Request,
    nummer: str = Form(...),
    bezeichnung: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(_require_admin),
):
    db.add(AtemschutzGeraet(
        org_id=user.org_id,
        nummer=nummer.strip(),
        bezeichnung=bezeichnung.strip() or None,
    ))
    write_audit(db, action="atemschutz_geraet.angelegt", org_id=user.org_id, user_id=user.id)
    db.commit()
    return RedirectResponse("/admin/atemschutz-pruefung/geraete?saved=1", status_code=303)


@router.post("/admin/atemschutz-pruefung/geraete/{geraet_id}/bearbeiten")
async def geraet_bearbeiten(
    request: Request,
    geraet_id: int,
    nummer: str = Form(...),
    bezeichnung: str = Form(""),
    aktiv: bool = Form(True),
    db: Session = Depends(get_db),
    user=Depends(_require_admin),
):
    g = (
        db.query(AtemschutzGeraet)
        .filter(AtemschutzGeraet.id == geraet_id, AtemschutzGeraet.org_id == user.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not g:
        raise HTTPException(status_code=404)
    g.nummer = nummer.strip()
    g.bezeichnung = bezeichnung.strip() or None
    g.aktiv = aktiv
    write_audit(db, action="atemschutz_geraet.bearbeitet", org_id=user.org_id, user_id=user.id, entity_id=g.id)
    db.commit()
    return RedirectResponse("/admin/atemschutz-pruefung/geraete?saved=1", status_code=303)


# ── Einstellungen: Wart-Benachrichtigung + Modul-Toggle ─────────────────────

@router.get("/admin/atemschutz-pruefung/einstellungen", response_class=HTMLResponse)
async def einstellungen(request: Request, db: Session = Depends(get_db), user=Depends(_require_admin)):
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    return templates.TemplateResponse(request, "admin/atemschutz_pruefung/einstellungen.html", {
        "user": user, "org": org,
        "saved": request.query_params.get("saved"),
    })


@router.post("/admin/atemschutz-pruefung/einstellungen")
async def einstellungen_speichern(
    request: Request,
    atemschutz_pruefung_modul_aktiv: bool = Form(False),
    atemschutz_wart_mail: str = Form(""),
    atemschutz_wart_teams_webhook_url: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(_require_admin),
):
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    org.atemschutz_pruefung_modul_aktiv = atemschutz_pruefung_modul_aktiv
    org.atemschutz_wart_mail = atemschutz_wart_mail.strip() or None
    org.atemschutz_wart_teams_webhook_url = atemschutz_wart_teams_webhook_url.strip() or None
    write_audit(db, action="atemschutz_pruefung.einstellungen_gespeichert", org_id=user.org_id, user_id=user.id)
    db.commit()
    return RedirectResponse("/admin/atemschutz-pruefung/einstellungen?saved=1", status_code=303)


# ── Öffentlicher Link / QR ───────────────────────────────────────────────────

@router.get("/admin/atemschutz-pruefung/token", response_class=HTMLResponse)
async def token_seite(request: Request, db: Session = Depends(get_db), user=Depends(_require_admin)):
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    base_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, "admin/atemschutz_pruefung/token.html", {
        "user": user, "org": org, "base_url": base_url,
        "saved": request.query_params.get("saved"),
    })


@router.post("/admin/atemschutz-pruefung/token")
async def token_generieren(request: Request, db: Session = Depends(get_db), user=Depends(_require_admin)):
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    org.atemschutz_pruef_token = secrets.token_urlsafe(24)
    write_audit(db, action="atemschutz_pruef_token_rotiert", org_id=user.org_id, user_id=user.id)
    db.commit()
    return RedirectResponse("/admin/atemschutz-pruefung/token?saved=1", status_code=303)


@router.get("/admin/atemschutz-pruefung/token/qr.png")
async def token_qr(request: Request, db: Session = Depends(get_db), user=Depends(_require_admin)):
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org or not org.atemschutz_pruef_token:
        raise HTTPException(status_code=404)
    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/ap/{org.atemschutz_pruef_token}"
    try:
        import io

        import qrcode  # type: ignore
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except ImportError:
        raise HTTPException(status_code=501, detail="QR-Bibliothek nicht verfügbar")
