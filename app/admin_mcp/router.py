"""CSRF-geschuetzter Admin-OAuth-Login und explizite UI-Schalter."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.admin_mcp.context import DENIED, allowed
from app.admin_mcp.provider import raw, revoke_user
from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.rate_limit import limiter
from app.core.security import hash_api_key
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.mcp.router import _form_action_origin
from app.models.admin_mcp import AdminMCPOAuthCode, AdminMCPOAuthToken
from app.models.admin_security import utcnow
from app.models.master import SystemSettings
from app.models.user import User
from app.services import totp_service as totp
from app.services.auth_service import authenticate_user

router = APIRouter()


def _page(request, row, vorgang, error=None, status_code=200):
    response = templates.TemplateResponse(request, "mcp/admin_anmelden.html",
        {"vorgang": vorgang, "error": error}, status_code=status_code)
    if row:
        response.headers["x-ec-form-action-extra"] = _form_action_origin(row.redirect_uri)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _flow(db, vorgang):
    set_tenant_context(db, None)
    return db.query(AdminMCPOAuthCode).filter(AdminMCPOAuthCode.code_hash == hash_api_key(vorgang),
        AdminMCPOAuthCode.user_id.is_(None), AdminMCPOAuthCode.expires_at > utcnow()).with_for_update().first()


@router.get("/mcp/admin/anmelden")
def anmelden(request: Request, vorgang: str, db: Session = Depends(get_db)):
    row = _flow(db, vorgang)
    return _page(request, row, vorgang, None if row else DENIED, 200 if row else 401)


@router.post("/mcp/admin/anmelden")
@(limiter.limit("5/minute") if limiter else lambda f: f)
def anmelden_submit(request: Request, vorgang: str = Form(...), username: str = Form(...),
    password: str = Form(...), code: str = Form(""), db: Session = Depends(get_db)):
    set_tenant_context(db, None)
    user, _ = authenticate_user(db, username, password, request.client.host if request.client else None)
    row = _flow(db, vorgang)
    event = "login_failed"
    success = False
    if row and user:
        if allowed(db, user):
            success = not totp.enabled(db) or totp.verify(db, user.id, code)
        else:
            event = "denied"
    if not success:
        write_audit(db, "admin_mcp." + event, user_id=user.id if user else None)
        db.commit()  # Auch MFA-Sperren/Replay-Schutz und fehlgeschlagene Versuche persistieren.
        return _page(request, row, vorgang, DENIED, 401)
    assert row is not None and user is not None
    value = raw()
    row.code_hash, row.user_id, row.session_epoch = hash_api_key(value), user.id, user.session_epoch
    row.mfa_at = utcnow() if totp.enabled(db) else None
    write_audit(db, "admin_mcp.login", user_id=user.id)
    db.commit()
    query = [("code", value)] + ([("state", row.state)] if row.state else [])
    parsed = urlsplit(row.redirect_uri)
    response = RedirectResponse(urlunsplit(parsed._replace(
        query=urlencode(list(parse_qsl(parsed.query)) + query))), status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/admin/system-einstellungen/admin-mcp")
def global_switch(request: Request, enabled: str = Form("false"), db: Session = Depends(get_db),
                  _=Depends(require_role("system_admin"))):
    if request.state.is_device or request.state.qr_incident_id or request.state.qr_lage_id:
        raise HTTPException(403, "Nur persoenliche Admin-Konten")
    value = enabled == "true"
    row = db.get(SystemSettings, "admin_mcp_enabled")
    if row is None:
        row = SystemSettings(key="admin_mcp_enabled")
        db.add(row)
    row.value, row.updated_by_user_id = "true" if value else "false", request.state.user.id
    if not value:
        for token in db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.revoked_at.is_(None)):
            token.revoked_at = utcnow()
    write_audit(db, "admin_mcp.global_changed", user_id=request.state.user.id, payload={"enabled": value})
    db.commit()
    return RedirectResponse("/admin/system-einstellungen?saved=1", status_code=303)


@router.post("/admin/system-einstellungen/admin-mcp/zugang")
def user_switch(request: Request, user_id: int = Form(...), enabled: str = Form("false"),
                db: Session = Depends(get_db), _=Depends(require_role("system_admin"))):
    target = db.query(User).execution_options(include_all_tenants=True).filter(User.id == user_id).first()
    if not target or not target.is_system_admin or target.is_device:
        raise HTTPException(404)
    if request.state.is_device or request.state.qr_incident_id or request.state.qr_lage_id:
        raise HTTPException(403, "Nur persoenliche Admin-Konten")
    value = enabled == "true"
    if value and totp.enabled(db) and not totp.fresh(request.state.mfa_at):
        raise HTTPException(403, "Zuerst 2FA frisch bestaetigen (maximal 5 Minuten)")
    target.admin_mcp_access = value
    revoke_user(db, target.id)
    write_audit(db, "admin_mcp.access_changed", user_id=request.state.user.id,
        entity_type="user", entity_id=target.id, payload={"enabled": value})
    db.commit()
    return RedirectResponse("/admin/system-einstellungen?saved=1", status_code=303)


@router.post("/admin/system-einstellungen/admin-mcp/reparatur")
def repair_switch(request: Request, enabled: str = Form("false"), low_auto: str = Form(""),
                  db: Session = Depends(get_db), _=Depends(require_role("system_admin"))):
    from app.services.repair.registry import ACTIONS

    if request.state.is_device or request.state.qr_incident_id or request.state.qr_lage_id:
        raise HTTPException(403, "Nur persoenliche Admin-Konten")
    ids = sorted({value.strip() for value in low_auto.split(",") if value.strip()})
    if any(value not in ACTIONS or ACTIONS[value].risiko != "low" for value in ids):
        raise HTTPException(400, "Nur registrierte LOW-Aktionen freischaltbar")
    if enabled == "true" and not totp.enabled(db):
        raise HTTPException(400, "2FA erforderlich")
    if enabled == "true" and not totp.fresh(request.state.mfa_at):
        raise HTTPException(403, "Zuerst 2FA frisch bestaetigen")
    for key, value in (("admin_repair_enabled", enabled), ("admin_repair_low_auto", ",".join(ids))):
        row = db.get(SystemSettings, key)
        if row is None:
            row = SystemSettings(key=key)
            db.add(row)
        row.value, row.updated_by_user_id = value, request.state.user.id
    write_audit(db, "admin_repair.global_changed", user_id=request.state.user.id,
                payload={"enabled": enabled == "true", "low_auto": ids})
    db.commit()
    return RedirectResponse("/admin/system-einstellungen?saved=1", status_code=303)


def _repair_page(request, run, **context):
    response = templates.TemplateResponse(request, "admin/repair_approval.html", {"run": run, **context})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _approval_run(request, db, run_id):
    from app.services.repair.runner import check_simulation, get_run, live_user

    set_tenant_context(db, None)
    if request.state.is_device or request.state.qr_incident_id or request.state.qr_lage_id:
        raise HTTPException(403, "Nur persoenliche Admin-Konten")
    try:
        live_user(db, request.state.user.id, request.state.user.session_epoch)
        run = get_run(db, run_id, request.state.user.id)
        check_simulation(run, run.simulation_hash or "")
        if run.risiko != "high":
            raise ValueError("Keine HIGH-Freigabe moeglich")
        return run
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/admin/reparatur/freigabe/{run_id}")
def repair_approval(request: Request, run_id: int, db: Session = Depends(get_db),
                    _=Depends(require_role("system_admin"))):
    return _repair_page(request, _approval_run(request, db, run_id))


@router.post("/admin/reparatur/freigabe/{run_id}")
@(limiter.limit("5/minute") if limiter else lambda f: f)
def repair_approval_submit(request: Request, run_id: int, code: str = Form(...),
                           simulation_hash: str = Form(...), db: Session = Depends(get_db),
                           _=Depends(require_role("system_admin"))):
    import time
    from uuid import uuid4

    from app.routers.ui_mfa import _verified_cookie
    from app.services.repair.runner import action_hash, audit, issue_approval

    run = _approval_run(request, db, run_id)
    correlation = str(uuid4())
    original = action_hash(run)
    if run.simulation_hash != simulation_hash or not totp.verify(db, request.state.user.id, code):
        audit(db, "abgelehnt", request.state.user.id, correlation, run.id, "starke_authentifizierung_fehlgeschlagen")
        db.commit()
        return _repair_page(request, run, error="Code oder Simulation ungueltig, verbraucht oder gesperrt.")
    # Ein Session-MFA-Marker ersetzt niemals die neue Code-Prüfung für diesen Lauf.
    verified_at = int(time.time())
    db.refresh(run, with_for_update=True)
    if not totp.fresh(verified_at):
        db.commit()
        raise HTTPException(403, "Frische 2FA erforderlich")
    if action_hash(run) != original:
        db.commit()  # Code verbraucht halten, auch wenn die Simulation inzwischen verändert wurde.
        raise HTTPException(409, "Simulation geaendert")
    try:
        token = issue_approval(db, run, request.state.user.id, correlation)
    except ValueError as exc:
        audit(db, "abgelehnt", request.state.user.id, correlation, run.id, "simulation_nicht_mehr_freigebbar")
        db.commit()  # Auch bei Ablauf während der Prüfung bleibt der Faktor verbraucht.
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    response = _repair_page(request, run, approval_token=token)
    _verified_cookie(request, request.state.user, response)
    return response
