"""Gerät-API: FCM-Token-Registrierung, Standort-Tracking, Dienst-Status.

Diese Endpoints werden von der nativen Android-App (Capacitor) aufgerufen.
Auth über bestehende Session-Cookies (Device-Login via /geraet-login).
"""
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.dependencies import _set_module_states
from app.core.permissions import can_view_kontakt_directory, can_view_kontakt_directory_request
from app.core.security import hash_api_key, sign_native_link_token
from app.db import get_db
from app.models.master import VehicleMaster
from app.models.user import DeviceToken, FcmDeliveryLog, FcmToken, PushLog, User
from app.services import device_auth_failure_service as dat
from app.services import push_service
from app.services.einsatz_live_service import build_live_state
from app.services.gsl_live_service import build_gsl_live_state, build_my_lage_queue
from app.services.kontakt_sync_service import delta as kontakt_delta
from app.services.kontakt_sync_service import snapshot as kontakt_snapshot
from app.services.resource_service import STATUS_IM_EINSATZ

router = APIRouter(prefix="/api/v1/device", tags=["device"])
log = logging.getLogger(__name__)



def _resolve_user_via_bearer_token(request: Request, db: Session) -> User | None:
    """Authentifiziert ein Gerät über dessen langlebigen Pairing-Token.

    Ungültige Zugänge (unbekannt, widerrufen, Benutzer gesperrt) werden aggregiert protokolliert
    (SEC-01) und im Request als ``auth_error`` vermerkt, damit die 401-Antwort dem Client den
    Grund per Header ``X-ECP-Auth-Error`` mitteilen kann (SEC-02).
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    raw_token = authorization.removeprefix("Bearer ").strip()
    if not raw_token:
        return None
    token_hash = hash_api_key(raw_token)
    device_token = db.query(DeviceToken).filter(DeviceToken.token_hash == token_hash).first()
    if not device_token:
        _auth_fehler(request, dat.REASON_UNKNOWN, token_hash=token_hash)
        return None
    if device_token.revoked_at is not None:
        _auth_fehler(request, dat.REASON_REVOKED, device_token=device_token)
        return None
    user = db.get(User, device_token.user_id)
    if not user or not user.active:
        _auth_fehler(request, dat.REASON_USER_INACTIVE, device_token=device_token)
        return None
    device_token.last_used_at = datetime.now(UTC).replace(tzinfo=None)
    request.state.bearer_device_token_id = device_token.id
    _merke_geraetemerkmale(request, device_token)
    return user


def _auth_fehler(
    request: Request, reason: str, *, token_hash: str | None = None, device_token: DeviceToken | None = None,
) -> None:
    request.state.auth_error = dat.CLIENT_CODE[reason]
    dat.record_failure(request, reason, "bearer", token_hash=token_hash, device_token=device_token)


def _merke_geraetemerkmale(request: Request, device_token: DeviceToken) -> None:
    """Übernimmt Plattform/OS/Modell nur, wenn die App sie tatsächlich mitsendet."""
    for feld, header, query, grenze in (
        ("platform", "X-ECP-Platform", "platform", 20),
        ("os_version", "X-ECP-OS-Version", "os_version", 40),
        ("device_model", "X-ECP-Device-Model", "device_model", 80),
    ):
        wert = request.headers.get(header) or request.query_params.get(query)
        if wert and wert.strip():
            setattr(device_token, feld, wert.strip()[:grenze])


def _unauthorized(request: Request) -> HTTPException:
    """401 wie bisher (Body unverändert), zusätzlich mit Fehlercode für die App."""
    code = getattr(request.state, "auth_error", None)
    headers = {dat.AUTH_ERROR_HEADER: code} if code else None
    return HTTPException(status_code=401, detail="Nicht eingeloggt", headers=headers)


def _get_device_token(user_id: int, db: Session, request: Request | None = None) -> DeviceToken | None:
    """Gibt das aktive DeviceToken des Users zurück.

    Mit ``request`` zählt zuerst das tatsächlich verwendete Gerät (Bearer-Token bzw.
    Geräte-Session); nur ohne Bezug gilt das neueste nicht-widerrufene Token des Users.
    """
    token_id = None
    if request is not None:
        token_id = getattr(request.state, "bearer_device_token_id", None) or getattr(
            request.state, "device_token_id", None
        )
    if token_id is not None:
        gewaehlt = db.query(DeviceToken).filter(
            DeviceToken.id == token_id, DeviceToken.user_id == user_id, DeviceToken.revoked_at.is_(None),
        ).first()
        if gewaehlt is not None:
            return gewaehlt
    return (
        db.query(DeviceToken)
        .filter(DeviceToken.user_id == user_id, DeviceToken.revoked_at.is_(None))
        .order_by(DeviceToken.created_at.desc())
        .first()
    )


def _can_read_kontakte(user: User) -> bool:
    return can_view_kontakt_directory(user)


@router.get("/kontakte/sync")
def sync_kontakte(
    request: Request,
    cursor: int | None = Query(None, ge=0),
    page_after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Synchronizes central contacts for the authenticated native-app user."""
    user = getattr(request.state, "user", None)
    bearer_authenticated = False
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
        bearer_authenticated = user is not None
    if not user:
        raise _unauthorized(request)
    if not _can_read_kontakte(user) or (not bearer_authenticated and not can_view_kontakt_directory_request(request)):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if user.org_id is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewaehlt")

    # Device routes do not run the UI module-state setup. Reuse its effective
    # system-flag AND org-flag calculation for the authenticated user's org.
    _set_module_states(request, user.org_id, db)
    if not getattr(request.state, "kontakte_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    if bearer_authenticated:
        db.commit()

    if cursor is None:
        return kontakt_snapshot(db, user.org_id, page_after, limit)
    return kontakt_delta(db, user.org_id, cursor, limit)


# ── FCM-Token ─────────────────────────────────────────────────────────────────

@router.post("/fcm-token")
def register_fcm_token(request: Request, db: Session = Depends(get_db)):
    """Registriert oder aktualisiert den FCM Registration Token des eingeloggten Geräts."""
    user = getattr(request.state, "user", None)
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
    if not user:
        raise _unauthorized(request)
    data = run_async(request.json)
    token = (data.get("token") or "").strip()
    platform = (data.get("platform") or "android").strip()[:20]
    if not token:
        raise HTTPException(status_code=400, detail="token fehlt")

    capabilities = None
    if "capabilities" in data:
        raw_capabilities = data["capabilities"]
        if not isinstance(raw_capabilities, list) or not all(isinstance(item, str) for item in raw_capabilities):
            raise HTTPException(status_code=400, detail="capabilities muss eine Liste von Strings sein")
        normalized = sorted({
            item.strip().lower() for item in raw_capabilities
            if re.fullmatch(r"[a-z0-9_]{1,40}", item.strip().lower())
        })
        selected: list[str] = []
        for item in normalized:
            if len(",".join([*selected, item])) <= 200:
                selected.append(item)
        capabilities = ",".join(selected) or None

    device_token = _get_device_token(user.id, db, request)
    app_version = data.get("app_version")
    if device_token and isinstance(app_version, str) and (app_version := app_version.strip()):
        device_token.app_version = app_version
    push_service.upsert_fcm_token(
        db,
        user_id=user.id,
        token=token,
        platform=platform,
        device_token_id=device_token.id if device_token else None,
        capabilities=capabilities,
        capabilities_provided="capabilities" in data,
    )
    db.commit()
    # upsert_fcm_token kuerzt auf 512 Zeichen, daher mit demselben Wert suchen.
    fcm_token = db.query(FcmToken).filter(FcmToken.token == token[:512]).first()
    gespeichert = fcm_token.capabilities if fcm_token else None
    return JSONResponse({
        "ok": True,
        "capabilities": gespeichert.split(",") if gespeichert else [],
    })


_FCM_ALARM_TEST_SPERRE = timedelta(seconds=20)


@router.post("/fcm-test-alarm")
def test_fcm_alarm(request: Request, db: Session = Depends(get_db)):
    """Sendet hoechstens alle 20 Sekunden einen Probealarm je eigenem FCM-Token."""
    user = getattr(request.state, "user", None)
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
    if not user:
        raise _unauthorized(request)
    data = run_async(request.json)
    token = data.get("token") if isinstance(data, dict) else None
    row = db.query(FcmToken).filter(FcmToken.token == token, FcmToken.user_id == user.id).first()
    if row is None:
        return JSONResponse({"detail": "Token nicht gefunden"}, status_code=404)
    # Sperre ueber das Push-Protokoll, damit sie fuer alle Worker gilt.
    letzter = (
        db.query(PushLog.sent_at)
        .filter(PushLog.source == "fcm_alarm_test", PushLog.target_user_id == user.id)
        .order_by(PushLog.sent_at.desc())
        .first()
    )
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    if letzter and letzter[0] is not None:
        letzter_at = letzter[0].astimezone(UTC).replace(tzinfo=None) if letzter[0].tzinfo else letzter[0]
        if jetzt - letzter_at < _FCM_ALARM_TEST_SPERRE:
            return JSONResponse({"detail": "Bitte 20 Sekunden warten"}, status_code=429)
    native_alarm = push_service.fcm_token_has_capability(row, "native_alarm")
    title = "Testalarm"
    body = "Probealarm – so klingt ein Einsatzalarm"
    push_log = push_service._log_push(
        db, title, body, "/", "fcm_alarm_test", user.id, org_id=user.org_id,
    )
    ok, error = push_service.send_fcm(
        row, title, body, url="/", channel_id="einsatz_alarm",
        extra_data={"test": "1", "incident_id": "0", "is_exercise": "0"},
        cfg=push_service._push_cfg(db), db=db, push_log_id=push_log.id,
    )
    push_log.sent_count = int(ok)
    push_log.total_count = 1
    db.commit()
    return JSONResponse({"ok": ok, "native_alarm": native_alarm, "error": error})


_FCM_PUSH_TEST_SPERRE = timedelta(seconds=20)


@router.post("/fcm-test-push")
def test_fcm_push(request: Request, db: Session = Depends(get_db)):
    """Sendet hoechstens alle 20 Sekunden eine normale Test-Push-Nachricht je eigenem FCM-Token."""
    user = getattr(request.state, "user", None)
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
    if not user:
        raise _unauthorized(request)
    data = run_async(request.json)
    token = data.get("token") if isinstance(data, dict) else None
    row = db.query(FcmToken).filter(FcmToken.token == token, FcmToken.user_id == user.id).first()
    if row is None:
        return JSONResponse({"detail": "Token nicht gefunden"}, status_code=404)
    # Sperre ueber das Push-Protokoll, damit sie fuer alle Worker gilt.
    letzter = (
        db.query(PushLog.sent_at)
        .filter(PushLog.source == "fcm_push_test", PushLog.target_user_id == user.id)
        .order_by(PushLog.sent_at.desc())
        .first()
    )
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    if letzter and letzter[0] is not None:
        letzter_at = letzter[0].astimezone(UTC).replace(tzinfo=None) if letzter[0].tzinfo else letzter[0]
        if jetzt - letzter_at < _FCM_PUSH_TEST_SPERRE:
            return JSONResponse({"detail": "Bitte 20 Sekunden warten"}, status_code=429)
    title = "Test-Push"
    body = "Wenn du das siehst, kommen Push-Benachrichtigungen an."
    push_log = push_service._log_push(
        db, title, body, "/", "fcm_push_test", user.id, org_id=user.org_id,
    )
    ok, error = push_service.send_fcm(
        row, title, body, url="/", channel_id=None,
        extra_data={"test": "1"},
        cfg=push_service._push_cfg(db), db=db, push_log_id=push_log.id,
    )
    push_log.sent_count = int(ok)
    push_log.total_count = 1
    db.commit()
    return JSONResponse({"ok": ok, "error": error})


@router.get("/fcm-token/status")
def get_fcm_token_status(
    request: Request, token: str | None = None, db: Session = Depends(get_db)
):
    """Gibt den Registrierungs- und letzten Zustellstatus eines FCM-Tokens zurück."""
    user = getattr(request.state, "user", None)
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
    if not user:
        raise _unauthorized(request)

    token = (token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token fehlt")

    fcm_token = db.query(FcmToken).filter(
        FcmToken.user_id == user.id, FcmToken.token == token
    ).first()
    if not fcm_token:
        return JSONResponse({"registered": False, "native_alarm": False})

    letzte_zustellung = (
        db.query(FcmDeliveryLog)
        .filter(FcmDeliveryLog.fcm_token_id == fcm_token.id)
        .order_by(FcmDeliveryLog.sent_at.desc())
        .first()
    )
    return JSONResponse({
        "registered": True,
        "capabilities": fcm_token.capabilities.split(",") if fcm_token.capabilities else [],
        "native_alarm": push_service.fcm_token_has_capability(fcm_token, "native_alarm"),
        "registered_at": fcm_token.created_at.isoformat() + "Z",
        "last_delivery_success": (
            letzte_zustellung.success if letzte_zustellung else None
        ),
        "last_delivery_at": (
            letzte_zustellung.sent_at.isoformat() + "Z" if letzte_zustellung else None
        ),
    })


@router.delete("/fcm-token")
def unregister_fcm_token(request: Request, db: Session = Depends(get_db)):
    """Entfernt den FCM Token bei Logout oder Token-Rotation.

    Auth erforderlich; löscht nur Token des eigenen Users (verhindert Push-DoS
    durch fremdes Löschen erratener/bekannter Token-Werte).
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise _unauthorized(request)
    data = run_async(request.json)
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
        raise _unauthorized(request)
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
def update_location(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Aktualisiert den Gerätestandort.

    Wird von der App nur bei aktivem Einsatz/Dienst aufgerufen (alle 10–30 s).
    Die Position erscheint auf der Lagekarte anstatt des Scatter-Punkts.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise _unauthorized(request)
    data = run_async(request.json)
    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat/lng fehlen oder ungültig")

    device_token = _get_device_token(user.id, db, request)
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
                LageEinheit.status == STATUS_IM_EINSATZ,
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

@router.get("/vehicles")
def get_vehicles(request: Request, db: Session = Depends(get_db)):
    """Liefert die für das Fahrtenbuch verfügbaren Fahrzeuge des Geräts."""
    user = getattr(request.state, "user", None)
    bearer_authenticated = False
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
        bearer_authenticated = user is not None
    if not user:
        raise _unauthorized(request)

    if bearer_authenticated:
        db.commit()

    vehicles = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == user.org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.deleted == False,  # noqa: E712
            VehicleMaster.is_adhoc == False,  # noqa: E712
            VehicleMaster.is_external == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    device_token = _get_device_token(user.id, db, request)
    device_vehicle = None
    if device_token and device_token.vehicle_master_id:
        device_vehicle = (
            db.query(VehicleMaster)
            .filter(VehicleMaster.id == device_token.vehicle_master_id)
            .execution_options(include_all_tenants=True)
            .first()
        )

    def vehicle_data(vehicle: VehicleMaster) -> dict[str, int | str | None]:
        return {"id": vehicle.id, "code": vehicle.code, "name": vehicle.name}

    return JSONResponse({
        "device_vehicle": vehicle_data(device_vehicle) if device_vehicle else None,
        "vehicles": [vehicle_data(vehicle) for vehicle in vehicles],
    })

@router.post("/duty")
def set_duty(request: Request, db: Session = Depends(get_db)):
    """Setzt den Dienst-Status des Geräts (aktiv/inaktiv).

    Die App startet / stoppt das Background-Tracking entsprechend.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise _unauthorized(request)
    data = run_async(request.json)
    active = bool(data.get("active", False))

    device_token = _get_device_token(user.id, db, request)
    if not device_token:
        raise HTTPException(status_code=404, detail="Kein registriertes Gerät")

    device_token.duty_active = active
    db.commit()
    return JSONResponse({"ok": True, "duty_active": active})


@router.get("/duty-state")
def get_duty_state(
    request: Request,
    app_version: str | None = None,
    db: Session = Depends(get_db),
):
    """Gibt Geräte-Tracking und sichtbare Live-Einsatz-/Lagezustände zurück.

    Die App nutzt diesen Endpoint, um Standort-Tracking automatisch zu steuern.
    """
    user = getattr(request.state, "user", None)
    bearer_authenticated = False
    if not user:
        user = _resolve_user_via_bearer_token(request, db)
        bearer_authenticated = user is not None
    if not user:
        raise _unauthorized(request)

    device_token = _get_device_token(user.id, db, request)
    if device_token and app_version and (app_version := app_version.strip()):
        device_token.app_version = app_version
    if bearer_authenticated or app_version:
        db.commit()

    server_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not device_token:
        live_incident, incident_count = build_live_state(db, user, None)
        live_lage, lage_count = build_gsl_live_state(db, user)
        return JSONResponse({
            "duty_active": False,
            "incident_active": False,
            "should_track": False,
            "server_time": server_time,
            "incident_count": incident_count,
            "incident": live_incident,
            "lage_count": lage_count,
            "lage": live_lage,
            "my_lage_queue": None,
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
                LageEinheit.status == STATUS_IM_EINSATZ,
                MajorIncident.status == MajorIncidentStatus.active,
            ).first() is not None

    live_incident, incident_count = build_live_state(db, user, device_token)
    live_lage, lage_count = build_gsl_live_state(db, user)
    my_lage_queue = build_my_lage_queue(db, device_token)
    return JSONResponse({
        "duty_active": device_token.duty_active,
        "incident_active": incident_active,
        "should_track": device_token.duty_active or incident_active,
        "server_time": server_time,
        "incident_count": incident_count,
        "incident": live_incident,
        "lage_count": lage_count,
        "lage": live_lage,
        "my_lage_queue": my_lage_queue,
    })


@router.post("/push-ack")
def acknowledge_push(request: Request, db: Session = Depends(get_db)):
    """Bestätigt eine FCM-Zustellung fuer das angemeldete Geraet."""
    user = _resolve_user_via_bearer_token(request, db)
    if not user:
        user = getattr(request.state, "user", None)
    if not user:
        raise _unauthorized(request)

    data = run_async(request.json)
    try:
        delivery_id = int(data.get("delivery_id"))
    except (AttributeError, TypeError, ValueError):
        delivery_id = None

    delivery = None
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
    if delivery is None:
        log.warning(
            "Unbekannte oder fremde Push-Delivery-ID fuer User %s: %s",
            user.id,
            delivery_id,
        )
        return JSONResponse({"ok": False, "reason": "unknown_delivery"})
    db.commit()
    return JSONResponse({"ok": True})
