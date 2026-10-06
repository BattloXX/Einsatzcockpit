from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentOrg, IncidentVehicle
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    MajorIncidentStatus,
    SitePhase,
)
from app.models.master import FireDept, VehicleMaster
from app.models.user import DeviceToken, User
from app.services.einsatz_live_service import build_live_state
from app.services.resource_service import STATUS_IM_EINSATZ


def _create_device(*, with_vehicle: bool = True, duty_active: bool = False):
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    try:
        org = FireDept(slug=f"live-{suffix}", name=f"Live Org {suffix}")
        db.add(org)
        db.flush()
        user = User(
            username=f"live-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name=f"Live {suffix}",
            active=True,
            is_device=True,
            org_id=org.id,
        )
        db.add(user)
        db.flush()
        vehicle = None
        if with_vehicle:
            vehicle = VehicleMaster(
                dept_id=org.id, code=f"FZ-{suffix}", name="Testfahrzeug", type="Test"
            )
            db.add(vehicle)
            db.flush()
        raw_token = f"raw-{suffix}"
        token = DeviceToken(
            user_id=user.id,
            token_hash=hash_api_key(raw_token),
            label=f"Geraet {suffix}",
            vehicle_master_id=vehicle.id if vehicle else None,
            duty_active=duty_active,
        )
        db.add(token)
        db.commit()
        return user.id, token.id, org.id, vehicle.id if vehicle else None, raw_token
    finally:
        db.close()


def _create_incident(
    org_id: int, *, started_at: datetime, code: str = "B2", is_exercise: bool = False
) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(
            primary_org_id=org_id,
            alarm_type_code=code,
            status="active",
            started_at=started_at,
            address_street="Hauptstrasse",
            address_no="5",
            address_city="Musterstadt",
            is_exercise=is_exercise,
        )
        db.add(incident)
        db.commit()
        return incident.id
    finally:
        db.close()


def _add_vehicle(incident_id: int, org_id: int, status: str, vehicle_id: int | None = None) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:8]
    try:
        if vehicle_id is None:
            vehicle = VehicleMaster(
                dept_id=org_id, code=f"AGG-{suffix}", name="Aggregatfahrzeug", type="Test"
            )
            db.add(vehicle)
            db.flush()
            vehicle_id = vehicle.id
        column = IncidentColumn(
            incident_id=incident_id, code=f"active-{suffix}", title="Einheiten"
        )
        db.add(column)
        db.flush()
        db.add(IncidentVehicle(
            incident_id=incident_id,
            column_id=column.id,
            vehicle_master_id=vehicle_id,
            unit_status=status,
        ))
        db.commit()
        return vehicle_id
    finally:
        db.close()


def _authenticate(client, user_id: int, device_id: int) -> None:
    client.cookies.set(
        "session", sign_session(user_id, device=True, device_token_id=device_id)
    )


def _bearer_duty_state(client, raw_token: str) -> dict:
    """Native App polling uses its DeviceToken and deliberately no session cookie."""
    client.cookies.clear()
    response = client.get(
        "/api/v1/device/duty-state",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert response.status_code == 200
    return response.json()


def _add_collaborating_org(incident_id: int, org_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(IncidentOrg(incident_id=incident_id, org_id=org_id, role="collaborator"))
        db.commit()
    finally:
        db.close()


def _create_lage(org_id: int, *, is_exercise: bool) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(
            org_id=org_id,
            name="Live Lage",
            status=MajorIncidentStatus.active,
            is_exercise=is_exercise,
            started_at=datetime(2026, 8, 3, 11, 0),
        )
        db.add(lage)
        db.commit()
        return lage.id
    finally:
        db.close()


def _assign_vehicle_to_lage(lage_id: int, org_id: int, vehicle_id: int) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = LageEinheit(
            lage_id=lage_id,
            vehicle_id=vehicle_id,
            label="Live Fahrzeug",
            status=STATUS_IM_EINSATZ,
        )
        db.add(einheit)
        db.flush()
        site = IncidentSite(
            major_incident_id=lage_id,
            org_id=org_id,
            bezeichnung="Abschnitt Nord",
            einsatzgrund="Wasser im Keller",
            strasse="Dorfplatz",
            hausnr="1",
            ort="Musterstadt",
            phase=SitePhase.in_arbeit,
        )
        db.add(site)
        db.flush()
        db.add(EinheitSiteDispatch(
            einheit_id=einheit.id,
            site_id=site.id,
            dispatched_at=datetime(2026, 8, 3, 11, 5),
        ))
        db.commit()
        return site.id
    finally:
        db.close()


def test_live_state_ohne_org_kontext_bricht_hart_ab():
    class UserOhneOrg:
        org_id = None

    assert build_live_state(None, None, None) == (None, 0)
    assert build_live_state(None, UserOhneOrg(), None) == (None, 0)


def test_org_einsatz_aendert_fahrzeug_tracking_nicht(client, setup_db):
    user_id, device_id, org_id, _, _ = _create_device(duty_active=False)
    incident_id = _create_incident(org_id, started_at=datetime(2026, 8, 3, 11, 2))
    _add_vehicle(incident_id, org_id, "Am Einsatzort")
    _authenticate(client, user_id, device_id)

    response = client.get("/api/v1/device/duty-state")

    assert response.status_code == 200
    data = response.json()
    assert data["duty_active"] is False
    assert data["incident_active"] is False
    assert data["should_track"] is False
    assert data["incident_count"] == 1
    assert data["incident"] == {
        "id": incident_id,
        "url": f"/einsatz/{incident_id}",
        "alarm_type_code": "B2",
        "address": "Hauptstrasse 5, Musterstadt",
        "lat": None,
        "lng": None,
        "gmaps_url": None,
        "meldung": None,
        "objekt": None,
        "started_at": "2026-08-03T11:02:00Z",
        "is_exercise": False,
        "phase": "einsatzstelle",
        "phase_index": 2,
        "phase_count": 4,
        "phase_label": "Am Einsatzort",
        "phase_source": "org",
        "unit_count": 1,
    }
    assert data["server_time"].endswith("Z")


@pytest.mark.parametrize(
    ("statuses", "own_status", "expected_phase", "expected_source"),
    [
        ([], None, "alarmiert", "org"),
        (["Einsatz übernommen"], None, "anfahrt", "org"),
        (["Einsatzbereit", "Am Einsatzort"], None, "einsatzstelle", "org"),
        (["Einsatzbereit", "Einsatzbereit"], None, "abschluss", "org"),
        (["Am Einsatzort"], "Einsatz übernommen", "anfahrt", "vehicle"),
    ],
)
def test_phasenmatrix(client, setup_db, statuses, own_status, expected_phase, expected_source):
    user_id, device_id, org_id, own_vehicle_id, _ = _create_device()
    incident_id = _create_incident(org_id, started_at=datetime(2026, 8, 3, 10, 0))
    for status in statuses:
        _add_vehicle(incident_id, org_id, status)
    if own_status:
        _add_vehicle(incident_id, org_id, own_status, own_vehicle_id)
    _authenticate(client, user_id, device_id)

    data = client.get("/api/v1/device/duty-state").json()

    assert data["duty_active"] is False
    assert data["incident_active"] is (own_status is not None)
    assert data["should_track"] is (own_status is not None)
    assert data["incident"]["phase"] == expected_phase
    assert data["incident"]["phase_source"] == expected_source


def test_fahrzeugrelevanter_einsatz_gewinnt_bei_zwei_aktiven(client, setup_db):
    user_id, device_id, org_id, vehicle_id, _ = _create_device()
    assigned_id = _create_incident(org_id, started_at=datetime(2026, 8, 3, 9, 0), code="T1")
    newest_id = _create_incident(org_id, started_at=datetime(2026, 8, 3, 12, 0), code="B3")
    _add_vehicle(assigned_id, org_id, "Am Einsatzort", vehicle_id)
    _authenticate(client, user_id, device_id)

    data = client.get("/api/v1/device/duty-state").json()

    assert data["incident_count"] == 2
    assert data["incident"]["id"] == assigned_id
    assert data["incident"]["id"] != newest_id
    assert data["incident_active"] is True
    assert data["should_track"] is True


def test_ohne_fahrzeug_gewinnt_neuester_einsatz(client, setup_db):
    user_id, device_id, org_id, _, _ = _create_device(with_vehicle=False, duty_active=True)
    _create_incident(org_id, started_at=datetime(2026, 8, 3, 8, 0), code="T1")
    newest_id = _create_incident(org_id, started_at=datetime(2026, 8, 3, 8, 0) + timedelta(hours=1))
    _authenticate(client, user_id, device_id)

    data = client.get("/api/v1/device/duty-state").json()

    assert data["duty_active"] is True
    assert data["incident_active"] is False
    assert data["should_track"] is True
    assert data["incident_count"] == 2
    assert data["incident"]["id"] == newest_id


def test_cross_org_einsatz_bleibt_unsichtbar(client, setup_db):
    user_id, device_id, _, _, _ = _create_device()
    _, _, foreign_org_id, _, _ = _create_device()
    _create_incident(foreign_org_id, started_at=datetime(2026, 8, 3, 11, 0))
    _authenticate(client, user_id, device_id)

    data = client.get("/api/v1/device/duty-state").json()

    assert data["duty_active"] is False
    assert data["incident_active"] is False
    assert data["should_track"] is False
    assert data["incident_count"] == 0
    assert data["incident"] is None


def test_ohne_device_token_bleibt_antwortform_vollstaendig(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    try:
        org = FireDept(slug=f"live-no-device-{suffix}", name="Ohne Geraet")
        db.add(org)
        db.flush()
        user = User(
            username=f"live-no-device-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name="Ohne Geraet",
            active=True,
            org_id=org.id,
        )
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()
    client.cookies.set("session", sign_session(user_id))

    data = client.get("/api/v1/device/duty-state").json()

    assert data["duty_active"] is False
    assert data["incident_active"] is False
    assert data["should_track"] is False
    assert data["incident_count"] == 0
    assert data["incident"] is None
    assert data["server_time"].endswith("Z")


def test_session_user_ohne_device_token_sieht_org_einsatz(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    try:
        org = FireDept(slug=f"live-no-device-{suffix}", name="Ohne Geraet")
        db.add(org)
        db.flush()
        user = User(
            username=f"live-no-device-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name="Ohne Geraet",
            active=True,
            org_id=org.id,
        )
        db.add(user)
        db.commit()
        user_id = user.id
        org_id = org.id
    finally:
        db.close()
    incident_id = _create_incident(org_id, started_at=datetime(2026, 8, 3, 11, 0))
    client.cookies.set("session", sign_session(user_id))

    data = client.get("/api/v1/device/duty-state").json()

    assert data["duty_active"] is False
    assert data["incident_active"] is False
    assert data["should_track"] is False
    assert data["incident_count"] == 1
    assert data["incident"]["id"] == incident_id


def test_session_user_ohne_device_token_sieht_fremden_einsatz_nicht(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    try:
        org = FireDept(slug=f"live-no-device-{suffix}", name="Ohne Geraet")
        db.add(org)
        db.flush()
        user = User(
            username=f"live-no-device-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name="Ohne Geraet",
            active=True,
            org_id=org.id,
        )
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()
    _, _, foreign_org_id, _, _ = _create_device()
    _create_incident(foreign_org_id, started_at=datetime(2026, 8, 3, 11, 0))
    client.cookies.set("session", sign_session(user_id))

    data = client.get("/api/v1/device/duty-state").json()

    assert data["duty_active"] is False
    assert data["incident_active"] is False
    assert data["should_track"] is False
    assert data["incident_count"] == 0
    assert data["incident"] is None


def test_duty_state_akzeptiert_bearer_device_token(client, setup_db):
    _, device_id, _, _, raw_token = _create_device(duty_active=True)

    response = client.get(
        "/api/v1/device/duty-state",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 200
    assert response.json()["duty_active"] is True
    db = SessionLocal()
    try:
        assert db.get(DeviceToken, device_id).last_used_at is not None
    finally:
        db.close()


@pytest.mark.parametrize("with_vehicle", [False, True], ids=["unbound", "vehicle_bound"])
@pytest.mark.parametrize(
    ("is_exercise", "is_collaboration"),
    [
        pytest.param(False, False, id="own_incident"),
        pytest.param(True, False, id="own_exercise"),
        pytest.param(False, True, id="collaboration_incident"),
    ],
)
def test_bearer_duty_state_liefert_sichtbare_einsaetze_ohne_session_cookie(
    client, setup_db, with_vehicle, is_exercise, is_collaboration
):
    _, _, own_org_id, vehicle_id, raw_token = _create_device(with_vehicle=with_vehicle)
    incident_org_id = own_org_id
    if is_collaboration:
        _, _, incident_org_id, _, _ = _create_device()
    incident_id = _create_incident(
        incident_org_id,
        started_at=datetime(2026, 8, 3, 11, 0),
        is_exercise=is_exercise,
    )
    if is_collaboration:
        _add_collaborating_org(incident_id, own_org_id)
    if vehicle_id:
        _add_vehicle(incident_id, own_org_id, "Einsatz übernommen", vehicle_id)

    data = _bearer_duty_state(client, raw_token)

    assert data["incident_count"] == 1
    assert data["incident"]["id"] == incident_id
    assert data["incident"]["is_exercise"] is is_exercise
    assert data["incident_active"] is with_vehicle
    assert data["should_track"] is with_vehicle


@pytest.mark.parametrize("with_vehicle", [False, True], ids=["unbound", "vehicle_bound"])
@pytest.mark.parametrize("is_exercise", [False, True], ids=["normal", "exercise"])
def test_bearer_duty_state_liefert_aktive_lage_ohne_fahrzeugzuordnung(
    client, setup_db, with_vehicle, is_exercise
):
    _, _, org_id, _, raw_token = _create_device(with_vehicle=with_vehicle)
    lage_id = _create_lage(org_id, is_exercise=is_exercise)

    data = _bearer_duty_state(client, raw_token)

    assert data["lage_count"] == 1
    assert data["lage"] == {
        "id": lage_id,
        "url": f"/lage/{lage_id}",
        "name": "Live Lage",
        "is_exercise": is_exercise,
        "started_at": "2026-08-03T11:00:00Z",
        "counts": {"neu": 0, "in_arbeit": 0, "erledigt": 0, "gesamt": 0},
    }
    assert data["my_lage_queue"] is None
    assert data["incident_active"] is False


def test_bearer_duty_state_liefert_uebungs_lagewarteschlange_fuer_zugeordnetes_fahrzeug(
    client, setup_db
):
    _, _, org_id, vehicle_id, raw_token = _create_device(with_vehicle=True)
    assert vehicle_id is not None
    lage_id = _create_lage(org_id, is_exercise=True)
    site_id = _assign_vehicle_to_lage(lage_id, org_id, vehicle_id)

    data = _bearer_duty_state(client, raw_token)

    assert data["incident_active"] is True
    assert data["should_track"] is True
    assert data["my_lage_queue"] == {
        "lage_id": lage_id,
        "lage_name": "Live Lage",
        "lage_url": f"/lage/{lage_id}",
        "is_exercise": True,
        "current": {
            "id": site_id,
            "bezeichnung": "Abschnitt Nord",
            "meldung": "Wasser im Keller",
            "address": "Dorfplatz 1, Musterstadt",
            "lat": None,
            "lng": None,
            "gmaps_url": None,
            "priority": None,
            "phase": "in_arbeit",
        },
        "upcoming": [],
        "remaining_count": 0,
    }


def test_duty_state_lehnt_widerrufenen_bearer_ab(client, setup_db):
    _, device_id, _, _, raw_token = _create_device()
    db = SessionLocal()
    try:
        db.get(DeviceToken, device_id).revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/v1/device/duty-state",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 401
