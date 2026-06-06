"""Externe REST-API – Einsatz automatisch anlegen.

Authentifizierung erfolgt über den HTTP-Header `X-API-Key`. Keys werden
im Admin-Bereich (`/admin/api-keys`) erstellt und sind org-gebunden:
Lese-Endpunkte liefern nur Einsätze der eigenen Org bzw. solche, an denen
die Org als Kollaborator beteiligt ist.

Antworten sind JSON; Fehler folgen FastAPI-Konvention mit `detail`-Feld.
"""
from datetime import UTC, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.security import hash_api_key, sign_qr_token
from app.db import get_db
from app.models.incident import Incident, IncidentOrg, IncidentToken
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.user import ApiKey
from app.services.broadcast import manager
from app.services.incident_service import create_incident
from app.services.push_service import notify_all

router = APIRouter(prefix="/api/v1", tags=["Einsätze"])

# Mapping of possible lowercase Stufe values to alarm type codes
STUFE_MAP = {
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f14": "F14",
    "t1": "T1", "t2": "T2", "t3": "T3", "t4": "T4", "t6": "T6", "t7": "T7",
    # Numeric variants
    "1": "T1", "2": "T2", "3": "T3", "4": "T4", "6": "T6", "7": "T7",
    "t9": "T9", "9": "T9",
}


class AlarmPayload(BaseModel):
    """Eingabe-Schema für `/api/v1/einsatz`.

    Felder folgen dem klassischen FWWO-Alarmlayout (deutsche Bezeichnungen).
    Pflichtfeld ist nur `Key` — er dient als Idempotency-Token und verhindert,
    dass dasselbe Alarm-Ereignis zweimal angelegt wird.
    """
    Key: str = Field(..., description="Eindeutiger Schlüssel des Alarms (Idempotency).",
                     examples=["A-2025-04711"])
    Nummer: int | None = Field(None, description="Externe Einsatznummer.", examples=[4711])
    AlarmDatumZeit: str | None = Field(
        None, description="ISO-8601 Datum/Zeit der Alarmierung.",
        examples=["2026-05-24T18:42:00+02:00"],
    )
    Stufe: str | None = Field(
        None, description="Alarmstufe: F1–F14, T1–T7 (case-insensitive).",
        examples=["F3"],
    )
    Art: str | None = Field(None, description="Einsatzart-Bezeichnung.")
    Meldung: str | None = Field(None, description="Volltext der Alarmmeldung.")
    Einsatzgrund: str | None = Field(None, description="Anlass/Einsatzgrund.")
    Ort: str | None = Field(None, description="Ort/Stadt.")
    Strasse: str | None = Field(None, description="Straße.")
    HausNr: str | None = Field(None, description="Hausnummer.")
    Uebung: bool = Field(False, description="Übungsalarm (kein echter Einsatz).")
    Zeitzone: str | None = Field(
        None,
        description=(
            "IANA-Zeitzone für naive `AlarmDatumZeit`-Werte ohne UTC-Offset "
            "(z. B. `'Europe/Vienna'`). "
            "Fehlt das Feld, wird die Zeitzone der Organisation verwendet; "
            "ist auch diese nicht gesetzt, greift der Server-Default."
        ),
        examples=["Europe/Vienna"],
    )


class IncidentCreatedResponse(BaseModel):
    """Antwort beim Anlegen / Idempotency-Hit eines Einsatzes."""
    id: int = Field(..., description="Interne Einsatz-ID.")
    external_key: str = Field(..., description="Vom Aufrufer mitgegebener Schlüssel.")
    url: str = Field(..., description="UI-URL zum neu angelegten Einsatz.")
    created: bool = Field(..., description="True bei Neuanlage, False bei Idempotency-Treffer.")
    board_token: str | None = Field(
        None,
        description=(
            "Signiertes QR-Token für direkten Board-Zugriff ohne Passwort. "
            "Gültig solange der Einsatz aktiv ist. "
            "Null, wenn dem API-Key kein Benutzer zugeordnet ist."
        ),
    )
    board_url: str | None = Field(
        None,
        description=(
            "Vollständige Login-URL für QR-Code-Zugriff auf das Einsatz-Board "
            "(direkt verlinkbar / als QR-Code druckbar). "
            "Null, wenn dem API-Key kein Benutzer zugeordnet ist."
        ),
    )


class IncidentSummary(BaseModel):
    """Kurz-Repräsentation eines Einsatzes in Listen-Endpunkten."""
    id: int
    alarm_type_code: str = Field(..., description="Alarmstufe (z. B. F3).")
    started_at: datetime | None = Field(None, description="Startzeitpunkt UTC.")
    is_exercise: bool = Field(..., description="Übungsalarm?")


class IncidentDetail(BaseModel):
    """Detail-Repräsentation eines Einsatzes."""
    id: int
    alarm_type_code: str
    status: str = Field(..., description="`active` oder `closed`.")
    started_at: datetime | None
    address: str = Field(..., description="Zusammengesetzte Adresse: 'Strasse HausNr, Ort'.")
    is_exercise: bool


def _resolve_tz(tz_name: str | None, org: FireDept | None) -> ZoneInfo:
    """Resolves a timezone: explicit name → org timezone → server default."""
    from app.config import settings
    for name in (tz_name, getattr(org, "timezone", None), settings.DEFAULT_TIMEZONE):
        if name:
            try:
                return ZoneInfo(name)
            except ZoneInfoNotFoundError:
                continue
    return ZoneInfo("UTC")


def _get_or_create_board_token(
    db: Session, incident_id: int, user_id: int | None, base_url: str
) -> tuple[str | None, str | None]:
    """Returns (token, board_url) for QR-code board access, or (None, None) if no user."""
    if not user_id:
        return None, None
    token = sign_qr_token(incident_id, user_id)
    token_hash = sha256(token.encode()).hexdigest()
    existing = db.query(IncidentToken).filter(
        IncidentToken.incident_id == incident_id,
        IncidentToken.issued_by_user_id == user_id,
        IncidentToken.revoked_at.is_(None),
    ).first()
    if not existing:
        db.add(IncidentToken(
            incident_id=incident_id,
            token_hash=token_hash,
            issued_by_user_id=user_id,
        ))
    board_url = f"{base_url}qr-login?incident_id={incident_id}&token={token}"
    return token, board_url


def _get_api_key(x_api_key: str = Header(..., alias="X-API-Key"), db: Session = Depends(get_db)):
    key_hash = hash_api_key(x_api_key)
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if not api_key or not api_key.is_active:
        raise HTTPException(status_code=401, detail="Ungültiger oder gesperrter API-Key")
    api_key.last_used_at = datetime.now(UTC)
    return api_key


async def _enrich_with_ai_suggestions(
    incident_id: int,
    meldung: str,
    einsatzart: str,
) -> None:
    """Background: generate AI task suggestions and persist them on the incident."""
    from app.db import SessionLocal
    from app.models.incident import Incident, Task
    from app.services.ai_service import suggest_tasks

    suggestions = await suggest_tasks(meldung, einsatzart)
    if not suggestions:
        return

    db = SessionLocal()
    try:
        incident = db.get(Incident, incident_id)
        if not incident:
            return

        tasks_col = next((c for c in incident.columns if c.code == "tasks"), None)

        for i, s in enumerate(suggestions):
            db.add(Task(
                incident_id=incident_id,
                column_id=tasks_col.id if tasks_col else None,
                title=s["titel"],
                detail=s.get("detail"),
                source="ai_suggestion",
                display_order=1000 + i,
            ))
        db.commit()

        from app.services.broadcast import manager
        await manager.broadcast(incident_id, {
            "type": "ai_suggestions_ready",
            "reload_board": True,
            "count": len(suggestions),
        })
    except Exception:
        pass
    finally:
        db.close()


async def _enrich_with_ai_hints(
    incident_id: int,
    meldung: str,
    alarm_type: str,
    address: str,
) -> None:
    """Background: generate AI Lage-Hinweise and persist them on the incident."""
    import json as _json
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.services.ai_service import generate_lage_hints

    hints = await generate_lage_hints(meldung, alarm_type, address)
    if not hints:
        return

    db = SessionLocal()
    try:
        incident = db.get(Incident, incident_id)
        if not incident:
            return
        incident.ai_lage_hints = _json.dumps(hints, ensure_ascii=False)
        db.commit()

        from app.services.broadcast import manager
        await manager.broadcast(incident_id, {"type": "ai_hints_ready", "reload_board": True})
    except Exception:
        pass
    finally:
        db.close()


def _handle_major_incident_trigger(
    db: Session,
    org_id: int,
    alarm_type_code: str,
    incident_id: int,
    external_key: str,
    *,
    is_exercise: bool,
    ort: str | None,
    strasse: str | None,
    hausnr: str | None,
    einsatzgrund: str | None,
    lat: float | None = None,
    lng: float | None = None,
) -> None:
    """Prüft ob der Alarm eine Großschadenslage auslöst oder in eine laufende übernommen wird."""
    from app.services.major_incident_service import (
        adopt_incident_as_site,
        get_active_lage,
        handle_alarm_trigger,
    )

    alarm_type = db.get(AlarmType, alarm_type_code)
    triggers = alarm_type.triggers_major_incident if alarm_type else False

    active_lage = get_active_lage(db, org_id)

    if triggers:
        # Alarm löst Lage aus oder wird in bestehende Lage eingefügt
        handle_alarm_trigger(
            db, org_id, alarm_type_code, incident_id, external_key,
            is_exercise=is_exercise,
            ort=ort, strasse=strasse, hausnr=hausnr, einsatzgrund=einsatzgrund,
            lat=lat, lng=lng,
        )
    elif active_lage:
        # Laufende Lage + mi_auto_adopt → normalen Einsatz spiegeln
        org_settings = (
            db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        )
        auto_adopt = org_settings.mi_auto_adopt if org_settings else True
        if auto_adopt:
            adopt_incident_as_site(
                db, active_lage,
                incident_id=incident_id,
                external_key=external_key,
                alarm_type_code=alarm_type_code,
                org_id=org_id,
                ort=ort, strasse=strasse, hausnr=hausnr, einsatzgrund=einsatzgrund,
                lat=lat, lng=lng,
            )


@router.post(
    "/einsatz",
    response_model=IncidentCreatedResponse,
    summary="Einsatz aus Alarm anlegen",
    description=(
        "Legt einen neuen Einsatz aus einem externen Alarm-Datensatz an "
        "(z. B. von der Leitstelle oder einem Alarmierungssystem). "
        "Bereits angelegte Einsätze (gleicher `Key`) werden idempotent zurückgegeben — "
        "ohne erneute Anlage, ohne Push-Notification. "
        "Bei Neuanlage werden alle Push-Empfänger und WebSocket-Clients informiert."
    ),
    responses={
        200: {"description": "Einsatz angelegt oder bereits vorhanden (siehe `created`)."},
        401: {"description": "Ungültiger oder gesperrter API-Key."},
    },
)
async def create_incident_api(
    payload: AlarmPayload,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(_get_api_key),
):
    org = db.get(FireDept, api_key.org_id) if api_key.org_id else None

    # Idempotency check
    existing = db.query(Incident).filter(Incident.external_key == payload.Key).first()
    if existing:
        write_audit(db, "api.incident.duplicate", api_key_id=api_key.id,
                    incident_id=existing.id, ip=request.client.host if request.client else None)
        board_token, board_url = _get_or_create_board_token(
            db, existing.id, api_key.created_by_user_id, str(request.base_url)
        )
        db.commit()
        return {
            "id": existing.id,
            "external_key": existing.external_key,
            "url": f"/einsatz/{existing.id}",
            "created": False,
            "board_token": board_token,
            "board_url": board_url,
        }

    # Map Stufe to alarm type code
    stufe_raw = (payload.Stufe or "T1").lower().strip()
    alarm_type_code = STUFE_MAP.get(stufe_raw, "T1")

    # Parse AlarmDatumZeit; naive values are interpreted in the request/org timezone
    started_at = None
    if payload.AlarmDatumZeit:
        try:
            started_at = datetime.fromisoformat(payload.AlarmDatumZeit)
            if started_at.tzinfo is None:
                tz = _resolve_tz(payload.Zeitzone, org)
                started_at = started_at.replace(tzinfo=tz)
            started_at = started_at.astimezone(UTC)
        except (ValueError, ZoneInfoNotFoundError):
            started_at = None

    incident = create_incident(
        db,
        alarm_type_code=alarm_type_code,
        started_at=started_at,
        external_key=payload.Key,
        nummer=payload.Nummer,
        is_exercise=payload.Uebung,
        address_street=payload.Strasse,
        address_no=payload.HausNr,
        address_city=payload.Ort,
        report_text=payload.Meldung,
        reason=payload.Einsatzgrund,
        primary_org_id=api_key.org_id,
        api_key_id=api_key.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()

    # Automatisches Geocoding wenn Adresse vorhanden (vor GSL-Trigger, damit Koordinaten fließen)
    _geo_lat: float | None = None
    _geo_lng: float | None = None
    if payload.Ort or payload.Strasse:
        from app.services.geocoding import geocode_address as _geocode
        geo = await _geocode(payload.Strasse, payload.HausNr, payload.Ort)
        if geo:
            _geo_lat, _geo_lng = geo.lat, geo.lng
            incident.lat = geo.lat
            incident.lng = geo.lng
            db.commit()

    # ── Großschadenslage-Trigger ─────────────────────────────────────────────
    if api_key.org_id:
        _handle_major_incident_trigger(
            db=db,
            org_id=api_key.org_id,
            alarm_type_code=alarm_type_code,
            incident_id=incident.id,
            external_key=payload.Key,
            is_exercise=payload.Uebung,
            ort=payload.Ort,
            strasse=payload.Strasse,
            hausnr=payload.HausNr,
            einsatzgrund=payload.Einsatzgrund,
            lat=_geo_lat,
            lng=_geo_lng,
        )
        db.commit()

    address = f"{payload.Strasse or ''} {payload.HausNr or ''}, {payload.Ort or ''}".strip(", ")
    exercise_prefix = "[ÜBUNG] " if payload.Uebung else ""

    # WebSocket broadcast to all connected clients
    await manager.broadcast_all({
        "type": "incident_created",
        "incident_id": incident.id,
        "alarm": alarm_type_code,
        "address": address,
        "is_exercise": payload.Uebung,
        "url": f"/einsatz/{incident.id}",
        "title": f"{exercise_prefix}Neuer Einsatz: {alarm_type_code} – {address}",
    })

    # Web Push notification
    push_title = f"{exercise_prefix}🚒 Einsatz: {alarm_type_code}"
    push_body = address or payload.Meldung or "Kein Ort angegeben"
    notify_all(db, push_title, push_body, url=f"/einsatz/{incident.id}")

    board_token, board_url = _get_or_create_board_token(
        db, incident.id, api_key.created_by_user_id, str(request.base_url)
    )
    db.commit()

    # AI task suggestions + Lage-Hinweise in background (never blocks alarm creation)
    from app.services.ai_service import is_enabled as ai_is_enabled
    if ai_is_enabled() and not payload.Uebung:
        background_tasks.add_task(
            _enrich_with_ai_suggestions,
            incident.id,
            payload.Meldung or "",
            payload.Art or payload.Stufe or "",
        )
        background_tasks.add_task(
            _enrich_with_ai_hints,
            incident.id,
            payload.Meldung or "",
            alarm_type_code,
            address,
        )

    return {
        "id": incident.id,
        "external_key": incident.external_key,
        "url": f"/einsatz/{incident.id}",
        "created": True,
        "board_token": board_token,
        "board_url": board_url,
    }


def _api_key_scoped_incidents(db: Session, api_key: ApiKey):
    """Liefert eine Incident-Query, die nur Einsätze enthält, die zur Org des API-Keys gehören.
    Bei API-Keys ohne org_id (legacy / system) werden alle Einsätze geliefert."""
    q = db.query(Incident)
    if api_key.org_id is None:
        return q
    collab_ids_subq = db.query(IncidentOrg.incident_id).filter(
        IncidentOrg.org_id == api_key.org_id
    )
    return q.filter(
        or_(
            Incident.primary_org_id == api_key.org_id,
            Incident.id.in_(collab_ids_subq),
        )
    )


# ── Großschadenslage: Direkte Site-Erstellung ────────────────────────────────

class LageAlarmPayload(BaseModel):
    """Direktes Hinzufügen einer Einsatzstelle zur laufenden Großschadenslage."""
    Key: str = Field(..., description="Eindeutiger Schlüssel (Idempotency-Token).")
    Art: str | None = Field(None, description="Bezeichnung/Einsatzart für die Einsatzstelle.")
    Meldung: str | None = Field(None, description="Volltext der Meldung (wird als Einsatzgrund gespeichert).")
    Stufe: str | None = Field(None, description="Alarmstufe (F1–F14, T1–T7).")
    Ort: str | None = Field(None, description="Ort.")
    Strasse: str | None = Field(None, description="Straße.")
    HausNr: str | None = Field(None, description="Hausnummer.")
    Lat: float | None = Field(None, description="Breitengrad (WGS 84).")
    Lng: float | None = Field(None, description="Längengrad (WGS 84).")


class LageSiteCreatedResponse(BaseModel):
    lage_id: int = Field(..., description="ID der Großschadenslage.")
    site_id: int = Field(..., description="ID der angelegten Einsatzstelle.")
    created: bool = Field(..., description="True bei Neuanlage, False bei Idempotency-Treffer.")


@router.post(
    "/lage/alarm",
    response_model=LageSiteCreatedResponse,
    summary="Einsatzstelle direkt in aktive Großschadenslage eintragen",
    description=(
        "Legt eine Einsatzstelle in der aktiven Großschadenslage der Org an. "
        "Identische `Key`-Werte werden idempotent behandelt. "
        "Liefert 404 wenn keine aktive Lage existiert."
    ),
    responses={
        200: {"description": "Einsatzstelle angelegt oder Idempotency-Treffer."},
        401: {"description": "Ungültiger oder gesperrter API-Key."},
        404: {"description": "Keine aktive Großschadenslage."},
    },
)
async def lage_alarm(
    payload: LageAlarmPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(_get_api_key),
):
    from app.models.major_incident import (
        IncidentSite,
        MajorIncidentStatus,
        SiteLogEntry,
    )
    from app.services.broadcast import broadcast_lage
    from app.services.major_incident_service import create_site, get_active_lage

    lage = get_active_lage(db, api_key.org_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Keine aktive Großschadenslage.")

    # Idempotency
    existing = (
        db.query(IncidentSite)
        .filter(
            IncidentSite.major_incident_id == lage.id,
            IncidentSite.external_key == payload.Key,
        )
        .first()
    )
    if existing:
        return LageSiteCreatedResponse(lage_id=lage.id, site_id=existing.id, created=False)

    bezeichnung = (payload.Art or payload.Meldung or "Alarm")[:160]
    stufe = STUFE_MAP.get((payload.Stufe or "").lower().strip())
    if stufe and not payload.Art:
        bezeichnung = f"[{stufe}] {bezeichnung}"[:160]

    site = create_site(
        db, lage,
        bezeichnung=bezeichnung,
        einsatzgrund=(payload.Meldung or "")[:160] or None,
        ort=payload.Ort,
        strasse=payload.Strasse,
        hausnr=payload.HausNr,
        lat=payload.Lat,
        lng=payload.Lng,
        source="api",
        external_key=payload.Key,
        alarm_stufe=stufe,
    )
    db.add(SiteLogEntry(
        incident_site_id=site.id,
        kind="status",
        text=f"Einsatzstelle aus API-Alarm erstellt (Key: {payload.Key})",
    ))
    write_audit(db, "major_incident.site.from_api", api_key_id=api_key.id,
                payload={"lage_id": lage.id, "site_id": site.id, "key": payload.Key})
    db.commit()

    background_tasks.add_task(
        broadcast_lage, lage.id, {"type": "site_created", "reload_board": True}
    )
    return LageSiteCreatedResponse(lage_id=lage.id, site_id=site.id, created=True)


@router.get(
    "/einsatz/active",
    response_model=list[IncidentSummary],
    summary="Aktive Einsätze auflisten",
    description=(
        "Liefert alle Einsätze mit Status `active` für die Organisation des API-Keys "
        "(primary org und Kollaborationen). Legacy/System-Keys ohne `org_id` "
        "sehen alle aktiven Einsätze."
    ),
    responses={401: {"description": "Ungültiger oder gesperrter API-Key."}},
)
def list_active_incidents(db: Session = Depends(get_db), api_key: ApiKey = Depends(_get_api_key)):
    incidents = _api_key_scoped_incidents(db, api_key).filter(Incident.status == "active").all()
    return [{"id": i.id, "alarm_type_code": i.alarm_type_code,
             "started_at": i.started_at, "is_exercise": i.is_exercise} for i in incidents]


@router.get(
    "/einsatz/{incident_id}",
    response_model=IncidentDetail,
    summary="Einsatz-Detail abrufen",
    description=(
        "Liefert Detail-Informationen zu einem Einsatz. Org-Scope wie bei `/einsatz/active`."
    ),
    responses={
        401: {"description": "Ungültiger API-Key."},
        404: {"description": "Einsatz nicht gefunden oder nicht im Scope der Org."},
    },
)
def get_incident(incident_id: int, db: Session = Depends(get_db), api_key: ApiKey = Depends(_get_api_key)):
    incident = _api_key_scoped_incidents(db, api_key).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404)
    return {
        "id": incident.id,
        "alarm_type_code": incident.alarm_type_code,
        "status": incident.status,
        "started_at": incident.started_at,
        "address": (
            f"{incident.address_street or ''} {incident.address_no or ''}, {incident.address_city or ''}".strip(", ")
        ),
        "is_exercise": incident.is_exercise,
    }
