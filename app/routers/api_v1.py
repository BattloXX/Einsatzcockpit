"""Externe REST-API – Einsatz automatisch anlegen.

Authentifizierung erfolgt über den HTTP-Header `X-API-Key`. Keys werden
im Admin-Bereich (`/admin/api-keys`) erstellt und sind org-gebunden:
Lese-Endpunkte liefern nur Einsätze der eigenen Org bzw. solche, an denen
die Org als Kollaborator beteiligt ist.

Antworten sind JSON; Fehler folgen FastAPI-Konvention mit `detail`-Feld.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.security import hash_api_key
from app.db import get_db
from app.models.incident import Incident, IncidentOrg
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
    "t9": "T3",  # fallback for unknown T-variants
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


class IncidentCreatedResponse(BaseModel):
    """Antwort beim Anlegen / Idempotency-Hit eines Einsatzes."""
    id: int = Field(..., description="Interne Einsatz-ID.")
    external_key: str = Field(..., description="Vom Aufrufer mitgegebener Schlüssel.")
    url: str = Field(..., description="UI-URL zum neu angelegten Einsatz.")
    created: bool = Field(..., description="True bei Neuanlage, False bei Idempotency-Treffer.")


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


def _get_api_key(x_api_key: str = Header(..., alias="X-API-Key"), db: Session = Depends(get_db)):
    key_hash = hash_api_key(x_api_key)
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if not api_key or not api_key.is_active:
        raise HTTPException(status_code=401, detail="Ungültiger oder gesperrter API-Key")
    api_key.last_used_at = datetime.now(UTC)
    return api_key


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
    db: Session = Depends(get_db),
    api_key: ApiKey = Depends(_get_api_key),
):
    # Idempotency check
    existing = db.query(Incident).filter(Incident.external_key == payload.Key).first()
    if existing:
        write_audit(db, "api.incident.duplicate", api_key_id=api_key.id,
                    incident_id=existing.id, ip=request.client.host if request.client else None)
        db.commit()
        return {"id": existing.id, "external_key": existing.external_key,
                "url": f"/einsatz/{existing.id}", "created": False}

    # Map Stufe to alarm type code
    stufe_raw = (payload.Stufe or "T1").lower().strip()
    alarm_type_code = STUFE_MAP.get(stufe_raw, "T1")

    # Parse AlarmDatumZeit
    started_at = None
    if payload.AlarmDatumZeit:
        try:
            started_at = datetime.fromisoformat(payload.AlarmDatumZeit)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
        except ValueError:
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

    # Automatisches Geocoding wenn Adresse vorhanden
    if payload.Ort or payload.Strasse:
        from app.services.geocoding import geocode_address as _geocode
        geo = await _geocode(payload.Strasse, payload.HausNr, payload.Ort)
        if geo:
            incident.lat = geo.lat
            incident.lng = geo.lng
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

    return {
        "id": incident.id,
        "external_key": incident.external_key,
        "url": f"/einsatz/{incident.id}",
        "created": True,
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
        "address": f"{incident.address_street or ''} {incident.address_no or ''}, {incident.address_city or ''}".strip(", "),
        "is_exercise": incident.is_exercise,
    }
