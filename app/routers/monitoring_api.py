"""Token-geschuetzte Uptime-Endpunkte fuer externe Monitore."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.dienst_monitor import DIENST_LABELS, DienstMonitorToken, DienstStatus
from app.models.master import OrgSettings, SystemSettings
from app.services.dienst_monitor_service import dienst_zustand, pruefe_dienste

router = APIRouter(tags=["Monitoring"])


def _token_org(request: Request) -> tuple[Session, int]:
    auth = request.headers.get("authorization", "")
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not raw:
        raw = (request.query_params.get("token") or "").strip()
    db = SessionLocal()
    set_tenant_context(db, None)
    now = datetime.now(UTC).replace(tzinfo=None)
    token = (
        db.query(DienstMonitorToken)
        .filter(DienstMonitorToken.token_hash == hash_api_key(raw) if raw else DienstMonitorToken.id == -1)
        .first()
    )
    if not token or token.revoked_at is not None or (token.expires_at is not None and token.expires_at <= now):
        db.close()
        raise HTTPException(401, "Ungültiges Monitoring-Token")
    token.last_used_at = now
    db.commit()
    return db, token.org_id


def _karenz_min(db: Session, org_id: int) -> int:
    org_s = (
        db.query(OrgSettings)
        .filter(OrgSettings.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return org_s.dienst_monitor_karenz_min if org_s else 5


@router.get("/health/dienste", include_in_schema=False)
def dienste(request: Request):
    db, org_id = _token_org(request)
    try:
        checks = {c.key: c for c in pruefe_dienste(db, org_id)}
        rows = {
            r.key: r
            for r in db.query(DienstStatus)
            .filter(DienstStatus.org_id == org_id)
            .execution_options(include_all_tenants=True)
            .all()
        }
        karenz = _karenz_min(db, org_id)
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        ausgabe = []
        down = False
        for key, label in DIENST_LABELS.items():
            check, row = checks[key], rows.get(key)
            status = dienst_zustand(check, row, karenz, jetzt)
            down |= status == "down"
            ausgabe.append(
                {
                    "key": key,
                    "label": label,
                    "status": status,
                    "roh_status": check.state,
                    "seit": row.down_since.isoformat() if row and row.down_since else None,
                    "detail": check.detail,
                }
            )
        loop = db.get(SystemSettings, "dienst_monitor_last_run")
        return JSONResponse(
            {"dienste": ausgabe, "loop_letzter_lauf": loop.value if loop else None}, status_code=503 if down else 200
        )
    finally:
        db.close()


@router.get("/health/dienst/{key}", include_in_schema=False)
def dienst(key: str, request: Request):
    if key not in DIENST_LABELS:
        raise HTTPException(404, "Unbekannter Dienst")
    db, org_id = _token_org(request)
    try:
        check = next(c for c in pruefe_dienste(db, org_id) if c.key == key)
        row = (
            db.query(DienstStatus)
            .filter(DienstStatus.org_id == org_id, DienstStatus.key == key)
            .execution_options(include_all_tenants=True)
            .first()
        )
        status = dienst_zustand(
            check, row, _karenz_min(db, org_id), datetime.now(UTC).replace(tzinfo=None)
        )
        if status == "nicht_konfiguriert":
            return {"status": "nicht_konfiguriert", "roh_status": check.state, "detail": check.detail}
        return JSONResponse(
            {"status": status, "roh_status": check.state, "detail": check.detail},
            status_code=503 if status == "down" else 200,
        )
    finally:
        db.close()
