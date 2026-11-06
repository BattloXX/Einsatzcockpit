"""JSON-API für die native Atemschutzüberwachung."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.breathing import BreathingTroop
from app.models.master import OrgSettings
from app.models.user import User
from app.routers.device_api import _resolve_user_via_bearer_token, _unauthorized
from app.services import breathing_sitzung as svc
from app.services.breathing_service import check_troop_warnings, get_asuew_settings

router = APIRouter(prefix="/api/asuew", tags=["asuew"])
COOKIE = "ec_asuew"


class HeartbeatDaten(BaseModel):
    akku_prozent: int | None = Field(None, ge=0, le=100, strict=True)
    laedt: bool | None = Field(None, strict=True)
    selbsttest: dict | None = None


class StartDaten(HeartbeatDaten):
    incident_id: int = Field(gt=0, strict=True)
    fcm_token: str = Field(min_length=1, max_length=512)
    app_version: str | None = Field(None, max_length=30)
    geraet_label: str | None = Field(None, max_length=100)


def _user(request: Request, db: Session = Depends(get_db)) -> User:
    user = getattr(request.state, "user", None)
    if not user:
        # Der Geräte-Bearer löst den Benutzer vor Auswahl seiner Org auf.
        set_tenant_context(db, None)
        user = _resolve_user_via_bearer_token(request, db)
    if not user:
        raise _unauthorized(request)
    set_tenant_context(db, user.org_id)
    return user


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def _key(request: Request) -> str:
    return request.headers.get("X-ECP-ASUEW") or request.cookies.get(COOKIE, "")


def _fehler(exc: svc.SitzungFehler) -> JSONResponse:
    return JSONResponse({"error": exc.code, "message": exc.nachricht}, status_code=exc.http_status)


@router.post("/sitzung")
def start(
    data: StartDaten, request: Request, user: User = Depends(_user), db: Session = Depends(get_db),
):
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        sitzung = svc.start_sitzung(db, user, **data.model_dump(), now=now,
                                  platform=request.headers.get("X-ECP-Platform"))
        db.commit()
    except svc.SitzungFehler as exc:
        db.rollback()
        return _fehler(exc)
    check = svc.geraete_check(db, sitzung, now)
    fehler = svc.monitoring_fehler(db, sitzung, now)
    response = JSONResponse({
        "session_key": sitzung.session_key, "incident_id": sitzung.incident_id,
        "server_time": _iso(now), "heartbeat_interval_s": svc.HEARTBEAT_INTERVAL_S,
        "heartbeat_max_age_s": svc.HEARTBEAT_MAX_AGE_S, **check,
    }, status_code=fehler.http_status if fehler else 200)
    response.set_cookie(
        COOKIE, sitzung.session_key, httponly=True, samesite="lax", secure=request.url.scheme == "https",
        path="/", max_age=svc.SITZUNG_MAX_STUNDEN * 3600,
    )
    return response


@router.post("/heartbeat")
def heartbeat(
    data: HeartbeatDaten, request: Request, user: User = Depends(_user), db: Session = Depends(get_db),
):
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        sitzung = svc.heartbeat(db, _key(request), user, **data.model_dump(), now=now,
                                  platform=request.headers.get("X-ECP-Platform"))
        db.commit()
    except svc.SitzungFehler as exc:
        db.rollback()
        return _fehler(exc)
    fehler = svc.monitoring_fehler(db, sitzung, now)
    return JSONResponse({"ok": fehler is None, "server_time": _iso(now),
                         **svc.geraete_check(db, sitzung, now)},
                        status_code=fehler.http_status if fehler else 200)


@router.post("/sitzung/beenden")
def beenden(request: Request, user: User = Depends(_user), db: Session = Depends(get_db)):
    try:
        svc.beende_sitzung(db, _key(request), user)
        db.commit()
    except svc.SitzungFehler as exc:
        db.rollback()
        return _fehler(exc)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return response


@router.get("/{incident_id}/zustand")
def zustand(
    incident_id: int, request: Request, user: User = Depends(_user), db: Session = Depends(get_db),
):
    now = datetime.now(UTC).replace(tzinfo=None)
    if user.org_id is None or svc.validiere_sitzung(db, _key(request), incident_id, user.id, now) is None:
        return JSONResponse(
            {"detail": "Atemschutzüberwachung nur in der Einsatzcockpit-App."}, status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    org = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    troops = (db.query(BreathingTroop).filter(BreathingTroop.incident_id == incident_id)
              .order_by(BreathingTroop.id).all())
    payload = []
    for troop in troops:
        item = {name: getattr(troop, name) for name in (
            "id", "name", "status", "is_sicherheitstrupp", "planned_duration_min",
            "one_third_seconds", "two_third_seconds", "max_seconds", "withdraw_press_calc",
        )}
        item.update({name: _iso(getattr(troop, name)) for name in (
            "entry_at", "notfall_at", "funk_verloren_at", "last_meldung_at",
        )})
        item["warnings"] = check_troop_warnings(troop)
        # Fuer die lokale Fristberechnung des nativen Waechters (gleiche Regeln wie get_time_warnings).
        druck_zeiten = [log.ts for log in troop.active_pressure_logs]
        item["last_pressure_at"] = _iso(max(druck_zeiten)) if druck_zeiten else None
        item["max_time_acked"] = troop.warn_max_time_acked_at is not None
        item["kontrollen"] = [{
            "stufe": kontrolle.stufe, "art": kontrolle.art, "erledigt_at": _iso(kontrolle.erledigt_at),
            "niedrigster_druck": kontrolle.niedrigster_druck,
        } for kontrolle in troop.active_kontrollen]
        item["members"] = [{
            "id": member.id, "name": member.display_name, "role": member.role,
            "start_press": member.start_press, "current_press": member.current_press,
            "withdraw_press": member.withdraw_press,
        } for member in troop.active_members]
        payload.append(item)
    # Auch Bearer-last_used_at aus der Authentifizierung persistieren.
    db.commit()
    return JSONResponse({
        "server_time": _iso(now), "incident_id": incident_id,
        "asuew_app_pflicht": bool(org and org.asuew_app_pflicht),
        "settings": get_asuew_settings(db, user.org_id)._asdict(), "troops": payload,
    }, headers={"Cache-Control": "no-store"})
