"""Settings-Router: Organisations-Einstellungen, Logo-Upload, System-Update (system_admin)."""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings as app_settings
from app.core.permissions import has_role, require_role, require_system_admin
from app.core.templating import templates
from app.core.timezones import common_timezones
from app.db import get_db
from app.models.master import BOS_VALUES, FireDept, OrgSettings, SystemSettings
from app.models.user import User
from app.services.update_service import apply_update, get_current_version

router = APIRouter(prefix="/admin")

UPLOAD_DIR = Path("app/static/img/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Erlaubte Logo-Formate – MIME zusätzlich per Magic-Bytes prüfen
ALLOWED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
ALLOWED_LOGO_MIME = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB


def _validate_logo_bytes(data: bytes, ext: str) -> tuple[bool, str]:
    """Prüft Größe + (wenn möglich) MIME via filetype-Lib. SVG wird per Heuristik geprüft."""
    if len(data) > MAX_LOGO_BYTES:
        return False, f"Datei zu groß (max {MAX_LOGO_BYTES // 1024} KB)"
    if ext == ".svg":
        head = data[:512].lower()
        if b"<svg" not in head:
            return False, "Keine gültige SVG-Datei"
        if b"<script" in data.lower():
            return False, "SVG mit eingebettetem Script wird abgelehnt"
        return True, ""
    try:
        import filetype  # type: ignore
        kind = filetype.guess(data)
        if kind is None:
            return False, "Unbekanntes Dateiformat"
        if kind.mime not in ALLOWED_LOGO_MIME:
            return False, f"MIME-Typ {kind.mime} nicht erlaubt"
        return True, ""
    except ImportError:
        # Lib noch nicht installiert: nur Extension prüfen
        return True, ""


# ── Organisations-Einstellungen ──────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db=Depends(get_db), user: User = Depends(require_role("org_admin", "admin")),
                  org_id: int | None = None):
    is_sysadmin = has_role(user, "system_admin")
    if is_sysadmin and org_id:
        effective_org_id = org_id
    else:
        effective_org_id = user.org_id
    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first() if effective_org_id else None
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
        "timezones": common_timezones(),
        "default_timezone": app_settings.DEFAULT_TIMEZONE,
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
    withdraw_press_factor: str = Form(""),
    withdraw_press_reserve: str = Form(""),
    timezone: str = Form(""),
    fallback_lat: str = Form(""),
    fallback_lng: str = Form(""),
    logo: UploadFile = File(None),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if is_sysadmin and target_org_id else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings", status_code=303)

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first()
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
    if org and withdraw_press_factor:
        try:
            org.withdraw_press_factor = float(withdraw_press_factor)
        except ValueError:
            pass
    if org and withdraw_press_reserve:
        try:
            org.withdraw_press_reserve = int(withdraw_press_reserve)
        except ValueError:
            pass
    if org and timezone:
        # Akzeptiere nur bekannte IANA-Namen, sonst ignorieren (Default greift)
        from zoneinfo import available_timezones
        if timezone in available_timezones():
            org.timezone = timezone
    if org:
        try:
            org.fallback_lat = float(fallback_lat) if fallback_lat.strip() else None
        except ValueError:
            pass
        try:
            org.fallback_lng = float(fallback_lng) if fallback_lng.strip() else None
        except ValueError:
            pass

    # Logo-Upload
    logo_path = None
    upload_error: str | None = None
    if logo and logo.filename:
        ext = Path(logo.filename).suffix.lower()
        if ext not in ALLOWED_LOGO_EXTS:
            upload_error = "Dateityp nicht erlaubt (nur PNG/JPG/SVG/WEBP)"
        else:
            data = await logo.read()
            ok, msg = _validate_logo_bytes(data, ext)
            if not ok:
                upload_error = msg
            else:
                dest = UPLOAD_DIR / f"logo_org{effective_org_id}{ext}"
                # Alte Datei mit anderer Extension löschen, damit es keine Mehrfachversionen gibt
                for old_ext in ALLOWED_LOGO_EXTS:
                    if old_ext != ext:
                        old = UPLOAD_DIR / f"logo_org{effective_org_id}{old_ext}"
                        if old.exists():
                            try:
                                old.unlink()
                            except OSError:
                                pass
                dest.write_bytes(data)
                logo_path = f"/static/img/uploads/logo_org{effective_org_id}{ext}"
                if org:
                    org.logo_path = logo_path

    # OrgSettings
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if not org_s:
        org_s = OrgSettings(org_id=effective_org_id)
        db.add(org_s)
    if primary_color:
        org_s.primary_color = primary_color
    if footer_text:
        org_s.footer_text = footer_text
    if logo_path:
        org_s.logo_path = logo_path

    db.commit()
    suffix = "&logo_error=" + upload_error.replace(" ", "%20") if upload_error else ""
    org_suffix = f"&org_id={effective_org_id}" if is_sysadmin and effective_org_id else ""
    return RedirectResponse(f"/admin/settings?saved=1{org_suffix}{suffix}", status_code=303)


@router.post("/settings/org/logo/reset")
async def reset_org_logo(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
):
    """Entfernt das hochgeladene Logo der Organisation; Standardlogo wird wieder verwendet."""
    if not user.org_id:
        return RedirectResponse("/admin/settings", status_code=303)
    org = db.query(FireDept).filter(FireDept.id == user.org_id).first()
    if org and org.logo_path:
        # Datei aus dem Upload-Ordner löschen (Pfad muss unter UPLOAD_DIR liegen)
        try:
            rel = org.logo_path.lstrip("/")
            target = Path("app") / rel if rel else None
            if target and UPLOAD_DIR.resolve() in target.resolve().parents:
                target.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        org.logo_path = None
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if org_s:
        org_s.logo_path = None
    db.commit()
    return RedirectResponse("/admin/settings?saved=1&logo=reset", status_code=303)


# ── Organisations-Verwaltung (system_admin) ──────────────────────────────────

@router.get("/organisations", response_class=HTMLResponse)
def organisations_page(request: Request, db=Depends(get_db), user: User = Depends(require_system_admin)):
    orgs = db.query(FireDept).order_by(FireDept.name).all()
    return templates.TemplateResponse(request, "admin/organisations.html", {
        "user": user,
        "orgs": orgs,
        "bos_values": BOS_VALUES,
    })


@router.post("/organisations/new")
async def create_organisation(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    slug: str = Form(...),
    name: str = Form(...),
    color: str = Form("#b71921"),
    bos: str = Form("Feuerwehr"),
    contact_email: str = Form(""),
):
    existing = db.query(FireDept).filter(FireDept.slug == slug).first()
    if existing:
        return RedirectResponse("/admin/organisations?error=slug_exists", status_code=303)
    org = FireDept(
        slug=slug,
        name=name,
        color=color,
        bos=bos if bos in BOS_VALUES else "Feuerwehr",
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
