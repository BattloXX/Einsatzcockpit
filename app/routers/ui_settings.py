"""Settings-Router: Organisations-Einstellungen, Logo-Upload, System-Update (system_admin)."""
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings as app_settings
from app.core.permissions import has_role, require_role, require_system_admin
from app.core.templating import templates
from app.core.timezones import common_timezones
from app.db import get_db
from app.models.master import BOS_VALUES, FireDept, OrgSettings, SeedTemplate, SystemSettings
from app.models.user import User
from app.services.seed_service import apply_seed_profile, copy_default_prompts, list_profiles
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

def _settings_context(request, db, user, org_id, **extra) -> dict:
    """Baut den Template-Kontext für admin/settings.html (GET + Wetter-POST teilen sich ihn)."""
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = org_id if (is_sysadmin and org_id) else user.org_id
    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    org_settings = (
        db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first() if effective_org_id else None
    )
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    sys_settings = {s.key: s.value for s in db.query(SystemSettings).all()} if is_sysadmin else {}
    from app.models.weather import WeatherStation
    from app.services.uas_service import uas_system_enabled
    weather_stations = (
        db.query(WeatherStation)
        .filter(WeatherStation.org_id == effective_org_id)
        .order_by(WeatherStation.name)
        .all()
        if effective_org_id else []
    )
    ctx = {
        "user": user,
        "org": org,
        "org_settings": org_settings,
        "version": get_current_version(),
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "sys_settings": sys_settings,
        "uas_sys_enabled": uas_system_enabled(db),
        "timezones": common_timezones(),
        "default_timezone": app_settings.DEFAULT_TIMEZONE,
        "weather_stations": weather_stations,
        "new_station_token": None,
        "weather_db_enabled": bool(app_settings.WEATHER_DATABASE_URL),
        "public_base_url": (app_settings.PUBLIC_BASE_URL or app_settings.APP_BASE_URL).rstrip("/"),
    }
    ctx.update(extra)
    return ctx


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db=Depends(get_db), user: User = Depends(require_role("org_admin", "admin")),
                  org_id: int | None = None):
    return templates.TemplateResponse(
        request, "admin/settings.html", _settings_context(request, db, user, org_id)
    )


@router.post("/settings/org", response_class=HTMLResponse)
async def save_org_settings(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_name: str = Form(""),
    short_code: str = Form(""),
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
    autoclose_enabled_raw: str = Form(""),
    autoclose_after_hours_raw: str = Form(""),
    autoclose_grace_minutes_raw: str = Form(""),
    default_access_pin: str = Form(""),
    gsl_lagemeldung_interval_raw: str = Form(""),
    gsl_lagemeldung_sofort_raw: str = Form(""),
    gsl_lagemeldung_auto_auftrag_raw: str = Form(""),
    uas_module_enabled_raw: str = Form(""),
    fahrtenbuch_modul_aktiv_raw: str = Form(""),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if is_sysadmin and target_org_id else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings", status_code=303)

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first()
    if org and org_name:
        org.name = org_name
    if org:
        # Kürzel: max. 3 Zeichen, Großbuchstaben; leerer String → NULL
        cleaned_code = short_code.strip().upper()[:3] if short_code.strip() else None
        org.short_code = cleaned_code
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

    # Autoclose-Override (leer = globale SystemSettings-Fallback verwenden)
    if autoclose_enabled_raw == "":
        org_s.autoclose_enabled = None
    elif autoclose_enabled_raw in ("1", "true"):
        org_s.autoclose_enabled = True
    else:
        org_s.autoclose_enabled = False
    try:
        org_s.autoclose_after_hours = (
            int(autoclose_after_hours_raw) if autoclose_after_hours_raw.strip() else None
        )
    except ValueError:
        pass
    try:
        org_s.autoclose_grace_minutes = (
            int(autoclose_grace_minutes_raw) if autoclose_grace_minutes_raw.strip() else None
        )
    except ValueError:
        pass

    # GSL-Lagemeldungs-Intervalle: leer ⇒ NULL (Default-Feld leer = Logik aus,
    # Sofort-Feld leer = Default verwenden); 0/negativ ablehnen (unverändert lassen)
    def _parse_interval(raw: str) -> tuple[bool, int | None]:
        s = raw.strip()
        if not s:
            return True, None
        try:
            v = int(s)
        except ValueError:
            return False, None
        if v <= 0:
            return False, None
        return True, v

    interval_was_off = org_s.gsl_lagemeldung_interval_minutes is None
    ok, val = _parse_interval(gsl_lagemeldung_interval_raw)
    if ok:
        org_s.gsl_lagemeldung_interval_minutes = val
    ok, val = _parse_interval(gsl_lagemeldung_sofort_raw)
    if ok:
        org_s.gsl_lagemeldung_interval_sofort_minutes = val
    org_s.gsl_lagemeldung_auto_auftrag = gsl_lagemeldung_auto_auftrag_raw in ("1", "true", "on")

    # UAS-Modul: Org-Toggle — nur änderbar wenn System-Flag aktiv.
    # Wenn System-Flag AUS: Wert unverändert lassen (disabled-Checkbox liefert
    # keinen Wert, was den Toggle sonst stillschweigend auf False setzen würde).
    from app.services.uas_service import uas_system_enabled
    if uas_system_enabled(db):
        old_uas = org_s.uas_module_enabled
        new_uas = uas_module_enabled_raw in ("1", "true", "on")
        org_s.uas_module_enabled = new_uas
        if old_uas != new_uas:
            from app.core.audit import write_audit
            write_audit(
                db,
                "uas.org_toggle",
                org_id=effective_org_id,
                user_id=user.id,
                payload={"alt": old_uas, "neu": new_uas},
                ip=request.client.host if request.client else None,
            )

    # Fahrtenbuch-Modul: Org-Toggle
    old_fb = org_s.fahrtenbuch_modul_aktiv
    new_fb = fahrtenbuch_modul_aktiv_raw in ("1", "true", "on")
    org_s.fahrtenbuch_modul_aktiv = new_fb
    if old_fb != new_fb:
        from app.core.audit import write_audit
        write_audit(
            db,
            "fahrtenbuch.org_toggle",
            org_id=effective_org_id,
            user_id=user.id,
            payload={"alt": old_fb, "neu": new_fb},
            ip=request.client.host if request.client else None,
        )

    # Wird die Lagemeldungs-Pflicht gerade erst aktiviert (Intervall aus → an), Timer für
    # bereits laufende Einsatzstellen sofort nachziehen – sonst erst beim nächsten
    # Phasen-/Ressourcen-Wechsel (Admin müsste sonst raten, warum noch kein Chip erscheint).
    if interval_was_off and org_s.gsl_lagemeldung_interval_minutes is not None:
        from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus, SitePhase
        from app.services import lagemeldung_service
        active_sites = (
            db.query(IncidentSite)
            .join(MajorIncident, MajorIncident.id == IncidentSite.major_incident_id)
            .filter(
                IncidentSite.org_id == effective_org_id,
                IncidentSite.phase == SitePhase.in_arbeit,
                MajorIncident.status == MajorIncidentStatus.active,
            )
            .all()
        )
        for s in active_sites:
            lagemeldung_service.ensure_timer(s, db)

    # Standard-PIN für neue Einsätze: neuer Wert → hashen; "__clear__" → entfernen; leer → unverändert
    pin_val = default_access_pin.strip()
    if pin_val == "__clear__":
        org_s.default_access_pin_hash = None
    elif pin_val:
        from app.core.security import hash_pin
        org_s.default_access_pin_hash = hash_pin(pin_val[:16])

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


# ── Pegelmessstationen ───────────────────────────────────────────────────────

@router.post("/settings/abfluss/add")
async def abfluss_station_add(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    hzbnr: str = Form(...),
    name: str = Form(...),
    beschreibung: str = Form(""),
    target_org_id: int | None = Form(None),
):
    import json
    effective_org_id = target_org_id if has_role(user, "system_admin") and target_org_id else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings", status_code=303)

    hzbnr = re.sub(r"\D", "", hzbnr.strip())[:10]
    name = name.strip()[:80]
    beschreibung = beschreibung.strip()[:200]
    if not hzbnr or not name:
        amp = f"&org_id={effective_org_id}" if has_role(user, "system_admin") else ""
        return RedirectResponse(f"/admin/settings/wetter?abfluss_error=ungueltig{amp}#pegel", status_code=303)

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if not org_s:
        org_s = OrgSettings(org_id=effective_org_id)
        db.add(org_s)

    stationen: list[dict] = org_s.abfluss_stationen_list
    if not any(s["hzbnr"] == hzbnr for s in stationen):
        stationen.append({"hzbnr": hzbnr, "name": name, "beschreibung": beschreibung})
    org_s.abfluss_stationen = json.dumps(stationen, ensure_ascii=False)
    db.commit()

    amp = f"&org_id={effective_org_id}" if has_role(user, "system_admin") else ""
    return RedirectResponse(f"/admin/settings/wetter?saved=1{amp}#pegel", status_code=303)


@router.post("/settings/abfluss/remove")
async def abfluss_station_remove(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    hzbnr: str = Form(...),
    target_org_id: int | None = Form(None),
):
    import json
    effective_org_id = target_org_id if has_role(user, "system_admin") and target_org_id else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings", status_code=303)

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if org_s:
        stationen = [s for s in org_s.abfluss_stationen_list if s["hzbnr"] != hzbnr.strip()]
        org_s.abfluss_stationen = json.dumps(stationen, ensure_ascii=False) if stationen else None
        db.commit()

        from app.services import abfluss_service
        abfluss_service.remove_station(effective_org_id, hzbnr.strip())

    amp = f"&org_id={effective_org_id}" if has_role(user, "system_admin") else ""
    return RedirectResponse(f"/admin/settings/wetter?saved=1{amp}#pegel", status_code=303)


# ── Wetter-Einstellungsseite ─────────────────────────────────────────────────

def _generate_qr_datauri(url: str) -> str | None:
    """Generiert einen QR-Code als base64 PNG Data-URI (dark theme). None bei Fehler."""
    try:
        import base64
        import io

        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=5,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#dae2fd", back_color="#0b1326")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _weather_settings_context(request, db, user, org_id, **extra) -> dict:
    """Baut den Template-Kontext fuer admin/settings_wetter.html."""
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = org_id if (is_sysadmin and org_id) else user.org_id
    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    org_settings = (
        db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []
    from app.models.weather import WeatherStation
    weather_stations = (
        db.query(WeatherStation)
        .filter(WeatherStation.org_id == effective_org_id)
        .order_by(WeatherStation.name)
        .all()
        if effective_org_id else []
    )
    from app.services import kachelmann_service
    base_url = (app_settings.PUBLIC_BASE_URL or app_settings.APP_BASE_URL).rstrip("/")
    alert_rules = _alert_rules_for_template(db, effective_org_id)
    alert_logs = _alert_logs_for_template(db, effective_org_id)
    from app.services.weather_alert_service import RULE_LABELS, RULE_DEFAULTS
    ctx = {
        "user": user,
        "org": org,
        "org_settings": org_settings,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "weather_stations": weather_stations,
        "new_station_token": None,
        "new_station_id": None,
        "new_dashboard_token": None,
        "weather_db_enabled": bool(app_settings.WEATHER_DATABASE_URL),
        "public_base_url": base_url,
        "dashboard_url": f"{base_url}/wetter/infoscreen/DASHBOARD_TOKEN",
        "kachelmann_is_configured": kachelmann_service.is_configured(),
        "dashboard_qr": None,
        "alert_rules": alert_rules,
        "alert_logs": alert_logs,
        "rule_labels": RULE_LABELS,
        "rule_defaults": RULE_DEFAULTS,
    }
    ctx.update(extra)
    # QR-Code nur wenn gerade eine frische URL bekannt ist
    if ctx.get("dashboard_url") and ctx.get("new_dashboard_token"):
        ctx["dashboard_qr"] = _generate_qr_datauri(ctx["dashboard_url"])
    return ctx


@router.get("/settings/wetter", response_class=HTMLResponse)
def weather_settings_page(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    return templates.TemplateResponse(
        request, "admin/settings_wetter.html",
        _weather_settings_context(request, db, user, org_id),
    )


@router.post("/settings/wetter/dashboard-token/generate", response_class=HTMLResponse)
async def weather_dashboard_token_generate(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    from app.core.security import generate_weather_dashboard_token, hash_api_key

    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    org_id_param = effective_org_id if is_sysadmin else None
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if not org_settings:
        org_settings = OrgSettings(org_id=effective_org_id)
        db.add(org_settings)

    raw = generate_weather_dashboard_token()
    org_settings.weather_dashboard_token_hash = hash_api_key(raw)
    db.commit()

    base_url = (app_settings.PUBLIC_BASE_URL or app_settings.APP_BASE_URL).rstrip("/")
    return templates.TemplateResponse(
        request, "admin/settings_wetter.html",
        _weather_settings_context(
            request, db, user, org_id_param,
            new_dashboard_token=raw,
            dashboard_url=f"{base_url}/wetter/infoscreen/{raw}",
        ),
    )


@router.post("/settings/wetter/dashboard-token/revoke")
async def weather_dashboard_token_revoke(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if org_settings:
        org_settings.weather_dashboard_token_hash = None
        db.commit()

    org_suffix = f"?org_id={effective_org_id}" if is_sysadmin else ""
    return RedirectResponse(f"/admin/settings/wetter{org_suffix}#dashboard", status_code=303)


@router.post("/settings/wetter/weather-toggle", response_class=HTMLResponse)
async def weather_toggle(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    enabled_raw: str = Form(""),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if not org_settings:
        org_settings = OrgSettings(org_id=effective_org_id)
        db.add(org_settings)

    org_settings.weather_enabled = bool(enabled_raw)
    db.commit()

    org_id_param = effective_org_id if is_sysadmin else None
    return templates.TemplateResponse(
        request, "admin/settings_wetter.html",
        _weather_settings_context(request, db, user, org_id_param),
    )


# ── Infoscreen Darstellungszeitraum ──────────────────────────────────────────

@router.post("/settings/wetter/infoscreen-hours", response_class=HTMLResponse)
async def weather_infoscreen_hours_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    history_hours: int = Form(24),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    hours = history_hours if history_hours in (6, 12, 24, 48, 72) else 24

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if not org_settings:
        org_settings = OrgSettings(org_id=effective_org_id)
        db.add(org_settings)

    org_settings.infoscreen_history_hours = hours
    db.commit()

    org_id_param = effective_org_id if is_sysadmin else None
    return templates.TemplateResponse(
        request, "admin/settings_wetter.html",
        _weather_settings_context(request, db, user, org_id_param),
    )


# ── Kachelmann API-Key (systemweit, nur system_admin) ────────────────────────

@router.post("/settings/wetter/kachelmann")
async def weather_kachelmann_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("system_admin")),
    kachelmann_api_key: str = Form(""),
):
    from datetime import UTC, datetime

    from app.models.master import SystemSettings

    key_val = kachelmann_api_key.strip()
    if key_val and key_val != "__clear__":
        existing = db.get(SystemSettings, "kachelmann_api_key")
        if existing:
            existing.value = key_val
            existing.updated_at = datetime.now(UTC)
            existing.updated_by_user_id = user.id
        else:
            db.add(SystemSettings(key="kachelmann_api_key", value=key_val,
                                  updated_by_user_id=user.id))
        db.commit()
    elif key_val == "__clear__":
        existing = db.get(SystemSettings, "kachelmann_api_key")
        if existing:
            existing.value = ""
            db.commit()

    try:
        from app.services import kachelmann_service
        kachelmann_service.reset_key_cache()
    except Exception:
        pass

    return RedirectResponse("/admin/settings/wetter?saved=1#kachelmann", status_code=303)


# ── Wetterwarnungen ──────────────────────────────────────────────────────────

def _alert_rules_for_template(db, org_id: int) -> list:
    """Lädt WeatherAlertRule-Einträge für das Template (stellt Seeding sicher)."""
    if not org_id:
        return []
    from app.services.weather_alert_service import ensure_rules
    ensure_rules(org_id, db)
    from app.models.weather_alert import WeatherAlertRule
    return (
        db.query(WeatherAlertRule)
        .filter(WeatherAlertRule.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .order_by(WeatherAlertRule.key)
        .all()
    )


def _alert_logs_for_template(db, org_id: int, limit: int = 20) -> list:
    if not org_id:
        return []
    from app.models.weather_alert import WeatherAlertLog
    return (
        db.query(WeatherAlertLog)
        .filter(WeatherAlertLog.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .order_by(WeatherAlertLog.gesendet_am.desc())
        .limit(limit)
        .all()
    )


@router.post("/settings/wetter/alert-channels")
async def weather_alert_channels_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    alert_mail: str = Form(""),
    alert_teams_webhook: str = Form(""),
    bodensee_temp_override: str = Form(""),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    if not org_s:
        org_s = OrgSettings(org_id=effective_org_id)
        db.add(org_s)

    org_s.weather_alert_mail = alert_mail.strip()[:255] or None
    webhook = alert_teams_webhook.strip()
    org_s.weather_alert_teams_webhook_url = webhook[:1000] if webhook.startswith("https://") else None

    bodensee_raw = bodensee_temp_override.strip()
    if bodensee_raw:
        try:
            from datetime import UTC, datetime
            org_s.bodensee_temp_override_c = float(bodensee_raw.replace(",", "."))
            org_s.bodensee_temp_override_at = datetime.now(UTC)
        except ValueError:
            pass
    else:
        org_s.bodensee_temp_override_c = None
        org_s.bodensee_temp_override_at = None

    db.commit()
    qs = f"org_id={effective_org_id}&saved=1" if is_sysadmin else "saved=1"
    return RedirectResponse(f"/admin/settings/wetter?{qs}#warnungen", status_code=303)


@router.post("/settings/wetter/alert-rule/{rule_id}")
async def weather_alert_rule_save(
    request: Request,
    rule_id: int,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    enabled: str = Form(""),
    vorwarnung: str = Form(""),
    eskalation: str = Form(""),
    channel_mail: str = Form(""),
    channel_teams: str = Form(""),
    mail_override: str = Form(""),
    teams_webhook_override: str = Form(""),
    cooldown_min: str = Form(""),
    params_json: str = Form(""),
    target_org_id: int | None = Form(None),
):
    import json

    from app.models.weather_alert import WeatherAlertRule

    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    rule = db.get(WeatherAlertRule, rule_id)
    if not rule or rule.org_id != effective_org_id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")

    rule.enabled         = enabled in ("1", "true", "on")
    rule.vorwarnung      = vorwarnung in ("1", "true", "on")
    rule.eskalation      = eskalation in ("1", "true", "on")
    rule.channel_mail    = channel_mail in ("1", "true", "on")
    rule.channel_teams   = channel_teams in ("1", "true", "on")
    rule.mail_override   = mail_override.strip()[:255] or None
    webhook = teams_webhook_override.strip()
    rule.teams_webhook_override = webhook[:1000] if webhook.startswith("https://") else None
    try:
        rule.cooldown_min = max(1, int(cooldown_min)) if cooldown_min.strip() else 60
    except ValueError:
        pass
    if params_json.strip():
        try:
            rule.params = json.loads(params_json)
        except (ValueError, json.JSONDecodeError):
            pass

    from datetime import UTC, datetime
    rule.updated_at = datetime.now(UTC)
    db.commit()

    qs = f"org_id={effective_org_id}&saved=1" if is_sysadmin else "saved=1"
    return RedirectResponse(f"/admin/settings/wetter?{qs}#warnungen", status_code=303)


@router.post("/settings/wetter/alert-test/mail")
async def weather_alert_test_mail(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    mail_to = org_s.weather_alert_mail if org_s else None
    error: str | None = None
    if not mail_to:
        error = "Kein E-Mail-Empfänger konfiguriert."
    else:
        try:
            from app.services.mail_service import _build_message, _send, get_smtp_cfg
            from app.models.master import FireDept
            org = db.query(FireDept).filter(FireDept.id == effective_org_id).first()
            smtp_cfg = get_smtp_cfg()
            betreff = f"[Wetterwarnung Test] – {org.name if org else ''}"
            body = "Dies ist eine Test-Wetterwarnung von Einsatzcockpit.\n\nGeoSphere Austria (CC BY 4.0)"
            msg = _build_message(to=mail_to, subject=betreff, body_txt=body, smtp_cfg=smtp_cfg)
            await _send(msg, smtp_cfg)
        except Exception as exc:
            error = str(exc)[:200]

    param = f"alert_mail_error={error.replace(' ', '+')}" if error else "alert_mail_ok=1"
    org_pre = f"org_id={effective_org_id}&" if is_sysadmin else ""
    return RedirectResponse(f"/admin/settings/wetter?{org_pre}{param}#warnungen", status_code=303)


@router.post("/settings/wetter/alert-test/teams")
async def weather_alert_test_teams(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    is_sysadmin = has_role(user, "system_admin")
    effective_org_id = target_org_id if (is_sysadmin and target_org_id) else user.org_id
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
    webhook = org_s.weather_alert_teams_webhook_url if org_s else None
    error: str | None = None
    if not webhook:
        error = "Kein Teams-Webhook konfiguriert."
    else:
        from app.services.weather_alert_dispatch import _post_teams
        ok = _post_teams(webhook, "Wetterwarnung Test", "Dies ist eine Test-Wetterwarnung.", None, "f59e0b")
        if not ok:
            error = "Teams-Post fehlgeschlagen (Webhook-URL prüfen)"

    param = f"alert_teams_error={error.replace(' ', '+')}" if error else "alert_teams_ok=1"
    org_pre = f"org_id={effective_org_id}&" if is_sysadmin else ""
    return RedirectResponse(f"/admin/settings/wetter?{org_pre}{param}#warnungen", status_code=303)


# ── Lokale Wetterstationen (Davis/Meteobridge) ───────────────────────────────

def _parse_coord(raw: str | None, lo: float, hi: float) -> float | None:
    """Parst eine optionale Koordinate; None bei leer/ungültig/außerhalb des Bereichs."""
    if not raw or not raw.strip():
        return None
    try:
        v = float(raw.strip().replace(",", "."))
    except ValueError:
        return None
    return v if lo <= v <= hi else None


@router.post("/settings/weather/add", response_class=HTMLResponse)
async def weather_station_add(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    name: str = Form(...),
    lat: str = Form(""),
    lng: str = Form(""),
    target_org_id: int | None = Form(None),
):
    from app.core.security import generate_weather_station_token, hash_api_key
    from app.models.weather import WeatherStation

    effective_org_id = target_org_id if has_role(user, "system_admin") and target_org_id else user.org_id
    org_id_param = effective_org_id if has_role(user, "system_admin") else None
    if not effective_org_id:
        return RedirectResponse("/admin/settings/wetter", status_code=303)

    name = name.strip()[:150]
    if not name:
        return templates.TemplateResponse(
            request, "admin/settings_wetter.html",
            _weather_settings_context(request, db, user, org_id_param,
                                      weather_error="Name darf nicht leer sein."),
        )

    raw = generate_weather_station_token()
    station = WeatherStation(
        org_id=effective_org_id,
        name=name,
        lat=_parse_coord(lat, -90.0, 90.0),
        lng=_parse_coord(lng, -180.0, 180.0),
        ingest_token_hash=hash_api_key(raw),
        active=True,
    )
    db.add(station)
    db.commit()

    return templates.TemplateResponse(
        request, "admin/settings_wetter.html",
        _weather_settings_context(request, db, user, org_id_param,
                                  new_station_token=raw, new_station_id=station.id),
    )


@router.post("/settings/weather/regenerate", response_class=HTMLResponse)
async def weather_station_regenerate(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    station_id: int = Form(...),
    target_org_id: int | None = Form(None),
):
    from app.core.security import generate_weather_station_token, hash_api_key
    from app.models.weather import WeatherStation

    effective_org_id = target_org_id if has_role(user, "system_admin") and target_org_id else user.org_id
    org_id_param = effective_org_id if has_role(user, "system_admin") else None

    station = db.get(WeatherStation, station_id)
    if not station or station.org_id != effective_org_id:
        raise HTTPException(status_code=404, detail="Station nicht gefunden")

    raw = generate_weather_station_token()
    station.ingest_token_hash = hash_api_key(raw)
    db.commit()

    return templates.TemplateResponse(
        request, "admin/settings_wetter.html",
        _weather_settings_context(request, db, user, org_id_param,
                                  new_station_token=raw, new_station_id=station.id),
    )


@router.post("/settings/weather/remove")
async def weather_station_remove(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    station_id: int = Form(...),
    target_org_id: int | None = Form(None),
):
    from app.models.weather import WeatherStation

    effective_org_id = target_org_id if has_role(user, "system_admin") and target_org_id else user.org_id
    station = db.get(WeatherStation, station_id)
    if station and station.org_id == effective_org_id:
        db.delete(station)
        db.commit()

    org_suffix = f"?org_id={effective_org_id}" if has_role(user, "system_admin") else ""
    return RedirectResponse(f"/admin/settings/wetter{org_suffix}#wetterstation", status_code=303)


# ── Organisations-Verwaltung (system_admin) ──────────────────────────────────

@router.get("/organisations", response_class=HTMLResponse)
def organisations_page(request: Request, db=Depends(get_db), user: User = Depends(require_system_admin)):
    orgs = db.query(FireDept).order_by(FireDept.name).all()
    seed_profiles = list_profiles(db)
    return templates.TemplateResponse(request, "admin/organisations.html", {
        "user": user,
        "orgs": orgs,
        "bos_values": BOS_VALUES,
        "seed_profiles": seed_profiles,
        "created": request.query_params.get("created"),
        "error": request.query_params.get("error"),
    })


@router.post("/organisations/new")
async def create_organisation(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    slug: str = Form(...),
    name: str = Form(...),
    color: str = Form("#d42225"),
    bos: str = Form("Feuerwehr"),
    contact_email: str = Form(""),
    seed_profile: str = Form(""),
    admin_email: str = Form(""),
    admin_name: str = Form(""),
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
        is_active=True,
    )
    db.add(org)
    db.flush()

    # Seed-Profil kopieren
    if seed_profile:
        apply_seed_profile(db, org.id, seed_profile)

    # Standard-KI-Prompts anlegen
    copy_default_prompts(db, org.id)

    db.commit()

    # Ersten org_admin einladen
    if admin_email:
        await _invite_org_admin(request, db, org, admin_email.strip(), admin_name.strip())

    return RedirectResponse("/admin/organisations?created=1", status_code=303)


async def _invite_org_admin(request: Request, db, org: FireDept, email: str, display_name: str) -> None:
    """Legt einen org_admin-User an und sendet einen Passwort-Set-Link."""
    import hashlib
    import secrets as sec
    from datetime import UTC, datetime, timedelta

    from app.config import settings as cfg
    from app.core.auth import hash_password
    from app.models.password_reset import PasswordResetToken
    from app.models.user import Role, User, UserRole
    from app.services.mail_service import send_password_reset

    # User bereits vorhanden?
    existing_user = db.query(User).filter(User.email == email.lower()).first()
    if existing_user:
        return

    tmp_pw = sec.token_urlsafe(24)
    new_user = User(
        username=email.split("@")[0][:50],
        display_name=display_name or email.split("@")[0],
        email=email.lower(),
        password_hash=hash_password(tmp_pw),
        org_id=org.id,
    )
    db.add(new_user)
    db.flush()

    org_admin_role = db.query(Role).filter(Role.code == "org_admin").first()
    if org_admin_role:
        db.add(UserRole(user_id=new_user.id, role_id=org_admin_role.id))
    db.flush()

    raw_token = sec.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    db.add(PasswordResetToken(
        user_id=new_user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        requesting_ip=request.client.host if request.client else None,
    ))
    db.flush()

    base = cfg.effective_public_base_url.rstrip("/")
    reset_url = f"{base}/passwort-zuruecksetzen?token={raw_token}"
    try:
        await send_password_reset(
            to=email.lower(), reset_url=reset_url,
            user_display_name=new_user.display_name,
            db=db,
        )
    except Exception:
        pass


@router.post("/organisations/{org_id}/toggle")
def toggle_organisation(org_id: int, db=Depends(get_db), user: User = Depends(require_system_admin)):
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    if org:
        org.is_active = not org.is_active
        db.commit()
    return RedirectResponse("/admin/organisations", status_code=303)


@router.post("/organisations/{org_id}/delete")
def delete_organisation(org_id: int, db=Depends(get_db), user: User = Depends(require_system_admin)):
    """Soft-Delete: setzt deleted_at auf jetzt; Purge nach 30 Tagen."""
    from datetime import UTC, datetime
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    if org and not org.deleted_at:
        org.deleted_at = datetime.now(UTC)
        org.is_active = False
        db.commit()
    return RedirectResponse("/admin/organisations", status_code=303)


@router.post("/organisations/{org_id}/restore")
def restore_organisation(org_id: int, db=Depends(get_db), user: User = Depends(require_system_admin)):
    """Hebt Soft-Delete innerhalb der 30-Tage-Frist wieder auf."""
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    if org and org.deleted_at:
        org.deleted_at = None
        org.is_active = True
        db.commit()
    return RedirectResponse("/admin/organisations", status_code=303)


@router.get("/seed-vorlagen", response_class=HTMLResponse)
def seed_templates_page(request: Request, db=Depends(get_db), user: User = Depends(require_system_admin)):
    import json as json_lib
    from collections import defaultdict
    raw = (
        db.query(SeedTemplate)
        .order_by(SeedTemplate.profile, SeedTemplate.type, SeedTemplate.display_order)
        .all()
    )
    by_profile: dict[str, dict] = {}
    for t in raw:
        if t.profile not in by_profile:
            by_profile[t.profile] = {"label": t.profile_label, "types": defaultdict(list)}
        try:
            d = json_lib.loads(t.data)
        except Exception:
            d = {}
        by_profile[t.profile]["types"][t.type].append({"id": t.id, "data": d, "order": t.display_order})
    return templates.TemplateResponse(request, "admin/seed_templates.html", {
        "user": user,
        "by_profile": by_profile,
    })


# ── System-Modul-Toggles (system_admin only) ─────────────────────────────────

@router.post("/settings/system/uas-toggle")
def toggle_uas_system(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_system_admin),
    enabled_raw: str = Form(""),
):
    """Systemweiten UAS-Flag umschalten (nur system_admin).

    Setzt SystemSettings key "uas_module_enabled" auf "true" oder "false".
    Beim Ausschalten bleiben alle Org-Daten und Org-Toggles erhalten;
    Re-Aktivierung stellt die Sichtbarkeit sofort wieder her.
    """
    new_enabled = enabled_raw in ("1", "true", "on")
    new_value = "true" if new_enabled else "false"

    row = db.query(SystemSettings).filter(SystemSettings.key == "uas_module_enabled").first()
    old_value = row.value if row else "false"

    if row is None:
        from datetime import UTC, datetime
        row = SystemSettings(
            key="uas_module_enabled",
            value=new_value,
            updated_at=datetime.now(UTC),
            updated_by_user_id=user.id,
        )
        db.add(row)
    else:
        from datetime import UTC, datetime
        row.value = new_value
        row.updated_at = datetime.now(UTC)
        row.updated_by_user_id = user.id

    from app.core.audit import write_audit
    write_audit(
        db,
        "uas.system_toggle",
        user_id=user.id,
        payload={"alt": old_value, "neu": new_value},
        ip=request.client.host if request.client else None,
    )
    db.commit()

    org_suffix = f"&org_id={request.query_params.get('org_id', '')}" if request.query_params.get("org_id") else ""
    return RedirectResponse(f"/admin/settings?saved=1{org_suffix}", status_code=303)


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


def _org_flags_dict(org_settings) -> dict:
    """Hilfsfunktion: OrgSettings-Feature-Flags als einfaches Dict für Templates."""
    _MI_KEYS = [
        "mi_feature_stab", "mi_feature_funkjournal", "mi_feature_meldungen",
        "mi_feature_sektoren", "mi_feature_karte", "mi_feature_zeitreise",
        "mi_feature_ressourcen", "mi_feature_uebergreifend", "mi_feature_geraeteverleih",
    ]
    return {k: bool(getattr(org_settings, k, True)) for k in _MI_KEYS}


# ── GSL-Einstellungen ────────────────────────────────────────────────────────

@router.get("/gsl-einstellungen", response_class=HTMLResponse)
def gsl_einstellungen(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
):
    org_id = user.org_id
    if not org_id:
        raise HTTPException(403)
    org_settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
    return templates.TemplateResponse(request, "admin/gsl_einstellungen.html", {
        "user": user,
        "org_settings": org_settings,
        "org_flags": _org_flags_dict(org_settings),
    })


@router.post("/gsl-einstellungen", response_class=HTMLResponse)
async def gsl_einstellungen_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
):
    org_id = user.org_id
    if not org_id:
        raise HTTPException(403)

    form = await request.form()
    org_settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
    if not org_settings:
        raise HTTPException(404, "Org-Settings nicht gefunden")

    def _bool(key: str) -> bool:
        return form.get(key, "") in ("1", "on", "true", "yes")

    def _int_or_none(key: str):
        v = str(form.get(key, "")).strip()
        return int(v) if v.isdigit() else None

    # Feature-Flags
    org_settings.mi_feature_stab           = _bool("mi_feature_stab")
    org_settings.mi_feature_funkjournal    = _bool("mi_feature_funkjournal")
    org_settings.mi_feature_meldungen      = _bool("mi_feature_meldungen")
    org_settings.mi_feature_sektoren       = _bool("mi_feature_sektoren")
    org_settings.mi_feature_karte          = _bool("mi_feature_karte")
    org_settings.mi_feature_zeitreise      = _bool("mi_feature_zeitreise")
    org_settings.mi_feature_ressourcen     = _bool("mi_feature_ressourcen")
    org_settings.mi_feature_uebergreifend  = _bool("mi_feature_uebergreifend")
    org_settings.mi_feature_geraeteverleih = _bool("mi_feature_geraeteverleih")

    # Lagemeldungs-Regelkreis
    lm_interval = _int_or_none("gsl_lagemeldung_interval_minutes")
    org_settings.gsl_lagemeldung_interval_minutes = lm_interval
    lm_sofort = _int_or_none("gsl_lagemeldung_interval_sofort_minutes")
    org_settings.gsl_lagemeldung_interval_sofort_minutes = lm_sofort
    org_settings.gsl_lagemeldung_auto_auftrag = _bool("gsl_lagemeldung_auto_auftrag")

    # Geräteverleih-Konfiguration
    erinnerung_h = _int_or_none("gsl_verleih_erinnerung_stunden")
    org_settings.gsl_verleih_erinnerung_stunden = erinnerung_h
    org_settings.gsl_verleih_sms_ausleih_text    = str(form.get("gsl_verleih_sms_ausleih_text", "")).strip() or None
    org_settings.gsl_verleih_sms_erinnerung_text = str(form.get("gsl_verleih_sms_erinnerung_text", "")).strip() or None

    # Allgemein
    org_settings.mi_auto_adopt = _bool("mi_auto_adopt")

    db.commit()

    return templates.TemplateResponse(request, "admin/gsl_einstellungen.html", {
        "user": user,
        "org_settings": org_settings,
        "org_flags": _org_flags_dict(org_settings),
        "saved": True,
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
