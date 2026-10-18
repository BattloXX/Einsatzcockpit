"""Systemadministrator-UI fuer systemweite Mailweiterleitungen."""
import json
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.mail_weiterleitung import MailWeiterleitung, MailWeiterleitungLog
from app.services.mail_weiterleitung_service import normalisiere_adresse, validiere_ziele
from app.services.org_mail_adresse import inbound_domain

router = APIRouter(prefix="/admin")


def _bearbeiten(db: Session, raw: str | None) -> MailWeiterleitung | None:
    return db.get(MailWeiterleitung, int(raw)) if raw and raw.isdigit() else None


@router.get("/mail-weiterleitungen", response_class=HTMLResponse)
def page(request: Request, db: Session = Depends(get_db), _=Depends(require_role("system_admin"))):
    return templates.TemplateResponse(request, "admin/mail_weiterleitungen.html", {
        "user": request.state.user,
        "regeln": db.query(MailWeiterleitung).order_by(MailWeiterleitung.adresse).all(),
        "logs": db.query(MailWeiterleitungLog).order_by(MailWeiterleitungLog.zeit.desc()).limit(200).all(),
        "error": request.query_params.get("error"),
        "info": request.query_params.get("info"),
        "bearbeiten": _bearbeiten(db, request.query_params.get("edit")),
        "inbound_domain": inbound_domain(db),
    })


@router.post("/mail-weiterleitungen")
def speichern(request: Request, db: Session = Depends(get_db), _=Depends(require_role("system_admin")),
                    regel_id: int | None = Form(None), adresse: str = Form(""), ziele: str = Form(""),
                    modus: str = Form("inline"), beschreibung: str = Form(""), max_pro_stunde: int = Form(60),
                    aktiv: bool = Form(False), catch_all_bestaetigt: bool = Form(False)):
    try:
        normalized = normalisiere_adresse(adresse, inbound_domain(db), catch_all_bestaetigt=catch_all_bestaetigt)
        targets = validiere_ziele([item.strip() for item in ziele.replace(";", ",").split(",")])
        if modus not in {"inline", "eml"}:
            raise ValueError("Ungültiger Modus")
        row = db.get(MailWeiterleitung, regel_id) if regel_id else None
        if row is None:
            row = MailWeiterleitung(adresse=normalized, ziele="[]")
            db.add(row)
        row.adresse, row.ziele, row.modus = normalized, json.dumps(targets), modus
        row.beschreibung, row.aktiv = beschreibung.strip()[:255] or None, aktiv
        row.max_pro_stunde = max(1, min(max_pro_stunde, 10000))
        write_audit(db, "admin.mail_weiterleitung.saved", user_id=request.state.user.id,
                    payload={"adresse": normalized, "modus": modus, "aktiv": aktiv})
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return RedirectResponse("/admin/mail-weiterleitungen?error=" + quote_plus(str(exc)), status_code=303)
    return RedirectResponse("/admin/mail-weiterleitungen", status_code=303)


@router.post("/mail-weiterleitungen/{regel_id}/toggle")
def toggle(regel_id: int, request: Request, db: Session = Depends(get_db),
                 _=Depends(require_role("system_admin"))):
    row = db.get(MailWeiterleitung, regel_id)
    if row:
        row.aktiv = not row.aktiv
        write_audit(db, "admin.mail_weiterleitung.toggled", user_id=request.state.user.id)
        db.commit()
    return RedirectResponse("/admin/mail-weiterleitungen", status_code=303)


@router.post("/mail-weiterleitungen/{regel_id}/loeschen")
def loeschen(regel_id: int, request: Request, db: Session = Depends(get_db),
                   _=Depends(require_role("system_admin"))):
    row = db.get(MailWeiterleitung, regel_id)
    if row:
        db.delete(row)
        write_audit(db, "admin.mail_weiterleitung.deleted", user_id=request.state.user.id)
        db.commit()
    return RedirectResponse("/admin/mail-weiterleitungen", status_code=303)


@router.post("/mail-weiterleitungen/{regel_id}/test")
async def testweiterleitung(regel_id: int, request: Request, db: Session = Depends(get_db),
                            _=Depends(require_role("system_admin"))):
    """Sendet eine feste Testmail (ohne Originalinhalt) an die Ziele der Regel."""
    from email.message import EmailMessage

    from app.services.mail_service import get_resend_cfg
    from app.services.resend_mail_service import send_via_resend

    row = db.get(MailWeiterleitung, regel_id)
    if row is None:
        return RedirectResponse("/admin/mail-weiterleitungen", status_code=303)
    cfg = get_resend_cfg(db)
    if not cfg:
        return RedirectResponse("/admin/mail-weiterleitungen?error=" + quote_plus("Resend ist nicht konfiguriert"),
                                status_code=303)
    msg = EmailMessage()
    msg["To"] = ", ".join(validiere_ziele(json.loads(row.ziele)))
    msg["Subject"] = "Testweiterleitung Einsatzcockpit"
    msg["X-Einsatzcockpit-Weitergeleitet"] = "1"
    msg.set_content(f"Dies ist eine Testnachricht der Weiterleitungsregel {row.adresse}.")
    try:
        await send_via_resend(msg, cfg["api_key"], f"weiterleitung@{cfg['from_domain']}")
    except Exception:
        fehler = quote_plus("Testmail konnte nicht gesendet werden")
        return RedirectResponse("/admin/mail-weiterleitungen?error=" + fehler, status_code=303)
    write_audit(db, "admin.mail_weiterleitung.test", user_id=request.state.user.id, payload={"adresse": row.adresse})
    db.commit()
    return RedirectResponse("/admin/mail-weiterleitungen?info=" + quote_plus("Testmail gesendet"), status_code=303)
