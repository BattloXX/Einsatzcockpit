"""Öffentliche Installationsseite und Android-App-Download."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.core.templating import templates
from app.routers.public import public_context
from app.services import app_download_service

router = APIRouter()


def _ist_iphone(request: Request) -> bool:
    user_agent = request.headers.get("user-agent", "").lower()
    return "iphone" in user_agent or "ipad" in user_agent or "ipod" in user_agent


@router.get("/app", response_class=HTMLResponse)
async def app_installieren(request: Request):
    return templates.TemplateResponse(
        request,
        "public/app_installieren.html",
        public_context(
            request,
            None,
            app_url=settings.effective_public_base_url.rstrip("/"),
            ist_iphone=_ist_iphone(request),
        ),
    )


@router.get("/app/android")
def android_download():
    # Synchron: FastAPI fuehrt die Route im Threadpool aus, der GitHub-Abruf blockiert so nicht den Event-Loop.
    return RedirectResponse(app_download_service.android_apk_url(), status_code=302)
