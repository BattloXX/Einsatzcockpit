"""Loginfreie Probenpläne; SEC-11-Scoping ausschließlich im gemeinsamen Selektor."""
import hashlib
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.config import settings
from app.db import get_db
from app.models.master import FireDept
from app.models.probenplanung import ProbePublicToken
from app.services.probenplanung_ics import KalenderNichtVerfuegbar, probenplan_ics
from app.services.probenplanung_public import oeffentliche_proben

logger = logging.getLogger(__name__)

public_router = APIRouter(tags=["probenplanung-public"])
# Keine internen Context-Processors (Benutzer, Organisation, Navigation).
public_templates = Jinja2Templates(directory="app/templates")
_PUBLIC_HEADERS = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow",
                   "Referrer-Policy": "no-referrer"}


@public_router.get("/p/probenplan/{token}.ics")
def oeffentlicher_probenkalender(token: str, db: Session = Depends(get_db)):
    eintraege = oeffentliche_proben(db, token)
    host = urlsplit(settings.PUBLIC_BASE_URL or settings.APP_BASE_URL).hostname or "localhost"
    try:
        content = probenplan_ics(eintraege, host)
    except KalenderNichtVerfuegbar as exc:
        logger.exception("Kalender-Feed nicht verfügbar: icalendar kann nicht geladen werden")
        return PlainTextResponse(str(exc), status_code=503, headers=_PUBLIC_HEADERS)
    return Response(content, media_type="text/calendar; charset=utf-8",
                    headers={**_PUBLIC_HEADERS, "Content-Disposition": 'inline; filename="probenplan.ics"'})


@public_router.get("/p/probenplan/{token}", response_class=HTMLResponse)
def oeffentlicher_probenplan(token: str, request: Request, db: Session = Depends(get_db)):
    eintraege = oeffentliche_proben(db, token)
    token_row = (db.query(ProbePublicToken).execution_options(include_all_tenants=True)
                 .filter(ProbePublicToken.token_hash == hashlib.sha256(token.encode()).hexdigest())
                 .first())
    org = (db.query(FireDept).execution_options(include_all_tenants=True)
           .filter(FireDept.id == token_row.org_id).first()) if token_row else None
    return public_templates.TemplateResponse(
        request, "probenplanung/public_plan.html", {
            "proben": tuple(e.probe for e in eintraege),
            "org_name": org.name if org else "Einsatzcockpit",
            "org_logo": (org.logo_path if org and org.logo_path else "/static/img/logo_einsatzcockpit.png"),
        },
        headers=_PUBLIC_HEADERS,
    )
