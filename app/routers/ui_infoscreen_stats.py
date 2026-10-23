from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.security import hash_api_key
from app.core.templating import templates
from app.core.timezones import now_local
from app.db import get_db
from app.models.master import FireDept, OrgSettings
from app.models.stats import StatistikDashboardToken
from app.services import shared_cache
from app.services.stats_infoscreen_service import get_infoscreen_data

router = APIRouter()


def _authorized_org(token: str, db: Session = Depends(get_db)) -> FireDept:
    dash_token = (db.query(StatistikDashboardToken)
                  .execution_options(include_all_tenants=True)
                  .filter(StatistikDashboardToken.token_hash == hash_api_key(token)).first())
    if not dash_token:
        raise HTTPException(status_code=401, detail="Ungueltiger oder geloeschter Dashboard-Token.")
    org = db.query(FireDept).filter(FireDept.id == dash_token.org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    org_settings = (db.query(OrgSettings).execution_options(include_all_tenants=True)
                    .filter(OrgSettings.org_id == org.id).first())
    if not org_settings or not org_settings.statistik_infoscreen_enabled:
        raise HTTPException(status_code=403, detail="Statistik-Infoscreen ist deaktiviert.")
    dash_token.last_used_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return org


def _data(db: Session, org: FireDept) -> dict:
    # Bestehenden optionalen Redis-JSON-Cache nutzen. Zugriffsprüfung erfolgt
    # auch bei Cache-Treffern für JEDEN Request.
    key = f"stats-infoscreen:v1:org:{org.id}:{now_local(org).date().isoformat()}"
    cached = run_async(shared_cache.get, key)
    if cached is not None:
        return cached
    data = get_infoscreen_data(db, org)
    run_async(shared_cache.set, key, data, 300)
    return data


@router.get("/infoscreen/statistik/{token}", response_class=HTMLResponse, include_in_schema=False)
def statistik_infoscreen(
    request: Request, org: FireDept = Depends(_authorized_org), db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "infoscreen/statistik.html", {
        "org": org, "data": _data(db, org),
    }, headers={"Cache-Control": "no-store"})


@router.get("/infoscreen/statistik/{token}/daten", include_in_schema=False)
def statistik_infoscreen_daten(
    token: str, db: Session = Depends(get_db),
):
    # Der allgemeine HTML-Fehlerhandler leitet 401 auf /login um. Ein laufender
    # Monitor benötigt stattdessen einen eindeutigen JSON-Status für den Widerruf.
    try:
        org = _authorized_org(token, db)
    except HTTPException as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(_data(db, org), headers={"Cache-Control": "no-store"})
