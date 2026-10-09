"""DIBOS-Fallback für LIS: Fahrzeugstatus/-position, externe Einheiten, Geocoding/Objekt-Matching,
Wiedereröffnen und Live-Push — mit Payloads in der Form echter EventHub-Antworten."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.major_incident import VehiclePosition
from app.models.master import VehicleMaster
from app.services.dibos import dibos_enrich
from app.services.incident_service import create_incident
from app.services.lis import lis_health
from tests.conftest import TestingSession

ORG_ID = 1


@pytest.fixture(autouse=True)
def _reset_dibos_fallback_state():
    from app.models.dibos import OrgDibosConfig
    from app.models.lis import OrgLisConfig

    dibos_enrich._fallback_active.clear()
    lis_health._last_ok.clear()
    db = _session()
    db.query(OrgDibosConfig).filter_by(org_id=ORG_ID).delete()
    db.query(OrgLisConfig).filter_by(org_id=ORG_ID).delete()
    db.commit()
    db.close()
    yield
    dibos_enrich._fallback_active.clear()
    lis_health._last_ok.clear()
    db = _session()
    db.query(OrgDibosConfig).filter_by(org_id=ORG_ID).delete()
    db.query(OrgLisConfig).filter_by(org_id=ORG_ID).delete()
    db.commit()
    db.close()


def _session():
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _event(number, *, units=None, closed=None, lat=47.47, lng=9.75):
    return {
        "eventNumber": number,
        "lev3": "F_WOLFU",
        "tycod": "t1",
        "tycodDescription": "Test",
        "created": "2026-01-05T10:00:00",
        "dispatched": "2026-01-05T10:01:00",
        "closed": closed,
        "callerList": [],
        "targetList": [],
        "comments": [],
        "personResponseList": [],
        "unitList": units or [],
        "locationStreet": "Teststrasse",
        "locationStreetNo": "1",
        "locationCity": "Wolfurt",
        "locationLatitude": lat,
        "locationLongitude": lng,
    }


def _incident(db, number, *, closed=False, lis_operation_id=None):
    incident, _ = create_incident(db, "T1", primary_org_id=ORG_ID, started_at=datetime(2026, 1, 5, 10, tzinfo=UTC))
    incident.lis_operation_number = number
    incident.lis_operation_id = lis_operation_id
    if closed:
        incident.status = "closed"
        incident.closed_via_lis_auto = True
    db.commit()
    return incident.id


def _vehicle(db, unid):
    vehicle = VehicleMaster(dept_id=ORG_ID, code=f"D{unid}", name="DIBOS Fahrzeug", type="TLF", lis_reference_id=unid)
    db.add(vehicle)
    db.flush()
    return vehicle


def test_1_fallback_creates_updates_and_filters_vehicle_statuses():
    number = "dibos-fallback-status-1"
    db = _session()
    incident_id = _incident(db, number)
    first = _vehicle(db, "u-s5")
    existing = _vehicle(db, "u-s4")
    ignored = _vehicle(db, "u-s2")
    first_id, existing_id, ignored_id = first.id, existing.id, ignored.id
    active = db.query(IncidentColumn).filter_by(incident_id=incident_id, code="active").one()
    db.add(
        IncidentVehicle(
            incident_id=incident_id,
            column_id=active.id,
            vehicle_master_id=existing.id,
            unit_status="Einsatz übernommen",
        )
    )
    db.commit()
    db.close()
    units = [
        {
            "unid": "u-s5",
            "unitType": "fahrzeug",
            "eventNumber": number,
            "currentStatusText": "S5",
            "latitude": 47.471,
            "longitude": 9.751,
        },
        {
            "unid": "u-s4",
            "unitType": "fahrzeug",
            "eventNumber": number,
            "currentStatusText": "S5",
            "latitude": 47.472,
            "longitude": 9.752,
        },
        {"unid": "u-s2", "unitType": "fahrzeug", "eventNumber": number, "currentStatusText": "S2"},
        {"unid": "unknown", "unitType": "fahrzeug", "eventNumber": number, "currentStatusText": "S5"},
        {"unid": "wache", "unitType": "wache", "eventNumber": number, "currentStatusText": "S5"},
        {"unid": "u-s5", "unitType": "fahrzeug", "eventNumber": "other", "currentStatusText": "S4"},
    ]
    result = dibos_enrich.enrich_events_for_org(ORG_ID, [_event(number)], raw_units=units)
    check = _session()
    cards = check.query(IncidentVehicle).filter_by(incident_id=incident_id).all()
    assert result["vehicle_changed_ids"] == [incident_id]
    dibos_cards = [card for card in cards if card.vehicle_master_id in {first_id, existing_id, ignored_id}]
    assert {(card.vehicle_master_id, card.unit_status) for card in dibos_cards} == {
        (first_id, "Am Einsatzort"),
        (existing_id, "Am Einsatzort"),
    }
    assert not any(card.vehicle_master_id == ignored_id for card in cards)
    check.close()


def test_2_healthy_lis_blocks_dibos_status_and_position():
    from app.models.lis import OrgLisConfig

    number = "dibos-lis-authoritative-2"
    db = _session()
    incident_id = _incident(db, number, lis_operation_id="lis-op-2")
    vehicle = _vehicle(db, "u-lis")
    db.add(
        OrgLisConfig(
            org_id=ORG_ID, enabled=True, base_url="https://lis", organization_id="1", username="u", password_enc="x"
        )
    )
    vehicle_id = vehicle.id
    db.commit()
    db.close()
    lis_health.mark_lis_ok(ORG_ID)
    result = dibos_enrich.enrich_events_for_org(
        ORG_ID,
        [_event(number)],
        raw_units=[
            {
                "unid": "u-lis",
                "unitType": "fahrzeug",
                "eventNumber": number,
                "currentStatusText": "S5",
                "latitude": 47.4,
                "longitude": 9.7,
            }
        ],
    )
    check = _session()
    assert result["vehicle_changed_ids"] == []
    assert check.query(IncidentVehicle).filter_by(incident_id=incident_id, vehicle_master_id=vehicle_id).count() == 0
    assert check.query(VehiclePosition).filter_by(vehicle_id=vehicle_id).count() == 0
    check.close()


def test_3_positions_skip_station_duplicates_and_manual_override():
    number = "dibos-position-3"
    db = _session()
    _incident(db, number)
    vehicle = _vehicle(db, "u-pos")
    vehicle_id = vehicle.id
    db.commit()
    db.close()
    unit = {
        "unid": "u-pos",
        "unitType": "fahrzeug",
        "eventNumber": number,
        "currentStatusText": "S5",
        "latitude": 47.470001,
        "longitude": 9.750001,
    }
    station = {"unid": "station", "unitType": "wache", "eventNumber": number, "latitude": 47.47, "longitude": 9.75}
    dibos_enrich.enrich_events_for_org(ORG_ID, [_event(number)], raw_units=[unit, station])
    check = _session()
    assert check.query(VehiclePosition).filter_by(vehicle_id=vehicle_id).count() == 0
    check.close()
    dibos_enrich.enrich_events_for_org(ORG_ID, [_event(number)], raw_units=[unit])
    dibos_enrich.enrich_events_for_org(ORG_ID, [_event(number)], raw_units=[unit])
    check = _session()
    positions = check.query(VehiclePosition).filter_by(vehicle_id=vehicle_id).all()
    assert len(positions) == 1 and positions[0].source == "dibos"
    positions[0].source = "manual"
    check.commit()
    check.close()
    moved = {**unit, "latitude": 47.48, "longitude": 9.76}
    dibos_enrich.enrich_events_for_org(ORG_ID, [_event(number)], raw_units=[moved])
    check = _session()
    positions = check.query(VehiclePosition).filter_by(vehicle_id=vehicle_id).all()
    assert len(positions) == 1 and positions[0].lat == 47.470001
    check.close()


def test_4_external_units_are_opt_in_and_idempotent():
    from app.models.dibos import OrgDibosConfig

    number = "dibos-external-4"
    foreign = {
        "unid": "foreign-1",
        "unidRfl": "P_LLZ 1",
        "lev3": "P_LLZ",
        "unitType": "fahrzeug",
        "currentStatusText": "S5",
    }
    db = _session()
    _incident(db, number)
    db.add(OrgDibosConfig(org_id=ORG_ID, sync_external_units=True))
    db.commit()
    db.close()
    event = _event(number, units=[foreign])
    dibos_enrich.enrich_events_for_org(ORG_ID, [event], raw_units=[{**foreign, "eventNumber": number}])
    dibos_enrich.enrich_events_for_org(ORG_ID, [event], raw_units=[{**foreign, "eventNumber": number}])
    check = _session()
    rows = check.query(VehicleMaster).filter_by(dept_id=ORG_ID, lis_reference_id="foreign-1").all()
    assert len(rows) == 1 and rows[0].is_external is True
    check.close()


def test_4_external_units_disabled_creates_nothing():
    number = "dibos-external-off-4"
    foreign = {
        "unid": "foreign-off",
        "unidRfl": "P_LLZ 2",
        "lev3": "P_LLZ",
        "unitType": "fahrzeug",
        "currentStatusText": "S5",
    }
    db = _session()
    _incident(db, number)
    db.close()
    dibos_enrich.enrich_events_for_org(
        ORG_ID, [_event(number, units=[foreign])], raw_units=[{**foreign, "eventNumber": number}]
    )
    check = _session()
    assert check.query(VehicleMaster).filter_by(dept_id=ORG_ID, lis_reference_id="foreign-off").count() == 0
    check.close()


@pytest.mark.asyncio
async def test_5_geocode_background_sets_coordinates_and_matches(monkeypatch):
    db = _session()
    incident_id = _incident(db, "dibos-geocode-5")
    incident = db.get(Incident, incident_id)
    incident.address_street, incident.address_city = "Teststrasse", "Wolfurt"
    db.commit()
    db.close()
    calls = []

    async def geocode(*args):
        return SimpleNamespace(lat=47.5, lng=9.7)

    async def match(value):
        calls.append(value)

    monkeypatch.setattr("app.services.geocoding.geocode_address", geocode)
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", match)
    await dibos_enrich._geocode_and_match_dibos_incident(incident_id, ORG_ID)
    check = _session()
    incident = check.get(Incident, incident_id)
    assert (incident.lat, incident.lng, calls) == (47.5, 9.7, [incident_id])
    check.close()


@pytest.mark.asyncio
async def test_5_only_open_coordinate_less_creations_schedule_geocoding(monkeypatch):
    scheduled = []

    def background(coro, label, incident_id):
        scheduled.append((label, incident_id))
        coro.close()

    monkeypatch.setattr(dibos_enrich, "_start_background", background)
    open_event = _event("dibos-schedule-5", lat=None, lng=None)
    closed_event = _event("dibos-closed-schedule-5", closed="2026-01-05T11:00:00", lat=None, lng=None)
    open_event["locationStreet"] = "Geocode open"
    closed_event["locationStreet"] = "Geocode closed"
    await dibos_enrich.enrich_and_broadcast(ORG_ID, [open_event], create_incidents=True)
    await dibos_enrich.enrich_and_broadcast(ORG_ID, [closed_event], create_incidents=True)
    assert [label for label, _ in scheduled if label == "DIBOS-Geocoding/Objekt-Matching"] == [
        "DIBOS-Geocoding/Objekt-Matching"
    ]


@pytest.mark.asyncio
async def test_5_existing_coordinates_do_not_call_geocoder(monkeypatch):
    db = _session()
    incident_id = _incident(db, "dibos-geocode-present-5")
    incident = db.get(Incident, incident_id)
    incident.lat, incident.lng = 47.4, 9.7
    db.commit()
    db.close()

    async def geocode(*args):
        raise AssertionError("coordinates already exist")

    async def match(*args):
        pass

    monkeypatch.setattr("app.services.geocoding.geocode_address", geocode)
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", match)
    await dibos_enrich._geocode_and_match_dibos_incident(incident_id, ORG_ID)


def test_6_reopens_only_when_lis_is_not_delivering_and_locked_events_stay_open():
    from app.models.lis import OrgLisConfig

    number = "dibos-reopen-6"
    db = _session()
    reopened_id = _incident(db, number, closed=True)
    locked_id = _incident(db, "dibos-locked-6")
    lis_id = _incident(db, "dibos-reopen-lis-6", closed=True, lis_operation_id="lis-reopen-6")
    locked = db.get(Incident, locked_id)
    locked.lis_auto_close_locked = True
    db.add(
        OrgLisConfig(
            org_id=ORG_ID, enabled=True, base_url="https://lis", organization_id="1", username="u", password_enc="x"
        )
    )
    db.commit()
    db.close()
    dibos_enrich.enrich_events_for_org(ORG_ID, [_event(number)])
    lis_health.mark_lis_ok(ORG_ID)
    dibos_enrich.enrich_events_for_org(ORG_ID, [_event("dibos-reopen-lis-6")])
    dibos_enrich.enrich_events_for_org(
        ORG_ID, [], raw_public_events=[_event("dibos-locked-6", closed="2026-01-05T11:00:00")]
    )
    check = _session()
    reopened, locked = check.get(Incident, reopened_id), check.get(Incident, locked_id)
    assert reopened.status == "active" and reopened.lis_auto_close_locked is True
    assert check.get(Incident, lis_id).status == "closed"
    assert locked.status == "active"
    check.close()


@pytest.mark.asyncio
async def test_7_live_notifications_continue_after_one_failure(monkeypatch):
    db = _session()
    closed_id = _incident(db, "dibos-live-close-7")
    vehicle_id = _incident(db, "dibos-live-unit-7")
    db.close()

    async def fake_thread(*args, **kwargs):
        return {
            "changed_ids": [vehicle_id],
            "vehicle_changed_ids": [vehicle_id],
            "rsvp_changed_ids": [],
            "closed_ids": [closed_id],
            "objekt_match_ids": [],
            "created_ids": [],
            "created_match_ids": [],
            "ok": True,
        }

    reasons = []

    async def notify(db, incident, **kwargs):
        reasons.append(kwargs["reason"])
        if kwargs["reason"] == "closed":
            raise RuntimeError("expected")

    async def broadcast(*args, **kwargs):
        pass

    def background(coro, *args):
        coro.close()

    monkeypatch.setattr(dibos_enrich.asyncio, "to_thread", fake_thread)
    monkeypatch.setattr("app.services.incident_live_notify.notify_incident_live", notify)
    monkeypatch.setattr("app.services.broadcast.manager.broadcast", broadcast)
    monkeypatch.setattr(dibos_enrich, "_start_background", background)
    assert await dibos_enrich.enrich_and_broadcast(ORG_ID, []) is True
    assert reasons == ["closed", "unit_status"]


def test_8_admin_dibos_settings_saves_sync_external_units(client):
    from app.core.security import hash_password
    from app.models.user import Role, User, UserRole

    db = _session()
    user = User(
        username="dibos-settings-admin",
        password_hash=hash_password("Test1234!"),
        display_name="Admin",
        org_id=ORG_ID,
        active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="admin").one().id))
    db.commit()
    db.close()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": "dibos-settings-admin", "password": "Test1234!", "_csrf": csrf})
    csrf = client.cookies.get("ec_csrf")
    assert (
        client.post(
            "/admin/dibos/save",
            data={"target_org_id": ORG_ID, "sync_external_units": "1", "_csrf": csrf},
            follow_redirects=False,
        ).status_code
        == 302
    )
    check = _session()
    from app.models.dibos import OrgDibosConfig

    assert check.query(OrgDibosConfig).filter_by(org_id=ORG_ID).one().sync_external_units is True
    check.close()
