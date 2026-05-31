"""Öffentliche Seiten (Startseite, Impressum, About, Kontakt) + WYSIWYG-CMS.

Die Startseite wird für nicht angemeldete Nutzer von ``ui_incident.index`` über
``render_public_page`` gerendert; eingeloggte Admins können sie über die Vorschau
(``/startseite/vorschau``) ansehen. Die Pflege erfolgt unter ``/admin/seiten``
(nur ``system_admin``).
"""
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.core.permissions import require_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.user import User
from app.services import site_pages
from app.services.mail_service import send_contact_message

logger = logging.getLogger("einsatzleiter.public")

router = APIRouter()

ALLOWED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMG_BYTES = 4 * 1024 * 1024  # 4 MB
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def render_public_page(request: Request, db, slug: str, *, preview: bool = False,
                       kontakt: str | None = None) -> HTMLResponse:
    """Rendert eine öffentliche Seite (HTML-Body aus dem CMS) im festen Gerüst."""
    meta = site_pages.PAGES[slug]
    return templates.TemplateResponse(request, "public/page.html", {
        "user": getattr(request.state, "user", None),
        "page_slug": slug,
        "page_title": meta["title"],
        "body_html": site_pages.get_page_html(db, slug),
        "contact": meta["contact"],
        "preview": preview,
        "kontakt": kontakt,
        "year": datetime.now(UTC).year,
    })


# ── Öffentliche Seiten ────────────────────────────────────────────────────────

@router.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request, db=Depends(get_db)):
    return render_public_page(request, db, "impressum")


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request, db=Depends(get_db)):
    return render_public_page(request, db, "about")


@router.get("/datenschutz", response_class=HTMLResponse)
async def datenschutz(request: Request, db=Depends(get_db)):
    return render_public_page(request, db, "datenschutz")


@router.post("/kontakt")
async def contact_submit(
    request: Request, db=Depends(get_db),
    name: str = Form(""), email: str = Form(""), message: str = Form(""),
    website: str = Form(""),  # Honeypot – muss leer bleiben
):
    if website.strip():
        return RedirectResponse("/?kontakt=ok#kontakt", status_code=303)
    if not name.strip() or not email.strip() or not message.strip():
        return RedirectResponse("/?kontakt=fehler#kontakt", status_code=303)
    try:
        await send_contact_message(
            name=name.strip(), reply_email=email.strip(), message=message.strip(), db=db,
        )
    except Exception:
        logger.exception("Kontaktformular: Versand fehlgeschlagen")
        return RedirectResponse("/?kontakt=fehler#kontakt", status_code=303)
    return RedirectResponse("/?kontakt=ok#kontakt", status_code=303)


@router.get("/startseite/vorschau", response_class=HTMLResponse)
async def landing_preview(request: Request, db=Depends(get_db),
                          user: User = Depends(require_system_admin)):
    """Vorschau der öffentlichen Startseite – auch für eingeloggte Admins."""
    return render_public_page(request, db, "landing", preview=True)


@router.get("/seite/bild/{name}")
async def page_image(name: str):
    """Liefert ein über den Editor hochgeladenes Bild (Pfad-Traversal-sicher)."""
    if not _SAFE_FILENAME.match(name) or "/" in name or "\\" in name:
        raise HTTPException(404)
    path = site_pages.UPLOAD_DIR / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


# ── CMS (nur system_admin) ────────────────────────────────────────────────────

@router.get("/admin/seiten", response_class=HTMLResponse)
async def pages_list(request: Request, db=Depends(get_db),
                     user: User = Depends(require_system_admin)):
    return templates.TemplateResponse(request, "admin/pages_list.html", {
        "user": user,
        "pages": site_pages.PAGES,
    })


# WICHTIG: Die literalen Pfade (/bild, /bilder/liste) müssen VOR den
# parametrisierten Routen (/{slug}) registriert werden – sonst fängt {slug}
# z.B. "bild" ab und liefert 404.

@router.get("/admin/seiten/bilder/liste")
async def page_images_list(request: Request, db=Depends(get_db),
                           user: User = Depends(require_system_admin)):
    """JSON-Liste der hochgeladenen Bilder (für die Galerie im Editor)."""
    return JSONResponse({"images": site_pages.list_images()})


@router.post("/admin/seiten/bild")
async def page_image_upload(request: Request, db=Depends(get_db),
                            user: User = Depends(require_system_admin),
                            image: UploadFile = File(...)):
    """Bild-Upload aus dem WYSIWYG-Editor. Gibt JSON {"url": ...} zurück."""
    ext = Path(image.filename or "").suffix.lower()
    if ext not in ALLOWED_IMG_EXTS:
        raise HTTPException(400, "Format nicht erlaubt (PNG, JPG, WEBP, GIF)")
    data = await image.read()
    if not data:
        raise HTTPException(400, "Leere Datei")
    if len(data) > MAX_IMG_BYTES:
        raise HTTPException(400, f"Datei zu groß (max {MAX_IMG_BYTES // (1024 * 1024)} MB)")
    site_pages.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (site_pages.UPLOAD_DIR / filename).write_bytes(data)
    return JSONResponse({"url": f"/seite/bild/{filename}", "name": filename})


@router.get("/admin/seiten/{slug}", response_class=HTMLResponse)
async def page_edit(slug: str, request: Request, db=Depends(get_db),
                    user: User = Depends(require_system_admin)):
    if slug not in site_pages.PAGES:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "admin/page_edit.html", {
        "user": user,
        "slug": slug,
        "meta": site_pages.PAGES[slug],
        "body_html": site_pages.get_page_html(db, slug),
        "images": site_pages.list_images(),
        "saved": request.query_params.get("saved"),
    })


@router.post("/admin/seiten/{slug}")
async def page_save(slug: str, request: Request, db=Depends(get_db),
                    user: User = Depends(require_system_admin), html: str = Form("")):
    if slug not in site_pages.PAGES:
        raise HTTPException(404)
    site_pages.set_page_html(db, slug, html, user.id)
    db.commit()
    return RedirectResponse(f"/admin/seiten/{slug}?saved=1", status_code=303)
