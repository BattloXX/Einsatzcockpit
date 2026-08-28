"""Admin-Oberflaeche fuer Dienststatus, Empfaenger und Monitoring-Tokens."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User

router = APIRouter(prefix="/admin/systemstatus", tags=["dienst-monitor-ui"])


def _org_id(user: User, target_org_id: int | None = None) -> int:
    org_id = target_org_id if has_role(user, "system_admin") and target_org_id else user.org_id
    if not org_id:
        raise HTTPException(400, "Keine Organisation ausgewählt")
    return org_id


def _context(request: Request, db, user: User, org_id: int, **extra) -> dict:
    from app.models.dienst_monitor import DIENST_LABELS, DienstMonitorLog, DienstMonitorToken, DienstStatus
    from app.models.master import FireDept, OrgSettings, SystemSettings
    from app.services.dienst_monitor_service import pruefe_dienste

    org = db.get(FireDept, org_id)
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    rows = {
        r.key: r
        for r in db.query(DienstStatus)
        .filter(DienstStatus.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .all()
    }
    checks = pruefe_dienste(db, org_id)
    loop = db.get(SystemSettings, "dienst_monitor_last_run")
    loop_letzter_lauf = loop.value if loop else None
    try:
        loop_letzter_lauf_dt = datetime.fromisoformat(loop_letzter_lauf) if loop_letzter_lauf else None
    except (ValueError, TypeError):
        loop_letzter_lauf_dt = None
    ctx = {
        "request": request,
        "user": user,
        "org": org,
        "org_settings": settings,
        "checks": checks,
        "status_rows": rows,
        "loop_letzter_lauf": loop_letzter_lauf,
        "loop_letzter_lauf_dt": loop_letzter_lauf_dt,
        "labels": DIENST_LABELS,
        "dienst_keys": list(DIENST_LABELS.keys()),
        "tokens": db.query(DienstMonitorToken)
        .filter(DienstMonitorToken.org_id == org_id)
        .order_by(DienstMonitorToken.created_at.desc())
        .all(),
        "logs": db.query(DienstMonitorLog)
        .filter(DienstMonitorLog.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .order_by(DienstMonitorLog.gesendet_am.desc())
        .limit(50)
        .all(),
        "target_org_id": org_id if has_role(user, "system_admin") else None,
    }
    ctx.update(extra)
    return ctx


@router.get("", response_class=HTMLResponse)
def seite(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    return templates.TemplateResponse(
        request, "admin/systemstatus.html", _context(request, db, user, _org_id(user, org_id))
    )


@router.get("/liste", response_class=HTMLResponse)
def liste(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    return templates.TemplateResponse(
        request, "admin/_systemstatus_liste.html", _context(request, db, user, _org_id(user, org_id))
    )


@router.post("/empfaenger", response_class=HTMLResponse)
def empfaenger(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    mail: str = Form(""),
    teams_webhook: str = Form(""),
    sms: str = Form(""),
    karenz_min: int = Form(5),
    wiederholung_min: int = Form(60),
    enabled: str = Form(""),
    target_org_id: int | None = Form(None),
):
    from app.core.audit import write_audit
    from app.models.master import OrgSettings

    org_id = _org_id(user, target_org_id)
    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if row is None:
        row = OrgSettings(org_id=org_id)
        db.add(row)
    row.dienst_monitor_enabled = enabled == "on"
    row.dienst_monitor_mail = mail.strip()[:255] or None
    webhook = teams_webhook.strip()
    row.dienst_monitor_teams_webhook_url = webhook[:1000] if webhook.startswith("https://") else None
    row.dienst_monitor_sms = sms.strip()[:255] or None
    row.dienst_monitor_karenz_min = min(1440, max(1, karenz_min))
    row.dienst_monitor_wiederholung_min = min(10080, max(1, wiederholung_min))
    write_audit(db, "admin.dienst_monitor.empfaenger_geaendert", org_id=org_id, user_id=user.id)
    db.commit()
    return templates.TemplateResponse(
        request, "admin/systemstatus.html", _context(request, db, user, org_id, gespeichert=True)
    )


@router.post("/token/neu", response_class=HTMLResponse)
def token_neu(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    label: str = Form(""),
    target_org_id: int | None = Form(None),
):
    from app.core.audit import write_audit
    from app.core.security import generate_api_key, hash_api_key
    from app.models.dienst_monitor import DienstMonitorToken

    org_id = _org_id(user, target_org_id)
    raw = generate_api_key()
    bezeichnung = label.strip()[:150] or "Uptime Kuma"
    db.add(
        DienstMonitorToken(
            token_hash=hash_api_key(raw),
            label=bezeichnung,
            org_id=org_id,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    write_audit(
        db, "admin.dienst_monitor.token_angelegt", org_id=org_id, user_id=user.id, payload={"label": bezeichnung}
    )
    db.commit()
    return templates.TemplateResponse(
        request, "admin/systemstatus.html", _context(request, db, user, org_id, neues_token=raw)
    )


@router.post("/token/{token_id}/widerrufen", response_class=HTMLResponse)
def token_widerrufen(
    token_id: int,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    from app.core.audit import write_audit
    from app.models.dienst_monitor import DienstMonitorToken

    org_id = _org_id(user, target_org_id)
    token = (
        db.query(DienstMonitorToken)
        .filter(DienstMonitorToken.id == token_id, DienstMonitorToken.org_id == org_id)
        .first()
    )
    if not token:
        raise HTTPException(404, "Token nicht gefunden")
    token.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    write_audit(
        db, "admin.dienst_monitor.token_widerrufen", org_id=org_id, user_id=user.id, payload={"token_id": token_id}
    )
    db.commit()
    return templates.TemplateResponse(request, "admin/systemstatus.html", _context(request, db, user, org_id))


@router.post("/test/{kanal}", response_class=HTMLResponse)
async def test_meldung(
    kanal: str,
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
):
    from types import SimpleNamespace

    from app.models.dienst_monitor import DienstStatus
    from app.models.master import FireDept, OrgSettings
    from app.services.dienst_monitor_dispatch import dispatch_meldung

    if kanal not in {"mail", "teams", "sms"}:
        raise HTTPException(404, "Unbekannter Kanal")
    org_id = _org_id(user, target_org_id)
    org, settings = db.get(FireDept, org_id), db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if not org or not settings:
        raise HTTPException(404, "Organisation nicht gefunden")
    probe = SimpleNamespace(
        **{
            k: getattr(settings, k)
            for k in ("dienst_monitor_mail", "dienst_monitor_teams_webhook_url", "dienst_monitor_sms")
        }
    )
    if kanal != "mail":
        probe.dienst_monitor_mail = None
    if kanal != "teams":
        probe.dienst_monitor_teams_webhook_url = None
    if kanal != "sms":
        probe.dienst_monitor_sms = None
    row = DienstStatus(
        org_id=org_id, key="print_gateway", state="down", down_since=datetime.now(UTC).replace(tzinfo=None)
    )
    check = SimpleNamespace(key="print_gateway", detail="Testbenachrichtigung der Dienstueberwachung")
    ok = await dispatch_meldung(
        db=db,
        org_id=org_id,
        org=org,
        org_settings=probe,
        row=row,
        check=check,
        art="stoerung",
        base_url=str(request.base_url).rstrip("/"),
    )
    return templates.TemplateResponse(
        request, "admin/systemstatus.html", _context(request, db, user, org_id, test_ergebnis=ok)
    )
