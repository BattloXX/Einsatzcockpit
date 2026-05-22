"""Settings-Router: Organisations-Einstellungen, Logo-Upload, System-Update (system_admin)."""
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.permissions import has_role, require_role, require_system_admin
from app.db import get_db
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import User
from app.services.update_service import apply_update, get_current_version

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

UPLOAD_DIR = Path("app/static/img/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── Organisations-Einstellungen ──────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db=Depends(get_db), user: User = Depends(require_role("org_admin", "admin"))):
    org = db.query(FireDept).filter(FireDept.id == user.org_id).first() if user.org_id else None
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first() if user.org_id else None
    version = get_current_version()
    is_sysadmin = has_role(user, "system_admin")
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    sys_settings = {s.key: s.value for s in db.query(SystemSettings).all()} if is_sysadmin else {}
    return templates.TemplateResponse(request, "admin/settings.html", {
        "user": user,
        "org": org,
        "org_settings": org_settings,
        "version": version,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "sys_settings": sys_settings,
    })


@router.post("/settings/org", response_class=HTMLResponse)
async def save_org_settings(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_name: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    street: str = Form(""),
    city: str = Form(""),
    primary_color: str = Form(""),
    footer_text: str = Form(""),
    logo: UploadFile = File(None),
):
    if not user.org_id:
        return RedirectResponse("/admin/settings", status_code=303)

    org = db.query(FireDept).filter(FireDept.id == user.org_id).first()
    if org and org_name:
        org.name = org_name
    if org and contact_email:
        org.contact_email = contact_email
    if org and contact_phone:
        org.contact_phone = contact_phone
    if org and street:
        org.street = street
    if org and city:
        org.city = city

    # Logo-Upload
    logo_path = None
    if logo and logo.filename:
        ext = Path(logo.filename).suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".svg", ".webp"}:
            dest = UPLOAD_DIR / f"logo_org{user.org_id}{ext}"
            with dest.open("wb") as f:
                shutil.copyfileobj(logo.file, f)
            logo_path = f"/static/img/uploads/logo_org{user.org_id}{ext}"
            if org:
                org.logo_path = logo_path

    # OrgSettings
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org_s:
        org_s = OrgSettings(org_id=user.org_id)
        db.add(org_s)
    if primary_color:
        org_s.primary_color = primary_color
    if footer_text:
        org_s.footer_text = footer_text
    if logo_path:
        org_s.logo_path = logo_path

    db.commit()
    return RedirectResponse("/admin/settings?saved=1", status_code=303)


# ── Organisations-Verwaltung (system_admin) ──────────────────────────────────

@router.get("/organisations", response_class=HTMLResponse)
def organisations_page(request: Request, db=Depends(get_db), user: User = Depends(require_system_admin)):
    orgs = db.query(FireDept).order_by(FireDept.name).all()
    return templates.TemplateResponse(request, "admin/organisations.html", {
        "user": user,
        "orgs": orgs,
    })


@router.post("/organisations/new")
async def create_organisation(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    slug: str = Form(...),
    name: str = Form(...),
    color: str = Form("#b71921"),
    contact_email: str = Form(""),
):
    existing = db.query(FireDept).filter(FireDept.slug == slug).first()
    if existing:
        return RedirectResponse("/admin/organisations?error=slug_exists", status_code=303)
    org = FireDept(
        slug=slug,
        name=name,
        color=color,
        contact_email=contact_email or None,
        is_home_org=False,
        is_active=True,
    )
    db.add(org)
    db.commit()
    return RedirectResponse("/admin/organisations?created=1", status_code=303)


@router.post("/organisations/{org_id}/toggle")
def toggle_organisation(org_id: int, db=Depends(get_db), user: User = Depends(require_system_admin)):
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    if org and not org.is_home_org:
        org.is_active = not org.is_active
        db.commit()
    return RedirectResponse("/admin/organisations", status_code=303)


# ── System-Update (system_admin only) ────────────────────────────────────────

@router.get("/system/update", response_class=HTMLResponse)
def update_page(request: Request, user: User = Depends(require_system_admin)):
    version = get_current_version()
    return templates.TemplateResponse(request, "admin/system_update.html", {
        "user": user,
        "version": version,
    })


@router.post("/system/update", response_class=HTMLResponse)
async def apply_system_update(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    release_zip: UploadFile = File(...),
):
    if not release_zip.filename or not release_zip.filename.endswith(".zip"):
        return templates.TemplateResponse(request, "admin/system_update.html", {
            "user": user,
            "version": get_current_version(),
            "error": "Bitte eine .zip-Datei hochladen",
        })

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        shutil.copyfileobj(release_zip.file, tmp)
        tmp_path = Path(tmp.name)

    result = apply_update(tmp_path)
    tmp_path.unlink(missing_ok=True)

    return templates.TemplateResponse(request, "admin/system_update.html", {
        "user": user,
        "version": get_current_version(),
        "update_result": result,
    })


# ── About ─────────────────────────────────────────────────────────────────────

@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db=Depends(get_db)):
    from app.config import settings
    user = getattr(request.state, "user", None)
    version = get_current_version()
    return templates.TemplateResponse(request, "admin/about.html", {
        "user": user,
        "version": version,
        "app_version": settings.APP_VERSION,
    })
