"""Loginfreie Probenpläne; SEC-11-Scoping ausschließlich im gemeinsamen Selektor."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.config import settings
from app.db import get_db
from app.services.probenplanung_ics import probenplan_ics
from app.services.probenplanung_public import oeffentliche_proben

public_router = APIRouter(tags=["probenplanung-public"])
# Keine internen Context-Processors (Benutzer, Organisation, Navigation).
public_templates = Jinja2Templates(directory="app/templates")
_PUBLIC_HEADERS = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow",
                   "Referrer-Policy": "no-referrer"}


@public_router.get("/p/probenplan/{token}.ics")
def oeffentlicher_probenkalender(token: str, db: Session = Depends(get_db)):
    eintraege = oeffentliche_proben(db, token)
    host = urlsplit(settings.PUBLIC_BASE_URL or settings.APP_BASE_URL).hostname or "localhost"
    return Response(probenplan_ics(eintraege, host), media_type="text/calendar; charset=utf-8",
                    headers={**_PUBLIC_HEADERS, "Content-Disposition": 'inline; filename="probenplan.ics"'})


@public_router.get("/p/probenplan/{token}", response_class=HTMLResponse)
def oeffentlicher_probenplan(token: str, request: Request, db: Session = Depends(get_db)):
    eintraege = oeffentliche_proben(db, token)
    return public_templates.TemplateResponse(
        request, "probenplanung/public_plan.html", {"proben": tuple(e.probe for e in eintraege)},
        headers=_PUBLIC_HEADERS,
    )
