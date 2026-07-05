"""PR 11 – System-Admin-Konsole.

Routes (alle nur system_admin):
  GET  /admin/system/orgs             – Per-Org KPI-Tabelle
  GET  /admin/system/quotas           – Quota-Verwaltung (Speicher + KI-Token) je Org
  POST /admin/system/quotas/{org_id}  – Quota speichern
  POST /admin/system/quotas/{org_id}/reconcile – Speicher-Verbrauch neu berechnen
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident
from app.models.master import FireDept, Member, OrgSettings, OrgStorageUsage
from app.models.user import ApiKey, User

router = APIRouter(prefix="/admin")

_GIB = 1024 ** 3


def _org_stats(db: Session) -> list[dict]:
    """Aggregate per-org KPIs for the system-admin console."""
    orgs = db.query(FireDept).order_by(FireDept.name).all()

    active_inc = {
        r.primary_org_id: r.cnt
        for r in db.query(Incident.primary_org_id, func.count().label("cnt"))
        .filter(Incident.status == "active")
        .group_by(Incident.primary_org_id)
        .all()
    }
    total_inc = {
        r.primary_org_id: r.cnt
        for r in db.query(Incident.primary_org_id, func.count().label("cnt"))
        .group_by(Incident.primary_org_id)
        .all()
    }
    last_inc = {
        r.primary_org_id: r.last
        for r in db.query(
            Incident.primary_org_id, func.max(Incident.started_at).label("last")
        )
        .group_by(Incident.primary_org_id)
        .all()
    }
    user_cnt = {
        r.org_id: r.cnt
        for r in db.query(User.org_id, func.count().label("cnt"))
        .filter(User.active == True, User.org_id.isnot(None))  # noqa: E712
        .group_by(User.org_id)
        .all()
    }
    member_cnt = {
        r.org_id: r.cnt
        for r in db.query(Member.org_id, func.count().label("cnt"))
        .filter(Member.active == True)  # noqa: E712
        .group_by(Member.org_id)
        .all()
    }
    apikey_cnt = {
        r.org_id: r.cnt
        for r in db.query(ApiKey.org_id, func.count().label("cnt"))
        .filter(ApiKey.revoked_at.is_(None))
        .group_by(ApiKey.org_id)
        .all()
    }

    return [
        {
            "org": org,
            "active_incidents": active_inc.get(org.id, 0),
            "total_incidents": total_inc.get(org.id, 0),
            "last_incident_at": last_inc.get(org.id),
            "users": user_cnt.get(org.id, 0),
            "members": member_cnt.get(org.id, 0),
            "api_keys": apikey_cnt.get(org.id, 0),
        }
        for org in orgs
    ]


def _pct(used: int, quota: int | None) -> int | None:
    """Auslastung in Prozent (0..100+); None wenn keine Quota gesetzt."""
    if quota is None:
        return None
    if quota <= 0:
        return 100 if used > 0 else 0
    return round(used / quota * 100)


def _quota_rows(db: Session) -> list[dict]:
    """Speicher- und KI-Token-Quota je Org (read-only, keine Schreibzugriffe)."""
    orgs = (
        db.query(FireDept)
        .filter(FireDept.deleted_at.is_(None))
        .order_by(FireDept.name)
        .all()
    )
    usage = {u.org_id: u.used_bytes for u in db.query(OrgStorageUsage).all()}
    settings_map = {s.org_id: s for s in db.query(OrgSettings).all()}
    cur_month = datetime.now(UTC).strftime("%Y-%m")

    rows: list[dict] = []
    for org in orgs:
        used = usage.get(org.id, 0)
        os_row = settings_map.get(org.id)
        ai_quota = os_row.ai_monthly_token_quota if os_row else None
        # Verbrauch nur werten, wenn er zum aktuellen Monat gehoert – sonst 0
        ai_used = (
            (os_row.ai_tokens_used_month or 0)
            if os_row and os_row.ai_tokens_month_key == cur_month
            else 0
        )
        rows.append(
            {
                "org": org,
                "storage_used": used,
                "storage_quota": org.storage_quota_bytes,
                "storage_pct": _pct(used, org.storage_quota_bytes),
                "storage_quota_gb": (
                    round(org.storage_quota_bytes / _GIB, 2)
                    if org.storage_quota_bytes is not None
                    else ""
                ),
                "ai_quota": ai_quota,
                "ai_used": ai_used,
                "ai_month": cur_month,
                "ai_pct": _pct(ai_used, ai_quota),
            }
        )
    return rows


@router.get("/system/orgs", response_class=HTMLResponse)
async def sysadmin_orgs(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    return templates.TemplateResponse(
        request,
        "admin/sysadmin_orgs.html",
        {
            "user": request.state.user,
            "is_sysadmin": True,
            "rows": _org_stats(db),
        },
    )


@router.get("/system/quotas", response_class=HTMLResponse)
async def sysadmin_quotas(
    request: Request,
    saved: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    return templates.TemplateResponse(
        request,
        "admin/sysadmin_quotas.html",
        {
            "user": request.state.user,
            "is_sysadmin": True,
            "rows": _quota_rows(db),
            "saved": saved,
        },
    )


@router.post("/system/quotas/{org_id}")
async def sysadmin_quota_save(
    org_id: int,
    request: Request,
    storage_quota_gb: str = Form(""),
    ai_monthly_token_quota: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    org = db.get(FireDept, org_id)
    if not org:
        raise HTTPException(404, "Organisation nicht gefunden")

    # ── Speicher-Quota (Eingabe in GB, leer = unbegrenzt) ──────────────────
    raw = storage_quota_gb.strip().replace(",", ".")
    if raw == "":
        org.storage_quota_bytes = None
    else:
        try:
            gb = float(raw)
        except ValueError:
            raise HTTPException(400, "Ungültiger Speicher-Quota-Wert")
        if gb < 0:
            raise HTTPException(400, "Speicher-Quota darf nicht negativ sein")
        org.storage_quota_bytes = int(round(gb * _GIB))

    # ── KI-Token-Quota (ganze Zahl, leer = unbegrenzt) ─────────────────────
    os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
    if os_row is None:
        os_row = OrgSettings(org_id=org_id)
        db.add(os_row)
    raw_ai = (
        ai_monthly_token_quota.strip()
        .replace(".", "")
        .replace(",", "")
        .replace(" ", "")
    )
    if raw_ai == "":
        os_row.ai_monthly_token_quota = None
    else:
        try:
            tokens = int(raw_ai)
        except ValueError:
            raise HTTPException(400, "Ungültige KI-Token-Quota")
        if tokens < 0:
            raise HTTPException(400, "KI-Token-Quota darf nicht negativ sein")
        os_row.ai_monthly_token_quota = tokens

    write_audit(
        db,
        "sysadmin.quota.updated",
        user_id=request.state.user.id,
        entity_type="fire_dept",
        entity_id=org_id,
        payload={
            "storage_quota_bytes": org.storage_quota_bytes,
            "ai_monthly_token_quota": os_row.ai_monthly_token_quota,
        },
    )
    db.commit()
    return RedirectResponse("/admin/system/quotas?saved=1", status_code=303)


@router.post("/system/quotas/{org_id}/reconcile")
async def sysadmin_quota_reconcile(
    org_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    org = db.get(FireDept, org_id)
    if not org:
        raise HTTPException(404, "Organisation nicht gefunden")
    from app.services.storage_service import reconcile_storage

    reconcile_storage(db, org_id)
    write_audit(
        db,
        "sysadmin.quota.reconciled",
        user_id=request.state.user.id,
        entity_type="fire_dept",
        entity_id=org_id,
    )
    db.commit()
    return RedirectResponse("/admin/system/quotas?saved=1", status_code=303)
