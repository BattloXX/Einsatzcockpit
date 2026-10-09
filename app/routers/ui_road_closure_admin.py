"""Organisation administration for anonymous road-closure screens."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.core.timezones import local_date_to_utc
from app.db import get_db
from app.models.road_closure import RoadClosureAccessToken
from app.models.user import User
from app.routers.ui_road_closure import require_strassensperren_enabled
from app.routers.ui_settings import _generate_qr_datauri
from app.services import road_closure_token_service

router = APIRouter(prefix="/admin/settings/strassensperren-infoscreen", tags=["strassensperren-admin"])


def _context(request: Request, db: Session, user: User, **extra):
    if user.org_id is None or user.org is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewaehlt")
    tokens = db.query(RoadClosureAccessToken).filter(
        RoadClosureAccessToken.org_id == user.org_id,
        RoadClosureAccessToken.art.in_(("status", "infoscreen")),
    ).order_by(RoadClosureAccessToken.created_at.desc()).all()
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = []
    for token in tokens:
        raw = road_closure_token_service.token_plain(token)
        url = road_closure_token_service.public_url(raw, token.art) if raw else None
        if token.revoked_at:
            state = "widerrufen"
        elif token.expires_at and token.expires_at <= now:
            state = "abgelaufen"
        else:
            state = "aktiv"
        try:
            berechtigungen = json.loads(token.berechtigungen_json or "{}")
        except json.JSONDecodeError:
            berechtigungen = {}
        rows.append({
            "token": token, "url": url, "state": state, "berechtigungen": berechtigungen,
            "qr": _generate_qr_datauri(url) if url and state == "aktiv" else None,
        })
    result = {"user": user, "org": user.org, "tokens": rows}
    result.update(extra)
    return result


@router.get("", response_class=HTMLResponse)
def page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("org_admin")),
         _guard: None = Depends(require_strassensperren_enabled)):
    return templates.TemplateResponse(
        request, "admin/settings_strassensperren_infoscreen.html", _context(request, db, user)
    )


@router.post("/neu")
def create(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("org_admin")),
           _guard: None = Depends(require_strassensperren_enabled), art: str = Form("status"), label: str = Form(""),
           expires: str = Form(""), zeige_geplante: str = Form(""), zeige_karte: str = Form(""),
           zeige_einschraenkungen: str = Form(""), zeige_grund: str = Form(""), rotation_sec: str = Form("0"),
           refresh_sec: str = Form("60")):
    if art not in {"status", "infoscreen"}:
        raise HTTPException(status_code=400, detail="Ungültige Art")
    if user.org_id is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewählt")
    expires_at = None
    if expires:
        try:
            expires_at = local_date_to_utc(date.fromisoformat(expires).isoformat(), end=True, org=user.org)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Ungültiges Ablaufdatum") from exc
    values = {"zeige_geplante": bool(zeige_geplante), "zeige_karte": bool(zeige_karte),
              "zeige_einschraenkungen": bool(zeige_einschraenkungen), "zeige_grund": bool(zeige_grund),
              "rotation_sec": rotation_sec, "refresh_sec": refresh_sec}
    road_closure_token_service.create_token(db, user.org_id, art, user.id, label=label.strip() or None,
                                            expires_at=expires_at, berechtigungen=values)
    db.commit()
    return RedirectResponse("/admin/settings/strassensperren-infoscreen", status_code=303)


@router.post("/{token_id}/widerrufen")
def revoke(
    token_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_strassensperren_enabled),
):
    token = db.query(RoadClosureAccessToken).filter(
        RoadClosureAccessToken.id == token_id, RoadClosureAccessToken.org_id == user.org_id,
        RoadClosureAccessToken.art.in_(("status", "infoscreen")),
    ).first()
    if token is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    road_closure_token_service.revoke_token(db, token, user.id)
    db.commit()
    return RedirectResponse("/admin/settings/strassensperren-infoscreen", status_code=303)
