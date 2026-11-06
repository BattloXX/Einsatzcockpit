"""Router-Regressionen fuer Einsatzzuordnung, Status und Druckeingaben."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.models.breathing import BreathingTroop, PressureLog
from app.models.incident import Incident
from app.models.master import FireDept, SystemSettings
from app.models.user import Role, User, UserRole
from app.routers.ui_breathing import manager
from app.services.breathing_service import create_troop
from tests.conftest import TestingSession


@pytest.fixture
def breathing_routes(client, monkeypatch):
    db = TestingSession()
    set_tenant_context(db, None)
    flag = db.get(SystemSettings, "breathing_enabled")
    original_value = flag.value if flag else None
    if flag is None:
        flag = SystemSettings(key="breathing_enabled", value="true")
        db.add(flag)
    else:
        flag.value = "true"
    suffix = uuid4().hex
    org = FireDept(slug=f"asuew-a2-{suffix}", name="ASUEW Router Tests")
    db.add(org)
    db.flush()
    user = User(
        username=f"asuew-a2-{suffix}", password_hash=hash_password("Test1234!"),
        display_name="ASUEW Aufsicht", org_id=org.id, active=True,
    )
    db.add(user)
    db.flush()
    role = db.query(Role).filter(Role.code == "breathing_supervisor").one()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    incident_a = Incident(primary_org_id=org.id)
    incident_b = Incident(primary_org_id=org.id)
    db.add_all([incident_a, incident_b])
    db.flush()
    troop = create_troop(db, incident_a.id, "Trupp A", [
        {"free_text_name": "Person A", "role": "truppfuehrer", "start_press": 300},
        {"free_text_name": "Person B", "role": "truppmann", "start_press": 290},
    ])
    troop.task_text = "Urspruenglicher Auftrag"
    troop.location_text = "Urspruenglicher Standort"
    troop.last_meldung_text = "Urspruengliche Meldung"
    db.commit()
    broadcast = AsyncMock()
    monkeypatch.setattr(manager, "broadcast", broadcast)
    try:
        assert client.get("/login").status_code == 200
        response = client.post("/login", data={
            "username": user.username, "password": "Test1234!",
            "_csrf": client.cookies.get("ec_csrf"),
        }, follow_redirects=False)
        assert response.status_code == 302

        def post(path, data):
            return client.post(path, data={
                **data, "_csrf": client.cookies.get("ec_csrf"),
            }, follow_redirects=False)

        yield SimpleNamespace(
            db=db, troop=troop, incident_id=incident_a.id, other_incident_id=incident_b.id,
            url=f"/einsatz/{incident_a.id}/atemschutz/{troop.id}",
            post=post, broadcast=broadcast, client=client,
        )
    finally:
        db.rollback()
        flag = db.get(SystemSettings, "breathing_enabled")
        if original_value is None:
            if flag is not None:
                db.delete(flag)
        else:
            flag.value = original_value
        db.commit()
        db.close()


def _troop_snapshot(troop):
    return {column.key: getattr(troop, column.key) for column in troop.__table__.columns}


def _members_form(pressure="300"):
    return {
        "member_0_name": "Neue Person A", "member_0_role": "truppfuehrer",
        "member_0_press": pressure, "member_1_name": "Neue Person B",
        "member_1_role": "truppmann", "member_1_press": "290",
    }


@pytest.mark.parametrize(("route", "data"), [
    ("meldung", {"text": "Fremde Meldung"}),
    ("ack", {"kind": "one_third"}),
    ("standort", {"location_text": "Fremder Standort"}),
    ("auftrag", {"task_text": "Fremder Auftrag"}),
])
def test_cross_incident_mutation_returns_404(breathing_routes, route, data):
    ctx = breathing_routes
    before = _troop_snapshot(ctx.troop)
    response = ctx.post(
        f"/einsatz/{ctx.other_incident_id}/atemschutz/{ctx.troop.id}/{route}", data,
    )
    assert response.status_code == 404
    ctx.db.refresh(ctx.troop)
    assert _troop_snapshot(ctx.troop) == before
    ctx.broadcast.assert_not_awaited()


@pytest.mark.parametrize(("status", "expected"), [("zurueck", 409), ("foo", 400)])
def test_invalid_status_returns_client_error(breathing_routes, status, expected):
    ctx = breathing_routes
    before = _troop_snapshot(ctx.troop)
    response = ctx.post(f"{ctx.url}/status", {"status": status})
    assert response.status_code == expected
    assert response.text
    ctx.db.refresh(ctx.troop)
    assert _troop_snapshot(ctx.troop) == before
    ctx.broadcast.assert_not_awaited()


def test_repeated_withdrawal_preserves_timestamp(breathing_routes, monkeypatch):
    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    ctx.db.commit()
    now = datetime.now(UTC)
    monkeypatch.setattr("app.services.breathing_service._now", lambda: now)
    first = ctx.post(f"{ctx.url}/status", {"status": "rueckzug"})
    assert first.status_code == 303
    assert first.headers["location"] == f"/einsatz/{ctx.incident_id}/atemschutz"
    ctx.db.refresh(ctx.troop)
    withdraw_at = ctx.troop.withdraw_at
    assert withdraw_at is not None
    monkeypatch.setattr("app.services.breathing_service._now", lambda: now + timedelta(minutes=1))
    second = ctx.post(f"{ctx.url}/status", {"status": "rueckzug"})
    assert second.status_code == 303
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.status == "rueckzug"
    assert ctx.troop.withdraw_at == withdraw_at


@pytest.mark.parametrize("route", ["druck", "einsatzziel", "enddruck"])
@pytest.mark.parametrize(("pressure", "status", "expected"), [
    ("500", "rueckzug", (400,)),
    ("nan", "rueckzug", (400, 422)),
    ("200", "bereit", (400,)),
])
def test_invalid_pressure_returns_client_error(breathing_routes, route, pressure, status, expected):
    ctx = breathing_routes
    ctx.troop.status = status
    ctx.db.commit()
    member = ctx.troop.active_members[0]
    before = (member.current_press, member.objective_press, member.back_press)
    response = ctx.post(f"{ctx.url}/{route}", {
        "troop_member_id": member.id, "pressure_bar": pressure,
    })
    assert response.status_code in expected
    if response.status_code == 400:
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text
    ctx.db.refresh(member)
    assert (member.current_press, member.objective_press, member.back_press) == before
    assert ctx.db.query(PressureLog).filter(PressureLog.troop_id == ctx.troop.id).count() == 0
    ctx.broadcast.assert_not_awaited()


@pytest.mark.parametrize("pressure", ["abc", "500", "nan"])
def test_create_invalid_pressure_redirects_without_creating_troop(breathing_routes, pressure):
    ctx = breathing_routes
    before = ctx.db.query(BreathingTroop).filter(BreathingTroop.incident_id == ctx.incident_id).count()
    response = ctx.post(f"/einsatz/{ctx.incident_id}/atemschutz/trupp", {
        "name": "Ungueltiger Trupp", **_members_form(pressure),
    })
    assert response.status_code == 303
    assert response.headers["location"] == f"/einsatz/{ctx.incident_id}/atemschutz?error=invalid_input"
    assert ctx.db.query(BreathingTroop).filter(BreathingTroop.incident_id == ctx.incident_id).count() == before
    ctx.broadcast.assert_not_awaited()


@pytest.mark.parametrize("pressure", ["abc", "500"])
def test_redeploy_invalid_pressure_returns_400_without_changes(breathing_routes, pressure):
    ctx = breathing_routes
    ctx.troop.status = "erholt"
    ctx.db.commit()
    member_ids = [member.id for member in ctx.troop.active_members]
    response = ctx.post(f"{ctx.url}/erneut-einsetzen", _members_form(pressure))
    assert response.status_code == 400
    assert response.text
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.status == "erholt"
    assert [member.id for member in ctx.troop.active_members] == member_ids
    assert ctx.troop.deployments == []
    ctx.broadcast.assert_not_awaited()


def test_auftrag_broadcasts_saved_task(breathing_routes):
    ctx = breathing_routes
    response = ctx.post(f"{ctx.url}/auftrag", {"task_text": "  Menschenrettung  "})
    assert response.status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.task_text == "Menschenrettung"
    ctx.broadcast.assert_awaited_once_with(ctx.incident_id, {
        "type": "troop_auftrag", "troop_id": ctx.troop.id, "task_text": "Menschenrettung",
    })


@pytest.mark.parametrize("redeploy", [False, True])
def test_valid_members_form_creates_live_members(breathing_routes, redeploy):
    ctx = breathing_routes
    if redeploy:
        ctx.troop.status = "erholt"
        ctx.db.commit()
        url = f"{ctx.url}/erneut-einsetzen"
    else:
        url = f"/einsatz/{ctx.incident_id}/atemschutz/trupp"
    response = ctx.post(url, {"name": "Neuer Trupp", **_members_form()})
    assert response.status_code == 303
    assert response.headers["location"] == f"/einsatz/{ctx.incident_id}/atemschutz"
    ctx.db.expire_all()
    troop = ctx.troop if redeploy else ctx.db.query(BreathingTroop).filter(
        BreathingTroop.incident_id == ctx.incident_id, BreathingTroop.name == "Neuer Trupp",
    ).one()
    assert troop.status == "bereit"
    assert [(m.free_text_name, m.role, m.start_press) for m in troop.active_members] == [
        ("Neue Person A", "truppfuehrer", 300), ("Neue Person B", "truppmann", 290),
    ]
