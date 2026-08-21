"""Gerät-API: FCM-Token-Registrierung, Standort-Tracking, Dienst-Status.

Diese Endpoints werden von der nativen Android-App (Capacitor) aufgerufen.
Auth über bestehende Session-Cookies (Device-Login via /geraet-login).
"""
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.security import hash_api_key, sign_native_link_token
from app.db import get_db
from app.models.user import DeviceToken, FcmDeliveryLog, FcmToken, User
from app.services import push_service
from app.services.einsatz_live_service import build_live_state

router = APIRouter(prefix="/api/v1/device", tags=["device"])


def _resolve_user_via_bearer_token(request: Request, db: Session) -> User | None:
    """Authentifiziert ein Gerät über dessen langlebigen Pairing-Token."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    raw_token = authorization.removeprefix("Bearer ").strip()
    if not raw_token:
        return None
    device_token = db.query(DeviceToken).filter(
        DeviceToken.token_hash == hash_api_key(raw_token),
        DeviceToken.revoked_at.is_(None),
    ).first()
    if not device_token:
        return None
    user = db.get(User, device_token.user_id)
    if not user or not user.active:
        return None
    device_token.last_used_at = datetime.now(UTC).replace(tzinfo=None)
    return user


def _get_device_token(user_id: int, db: Session) -> DeviceToken | None:
    """Gibt das aktive DeviceToken des Users zurück (neuestes nicht-widerrufenes)."""
    return (
        db.query(DeviceToken)
        .filter(DeviceToken.user_id == user_id, DeviceToken.revoked_at.is_(None))
        .order_by(DeviceToken.created_at.desc())
        .first()
    )


# ── FCM-Token ─────────────────────────────────────────────────────────────────

@router.post("/fcm-token")
async def register_fcm_token(request: Request, db: Session = Depends(get_db)):
    """Registriert oder aktualisiert den FCM Registration Token des eingeloggten Geräts."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")
    data = await request.json()
    token = (data.get("token") or "").strip()
    platform = (data.get("platform") or "android").strip()[:20]
    if not token:
        raise HTTPException(status_code=400, detail="token fehlt")

    device_token = _get_device_token(user.id, db)
    push_service.upsert_fcm_token(
        db,
        user_id=user.id,
        token=token,
        platform=platform,
        device_token_id=device_token.id if device_token else None,
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.delete("/fcm-token")
async def unregister_fcm_token(request: Request, db: Session = Depends(get_db)):
    """Entfernt den FCM Token bei Logout oder Token-Rotation.

    Auth erforderlich; löscht nur Token des eigenen Users (verhindert Push-DoS
    durch fremdes Löschen erratener/bekannter Token-Werte).
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")
    data = await request.json()
    token = (data.get("token") or "").strip()
    if token:
        db.query(FcmToken).filter(
            FcmToken.token == token, FcmToken.user_id == user.id
        ).delete()
        db.commit()
    return JSONResponse({"ok": True})


# ── Native-Link-Handoff (Custom-Tab-Auth für PDFs) ────────────────────────────

@router.post("/native-link")
async def create_native_link(request: Request):
    """Mint ein kurzlebiges, pfadgebundenes Token für die Uebergabe einer
    authentifizierten URL an einen Capacitor-Custom-Tab (@capacitor/browser),
    der die Session-Cookies der App-WebView nicht teilt. Siehe
    native-bridge.js::openUrl() und unsign_native_link_token in main.py.

    Nur relative Same-Origin-Pfade erlaubt (kein Scheme/Host/"//") - verhindert
    Open-Redirect/SSRF ueber ein manipuliertes 'path'.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")
    data = await request.json()
    path = (data.get("path") or "").strip()
    parsed = urlsplit(path)
    if not path.startswith("/") or path.startswith("//") or parsed.scheme or parsed.netloc:
        raise HTTPException(status_code=400, detail="Ungültiger Pfad")

    token = sign_native_link_token(parsed.path, user.id)
    sep = "&" if "?" in path else "?"
    url = f"{str(request.base_url).rstrip('/')}{path}{sep}nt={token}"
    return JSONResponse({"url": url})


# ── Standort ──────────────────────────────────────────────────────────────────

@router.post("/location")
async def update_location(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Aktualisiert den Gerätestandort.

    Wird von der App nur bei aktivem Einsatz/Dienst aufgerufen (alle 10–30 s).
    Die Position erscheint auf der Lagekarte anstatt des Scatter-Punkts.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")
    data = await request.json()
    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat/lng fehlen oder ungültig")

    device_token = _get_device_token(user.id, db)
    if not device_token:
        raise HTTPException(status_code=404, detail="Kein registriertes Gerät")

    now = datetime.now(UTC)
    device_token.last_lat = lat
    device_token.last_lng = lng
    device_token.last_location_at = now

    # Positionshistorie schreiben wenn einem Fahrzeug zugeordnet
    if device_token.vehicle_master_id:
        # Aktive GSL-Lage für dieses Fahrzeug ermitteln
        from app.models.major_incident import LageEinheit, MajorIncident, MajorIncidentStatus, VehiclePosition
        active_lage = (
            db.query(MajorIncident)
            .join(LageEinheit, LageEinheit.lage_id == MajorIncident.id)
            .filter(
                LageEinheit.vehicle_id == device_token.vehicle_master_id,
                LageEinheit.status == "eingesetzt",
                MajorIncident.status == MajorIncidentStatus.active,
            )
            .first()
        )

        from app.models.master import VehicleMaster
        vehicle = db.get(VehicleMaster, device_token.vehicle_master_id)
        org_id = vehicle.dept_id if vehicle else 0

        accuracy = float(data.get("accuracy", 0) or 0) or None
        db.add(VehiclePosition(
            incident_id=active_lage.id if active_lage else None,
            org_id=org_id,
            vehicle_id=device_token.vehicle_master_id,
            lat=lat,
            lon=lng,
            accuracy_m=accuracy,
            source="gps",
            recorded_at=now,
            received_at=now,
            reported_by=user.id,
        ))

        # WS-Broadcast (gedrosselt: handled by caller - hier immer senden, Frontend drosselt)
        if active_lage:
            from app.services.broadcast import broadcast_lage
            v = vehicle
            label = v.code if v else str(device_token.vehicle_master_id)
            background_tasks.add_task(broadcast_lage, active_lage.id, {
                "type": "vehicle:position",
                "vehicle_id": device_token.vehicle_master_id,
                "label": label,
                "lat": lat,
                "lng": lng,
                "source": "gps",
                "ts": now.isoformat(),
            })

    db.commit()
    return JSONResponse({"ok": True})


# ── Dienst-Status ─────────────────────────────────────────────────────────────

@router.post("/duty")
async def set_duty(request: Request, db: Session = Depends(get_db)):
    """Setzt den Dienst-Status des Geräts (aktiv/inaktiv).

    Die App startet / stoppt das Background-Tracking entsprechend.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")
    data = await request.json()
    active = bool(data.get("active", False))

    device_token = _get_device_token(user.id, db)
    if not device_token:
        raise HTTPException(status_code=404, detail="Kein registriertes Gerät")

    device_token.duty_active = active
    db.commit()
    return JSONResponse({"ok": True, "duty_active": active})


@router.get("/duty-state")
def get_duty_state(request: Request, db: Session = Depends(get_db)):
    """Gibt zurück, ob für das Gerät aktuell ein aktiver Einsatz vorliegt.

    Die App nutzt diesen Endpoint, um Standort-Tracking automatisch zu steuern.
    """
    user = getattr(request.state, "user", None)
    bearer_authenticated = False
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
        bearer_authenticated = user is not None
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    if bearer_authenticated:
        db.commit()

    device_token = _get_device_token(user.id, db)
    server_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not device_token:
        return JSONResponse({
            "duty_active": False,
            "incident_active": False,
            "should_track": False,
            "server_time": server_time,
            "incident_count": 0,
            "incident": None,
        })

    # Prüfen ob dem Fahrzeug ein aktiver Einsatz zugewiesen ist
    incident_active = False
    if device_token.vehicle_master_id:
        from app.models.incident import Incident, IncidentVehicle
        incident_active = db.query(Incident).join(
            IncidentVehicle, Incident.id == IncidentVehicle.incident_id
        ).filter(
            IncidentVehicle.vehicle_master_id == device_token.vehicle_master_id,
            IncidentVehicle.removed_at.is_(None),
            Incident.status == "active",
        ).first() is not None

        # GSL: Fahrzeug als LageEinheit in aktiver Großschadenslage?
        if not incident_active:
            from app.models.major_incident import LageEinheit, MajorIncident, MajorIncidentStatus
            incident_active = db.query(MajorIncident).join(
                LageEinheit, LageEinheit.lage_id == MajorIncident.id
            ).filter(
                LageEinheit.vehicle_id == device_token.vehicle_master_id,
                LageEinheit.status == "eingesetzt",
                MajorIncident.status == MajorIncidentStatus.active,
            ).first() is not None

    live_incident, incident_count = build_live_state(db, user, device_token)
    return JSONResponse({
        "duty_active": device_token.duty_active,
        "incident_active": incident_active,
        "should_track": device_token.duty_active or incident_active,
        "server_time": server_time,
        "incident_count": incident_count,
        "incident": live_incident,
    })


@router.post("/push-ack")
async def acknowledge_push(request: Request, db: Session = Depends(get_db)):
    """Bestätigt eine FCM-Zustellung, ohne fremde Log-IDs offenzulegen."""
    user = _resolve_user_via_bearer_token(request, db)
    if not user:
        user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    data = await request.json()
    try:
        delivery_id = int(data.get("delivery_id"))
    except (AttributeError, TypeError, ValueError):
        delivery_id = None

    if delivery_id is not None:
        delivery = (
            db.query(FcmDeliveryLog)
            .join(FcmToken, FcmDeliveryLog.fcm_token_id == FcmToken.id)
            .filter(
                FcmDeliveryLog.id == delivery_id,
                FcmToken.user_id == user.id,
            )
            .first()
        )
        if delivery and delivery.delivered_at is None:
            delivery.delivered_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return JSONResponse({"ok": True})
