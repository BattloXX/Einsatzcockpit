from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept, OrgSettings
from app.models.stats import StatistikDashboardToken
from app.services.stats_service import default_range, get_stats

router = APIRouter()


@router.get("/infoscreen/statistik/{token}", response_class=HTMLResponse, include_in_schema=False)
async def statistik_infoscreen(request: Request, token: str, db: Session = Depends(get_db)):
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
    von, bis = default_range(org)
    stats = get_stats(db, org.id, von, bis, user=None)
    return templates.TemplateResponse(request, "infoscreen/statistik.html", {
        "org": org, "stats": stats, "von": von, "bis": bis,
        "recent_incidents": stats.recent_incidents[:5],
    })
