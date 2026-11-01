"""Persoenliches Systemadmin-Cockpit mit denselben Services wie Admin-MCP."""
from __future__ import annotations

import hashlib
import hmac
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.admin_mcp.code_tools import collect_code, safe_title
from app.admin_mcp.diagnose_tools import collect_diagnose
from app.admin_mcp.login_hinweise import HINWEISE
from app.admin_mcp.masking import mask_text
from app.admin_mcp.registry import OVERVIEW_EXCLUDED, READ_TOOLS, collect
from app.admin_mcp.repair_tools import collect_repair
from app.config import settings
from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.admin_repair import AdminRepairRun
from app.models.app_geraet import AppGeraet, AppGeraetFehler
from app.models.diagnose import DiagFinding, DiagRun
from app.models.master import FireDept, SystemSettings
from app.models.user import AuditLog, DeviceToken, User
from app.services import app_geraet_auswertung as geraete
from app.services import diag_schedule, github_token, totp_service
from app.services.diagnose.registry import CHECKS
from app.services.diagnose.runner import einsatz_aktiv
from app.services.repair.registry import ACTIONS
from app.services.repair.runner import gates, prepare_session, public_run


def login_grund(entry: AuditLog) -> str | None:
    """Nur bekannte Login-Gruende zeigen, niemals freie Audit-Payloads."""
    if entry.action not in ("admin_mcp.login_failed", "admin_mcp.denied"):
        return None
    try:
        payload = json.loads(entry.payload_json or "{}")
    except (TypeError, ValueError):
        return None
    grund = payload.get("grund") if isinstance(payload, dict) else None
    if not isinstance(grund, str):
        return None
    return HINWEISE.get(grund, "").replace("{minuten}", "wenigen") or None


def personal(request: Request, db: Session = Depends(get_db), _=Depends(require_role("system_admin"))):
    set_tenant_context(db, None)
    if (request.state.is_device or request.state.user.is_device
            or request.state.qr_incident_id is not None or request.state.qr_lage_id is not None):
        raise HTTPException(403, "Nur persoenliche Admin-Konten")


router = APIRouter(prefix="/admin/systemdiagnose", dependencies=[Depends(personal)])
SECTIONS = ("gesamt", "diagnose", "befunde", "laeufe", "reparaturen", "github", "audit", "geraete")


def numbers(value):
    """Keine freien Feldnamen oder Texte aus persistierter Evidenz anzeigen."""
    if isinstance(value, dict):
        return [numbers(v) for v in list(value.values())[:50]]
    if isinstance(value, list):
        return [numbers(v) for v in value[:50]]
    return value if type(value) in (int, float, bool) or value is None else "[MASKIERT]"


def safe_scope(run):
    try:
        return ACTIONS[run.aktion_id].scope_schema.model_validate(run.scope).model_dump()
    except (ValueError, KeyError):
        return {}


def audit(db, request, action, ident=None):
    write_audit(db, "diag.ui." + action, user_id=request.state.user.id, entity_id=ident)
    db.commit()


def page_number(request):
    try:
        return max(0, min(int(request.query_params.get("page", "0")), 10000))
    except ValueError as exc:
        raise HTTPException(400, "Ungueltige Seite") from exc


def render(request, db, section, **extra):
    if section not in SECTIONS:
        raise HTTPException(404)
    set_tenant_context(db, None)
    context = {"user": request.state.user, "section": section, "sections": SECTIONS,
               "checks": CHECKS, "actions": ACTIONS, "safe_title": safe_title, "numbers": numbers,
               "github_date": github_token.parse_date, "mask_text": mask_text, "safe_scope": safe_scope, **extra}
    if section == "gesamt":
        context["summary"] = collect(db, "admin_system_uebersicht")
        context["parts"] = {name: collect(db, name) for name in READ_TOOLS
                            if name not in OVERVIEW_EXCLUDED}
    elif section == "diagnose":
        context["run"] = db.scalar(select(DiagRun).order_by(DiagRun.id.desc()).limit(1))
        context["throttled"] = einsatz_aktiv(db)
    elif section == "befunde":
        filters = {key: value for key in ("modul", "schweregrad", "status", "cursor")
                   if (value := request.query_params.get(key))}
        try:
            result = collect_diagnose(db, "admin_fehler_liste", request.state.user.id, "", **filters)
        except ValueError as exc:
            raise HTTPException(400, "Ungueltiger Filter") from exc
        ids = [row["id"] for row in result["evidence"]["befunde"]]
        context["findings"] = sorted(db.scalars(select(DiagFinding).where(DiagFinding.id.in_(ids))),
                                      key=lambda r: ids.index(r.id))
        context["cursor"] = result["evidence"]["cursor"]
        context["modules"] = sorted({c.modul for c in CHECKS.values()})
    elif section == "laeufe":
        page = page_number(request)
        context["runs"] = list(db.scalars(select(DiagRun).order_by(DiagRun.id.desc()).offset(page * 25).limit(25)))
        context["page"] = page
    elif section == "reparaturen":
        context["repairs"] = list(db.scalars(select(AdminRepairRun).where(
            AdminRepairRun.angefordert_von == request.state.user.id).order_by(AdminRepairRun.id.desc()).limit(25)))
        context["repair_enabled"] = totp_service.enabled(db) and (
            (row := db.get(SystemSettings, "admin_repair_enabled")) is not None and row.value == "true")
    elif section == "github":
        context["ci"] = collect_code(db, "admin_ci_status")
        context["analysis"] = collect_code(db, "admin_code_analyse")
    elif section == "geraete":
        filters = {key: value for key in ("version", "android_version", "geraeteklasse", "hersteller")
                   if (value := request.query_params.get(key))}
        try:
            numeric = {key: int(value) for key in ("org_id", "sdk_max")
                       if (value := request.query_params.get(key))}
            context["devices"] = geraete.liste(db, **filters, **numeric,
                nur_probleme=request.query_params.get("nur_probleme") == "1",
                lange_nicht_gesehen=request.query_params.get("lange_nicht_gesehen") == "1",
                page=page_number(request))
        except ValueError as exc:
            raise HTTPException(400, "Ungueltiger Geraetefilter") from exc
        context["verteilung"] = geraete.verteilung(db)
    elif section == "audit":
        page = page_number(request)
        context["entries"] = list(db.scalars(select(AuditLog).where(or_(
            *(AuditLog.action.startswith(prefix) for prefix in (
                "admin_mcp.", "diag.", "repair.", "admin_repair.", "github_token.",
                "mfa.", "admin.mfa."))))
            .order_by(AuditLog.id.desc()).offset(page * 25).limit(25)))
        context["page"] = page
        context["login_grund"] = login_grund
    response = templates.TemplateResponse(request, "admin/systemdiagnose/_panel.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("")
def cockpit(request: Request, db: Session = Depends(get_db)):
    audit(db, request, "opened")
    response = templates.TemplateResponse(request, "admin/systemdiagnose/index.html", {
        "user": request.state.user, "sections": SECTIONS,
        "geraet_id": (request.query_params.get("geraet", "")
                      if request.query_params.get("geraet", "").isdigit() else "")})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/teil/{section}")
def panel(section: str, request: Request, db: Session = Depends(get_db)):
    return render(request, db, section)


@router.get("/befund/{finding_id}")
def finding_detail(finding_id: int, request: Request, db: Session = Depends(get_db)):
    data = collect_diagnose(db, "admin_fehler_details", request.state.user.id, "", id=finding_id)["evidence"]
    if "id" not in data:
        raise HTTPException(404)
    audit(db, request, "finding", finding_id)
    row = db.get(DiagFinding, finding_id)
    response = templates.TemplateResponse(request, "admin/systemdiagnose/_finding.html", {
        "finding": row, "safe_title": safe_title, "numbers": numbers,
        "check": CHECKS.get(row.check_id) if row else None})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/start")
def start(request: Request, db: Session = Depends(get_db)):
    form = run_async(request.form)
    try:
        result = collect_diagnose(db, "admin_diagnose_starten", request.state.user.id, str(uuid4()),
            umfang=str(form.get("umfang", "schnell")),
            checks=[str(form["check"])] if form.get("check") else None)
    except ValueError as exc:
        audit(db, request, "start_rejected")
        raise HTTPException(400, "Ungueltige Pruefung") from exc
    ident = result["evidence"].get("run_id")
    if ident:
        row = db.get(DiagRun, ident)
        if row:
            row.quelle = "manual"
    audit(db, request, "start" if ident else "start_limited", ident)
    message = "Lauf angefordert" if ident else "Rate-Limit erreicht: ein aktiver Lauf, maximal 6 Starts/Stunde."
    return render(request, db, "diagnose", message=message)


@router.post("/codex/{finding_id}")
def codex(finding_id: int, request: Request, db: Session = Depends(get_db)):
    row = db.get(DiagFinding, finding_id)
    if row is None:
        raise HTTPException(404)
    result = collect_code(db, "admin_codex_auftrag_erstellen", finding_key=row.finding_key)
    audit(db, request, "codex", finding_id)
    response = templates.TemplateResponse(request, "admin/systemdiagnose/_codex.html", {
        "markdown": result["evidence"]["markdown"]})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/reparatur/{action}")
def repair(action: str, request: Request, db: Session = Depends(get_db)):
    prepare_session(db)
    set_tenant_context(db, None)
    form = run_async(request.form)
    user = request.state.user
    if action not in ("vorschlagen", "simulieren", "ausfuehren", "verifizieren"):
        raise HTTPException(404)
    try:
        gates(db)
        args: dict = {}
        if action == "vorschlagen":
            args["id"] = int(str(form.get("finding_id", "0")))
        elif action == "simulieren" and form.get("aktion_id"):
            args["aktion_id"] = str(form["aktion_id"])
        else:
            args["run_id"] = int(str(form.get("run_id", "0")))
        if action == "simulieren":
            args["scope"] = json.loads(str(form.get("scope", "{}")))
        if action == "ausfuehren":
            if not totp_service.fresh(request.state.mfa_at):
                audit(db, request, "repair_stepup_required")
                raise HTTPException(403, "Frische MFA erforderlich: /mfa/verify")
            args.update(simulation_hash=str(form.get("simulation_hash", "")),
                        bestaetigt=form.get("bestaetigt") == "true",
                        idempotency_key=hmac.new(settings.SECRET_KEY.encode(),
                            f"web:{user.id}:{args['run_id']}:{form.get('simulation_hash', '')}".encode(),
                            hashlib.sha256).hexdigest(), approval_token=form.get("approval_token"))
        result = collect_repair(db, "admin_reparatur_" + action, user.id, user.session_epoch,
                                "web:" + uuid4().hex, web=True, **args)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        db.rollback()
        audit(db, request, "repair_rejected")
        raise HTTPException(400, "Reparaturschalter oder Parameter pruefen") from exc
    audit(db, request, "repair_" + action)
    return render(request, db, "reparaturen", repair_result=result,
                  public_run=public_run)


@router.post("/einstellungen")
def save_settings(request: Request, db: Session = Depends(get_db)):
    form = run_async(request.form)
    try:
        values = diag_schedule.validate({key: str(form.get(key, default))
                                        for key, default in diag_schedule.DEFAULTS.items()})
    except ValueError as exc:
        audit(db, request, "settings_rejected")
        raise HTTPException(400, "Systemdiagnose-Einstellungen pruefen") from exc
    for key, value in values.items():
        row = db.get(SystemSettings, key)
        if row is None:
            row = SystemSettings(key=key)
            db.add(row)
        row.value, row.updated_by_user_id = value, request.state.user.id
    audit(db, request, "settings")
    return templates.TemplateResponse(request, "admin/systemdiagnose/_settings.html", {
        "diag_settings": values, "saved": True})


@router.get("/geraet/{geraet_id}")
def geraet_detail(geraet_id: int, request: Request, db: Session = Depends(get_db)):
    row = db.get(AppGeraet, geraet_id)
    if row is None:
        raise HTTPException(404, "App-Geraet nicht gefunden")
    errors = list(db.scalars(select(AppGeraetFehler).where(
        AppGeraetFehler.geraet_id == row.id).order_by(AppGeraetFehler.last_at.desc())))
    token = db.get(DeviceToken, row.device_token_id) if row.device_token_id else None
    soll = geraete.soll_version()
    codes = geraete.befunde(row, soll, token=token, fehler=errors)
    audit(db, request, "geraet", geraet_id)
    response = templates.TemplateResponse(request, "admin/systemdiagnose/_geraet.html", {
        "user": request.state.user, "geraet": row, "fehler": errors, "soll": soll,
        "benutzer": db.get(User, row.user_id), "token": token,
        "org": db.get(FireDept, row.org_id) if row.org_id else None,
        "ampel": geraete.ampel(row.app_version, soll), "gesundheit": geraete.gesundheit(codes),
        "befunde": [geraete.REGELN[code][0] for code in codes],
        "labels": GERAET_LABELS, "github_date": github_token.parse_date})
    response.headers["Cache-Control"] = "no-store"
    return response


GERAET_LABELS = {
    "app": "App",
    "geraet": "Geraet",
    "android": "Android",
    "display": "Anzeige",
    "webview": "WebView",
    "locale": "Sprache und Uhr",
    "version_name": "Versionsname",
    "version_code": "Versionscode",
    "build_variant": "Build-Variante",
    "target_sdk": "Ziel-SDK",
    "min_sdk": "Mindest-SDK",
    "first_install_at": "Erste Installation",
    "last_update_at": "Letztes Update",
    "hersteller": "Hersteller",
    "marke": "Marke",
    "modell": "Modell",
    "geraetename": "Geraetename",
    "klasse": "Geraeteklasse",
    "foldable": "Faltbar",
    "abi": "Prozessorarchitektur",
    "ram_mb_total": "Arbeitsspeicher gesamt (MB)",
    "ram_mb_free": "Arbeitsspeicher frei (MB)",
    "speicher_mb_free": "Speicher frei (MB)",
    "akku_prozent": "Akku (%)",
    "laedt": "Wird geladen",
    "akkusparer": "Energiesparmodus",
    "sim_anzahl": "SIM-Anzahl",
    "nfc": "NFC",
    "kamera": "Kamera",
    "telefonie": "Telefonie",
    "version": "Version",
    "sdk_int": "SDK",
    "sicherheitspatch": "Sicherheitspatch",
    "build_id": "Build-ID",
    "oberflaeche": "Oberflaeche",
    "breite_px": "Breite (Pixel)",
    "hoehe_px": "Hoehe (Pixel)",
    "dpi": "Pixeldichte (DPI)",
    "dichte_bucket": "Dichteklasse",
    "breite_dp": "Breite (DP)",
    "hz": "Bildfrequenz (Hz)",
    "ausrichtung": "Ausrichtung",
    "schrift_skala": "Schriftskalierung",
    "anzeige_skala": "Anzeigeskalierung",
    "paket": "Paket",
    "sprache": "Sprache",
    "zeitzone": "Zeitzone",
    "uhr_abweichung_s": "Uhrabweichung (Sekunden)",
    "el_alarm_tone": "Alarmton",
    "el_alarm_volume_percent": "Alarmlautstaerke (%)",
    "el_alarm_override_silent": "Lautlosmodus bei Alarm uebergehen",
    "el_live_enabled": "Live-Verbindung aktiviert",
    "el_gateway_receive_enabled": "Gateway-Empfang aktiviert",
    "auto_update": "Automatische Updates",
    "base_url_host": "Server-Hostname",
    "benachrichtigungen": "Benachrichtigungen",
    "akku_optimierung_aus": "Akku-Optimierung ausgenommen",
    "vollbild_intent": "Vollbild-Intent",
    "dnd_zugriff": "Nicht-stoeren-Zugriff",
    "standort": "Standortberechtigung",
    "sms": "SMS",
    "netz": "Netz",
    "poller_laeuft": "Abfragedienst laeuft",
    "keepalive": "Verbindung halten",
    "gateway_verbunden": "Gateway verbunden",
    "fcm_registriert": "FCM registriert",
    "fcm_faehigkeiten": "FCM-Faehigkeiten",
    "device_token_vorhanden": "Geraetezugang vorhanden",
    "gateway_token_vorhanden": "Gateway-Zugang vorhanden",
    "letzter_poll_ok_at": "Letzte erfolgreiche Abfrage",
    "letzte_push_bestaetigung_at": "Letzte Push-Bestaetigung",
    "letzter_kontaktsync_at": "Letzter Kontaktabgleich"
}
