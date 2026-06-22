"""Dokumentation & Hilfe."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.templating import templates

router = APIRouter()


@router.get("/hilfe", response_class=HTMLResponse)
async def hilfe_index(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "hilfe.html", {"user": user})
