from datetime import datetime
from uuid import uuid4

from app.core.security import hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.master import FireDept, VehicleMaster
from app.models.user import User


def _create_browser_user(*, with_incident: bool = False) -> tuple[int, int, int | None]:
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    try:
        org = FireDept(slug=f"api-live-{suffix}", name=f"API Live {suffix}")
        db.add(org)
        db.flush()
        user = User(
            username=f"api-live-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name=f"API Live {suffix}",
            active=True,
            org_id=org.id,
        )
        db.add(user)
        db.flush()

        incident_id = None
        if with_incident:
            incident = Incident(
                primary_org_id=org.id,
                alarm_type_code="B2",
                status="active",
                started_at=datetime(2026, 8, 3, 11, 2),
                address_street="Hauptstrasse",
                address_no="5",
                address_city="Musterstadt",
                is_exercise=False,
            )
            db.add(incident)
            db.flush()
            vehicle = VehicleMaster(
                dept_id=org.id,
                code=f"FZ-{suffix}",
                name="Testfahrzeug",
                type="Test",
            )
            db.add(vehicle)
            db.flush()
            column = IncidentColumn(
                incident_id=incident.id,
                code=f"active-{suffix}",
                title="Einheiten",
            )
            db.add(column)
            db.flush()
            db.add(
                IncidentVehicle(
                    incident_id=incident.id,
                    column_id=column.id,
                    vehicle_master_id=vehicle.id,
                    unit_status="Am Einsatzort",
                )
            )
            incident_id = incident.id

        db.commit()
        return user.id, org.id, incident_id
    finally:
        db.close()


def _authenticate(client, user_id: int) -> None:
    client.cookies.set("session", sign_session(user_id))


def test_browser_user_ohne_device_token_erhaelt_aktiven_einsatz(client, setup_db):
    user_id, _, incident_id = _create_browser_user(with_incident=True)
    _authenticate(client, user_id)

    response = client.get("/api/v1/live/state")

    assert response.status_code == 200
    assert response.json()["incident"] == {
        "id": incident_id,
        "url": f"/einsatz/{incident_id}",
        "alarm_type_code": "B2",
        "address": "Hauptstrasse 5, Musterstadt",
        "started_at": "2026-08-03T11:02:00Z",
        "is_exercise": False,
        "phase": "einsatzstelle",
        "phase_index": 2,
        "phase_count": 4,
        "phase_label": "Am Einsatzort",
        "phase_source": "org",
        "unit_count": 1,
    }
    assert response.json()["incident_count"] == 1


def test_cross_org_einsatz_bleibt_unsichtbar(client, setup_db):
    user_id, _, _ = _create_browser_user()
    _create_browser_user(with_incident=True)
    _authenticate(client, user_id)

    response = client.get("/api/v1/live/state")

    assert response.status_code == 200
    assert response.json()["incident_count"] == 0
    assert response.json()["incident"] is None


def test_ohne_session_cookie_kommt_401(client, setup_db):
    response = client.get("/api/v1/live/state")

    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht eingeloggt"}


def test_response_wird_nicht_gecacht(client, setup_db):
    user_id, _, _ = _create_browser_user()
    _authenticate(client, user_id)

    response = client.get("/api/v1/live/state")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_ohne_aktiven_einsatz_kommt_leerer_status(client, setup_db):
    user_id, _, _ = _create_browser_user()
    _authenticate(client, user_id)

    response = client.get("/api/v1/live/state")

    assert response.status_code == 200
    assert response.json()["incident_count"] == 0
    assert response.json()["incident"] is None
