"""Phase-1-Routen: Org-Vorgaben, Kontrollen, Notfall und Zuständigkeit."""
import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.models.breathing import BreathingTroop
from app.models.incident import Incident, IncidentChange
from app.models.master import OrgSettings, SystemSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services.breathing_service import get_time_warning
from tests.test_breathing_routes import _members_form, breathing_routes  # noqa: F401


def _user(ctx):
    org_id = ctx.db.get(Incident, ctx.incident_id).primary_org_id
    return ctx.db.query(User).filter(User.org_id == org_id).one()


def _role(ctx, code):
    user = _user(ctx)
    ctx.db.query(UserRole).filter(UserRole.user_id == user.id).delete()
    role = ctx.db.query(Role).filter(Role.code == code).one()
    ctx.db.add(UserRole(user_id=user.id, role_id=role.id))
    ctx.db.commit()
    ctx.db.expire_all()


def _settings(ctx, **values):
    org_id = ctx.db.get(Incident, ctx.incident_id).primary_org_id
    row = ctx.db.get(OrgSettings, org_id)
    if row is None:
        row = OrgSettings(org_id=org_id)
        ctx.db.add(row)
    for field, value in values.items():
        setattr(row, field, value)
    ctx.db.commit()
    return row


def _active(ctx):
    ctx.troop.status = "im_einsatz"
    ctx.troop.planned_duration_min = 30
    ctx.troop.entry_at = datetime.now(UTC) - timedelta(minutes=11)
    ctx.db.commit()


@pytest.mark.parametrize("redeploy", [False, True])
@pytest.mark.parametrize(("preset", "manual", "duration"), [
    ("1x9", None, 24), ("1x9", 50, 24), ("", 50, 24),
    ("manuell", None, 24), ("manuell", 17, 17), ("manuell", 1, 1), ("manuell", 120, 120),
])
def test_duration_and_creation_details(breathing_routes, redeploy, preset, manual, duration):
    ctx = breathing_routes
    _settings(ctx, asuew_erwartete_einsatzzeit_min=24)
    user = _user(ctx)
    user.full_name = "Überwacher Test"
    ctx.db.commit()
    if redeploy:
        ctx.troop.status = "erholt"
        ctx.db.commit()
    url = f"{ctx.url}/erneut-einsetzen" if redeploy else f"/einsatz/{ctx.incident_id}/atemschutz/trupp"
    data = {"name": "Phase 1", "bottle_preset": preset, **_members_form(),
            "angriffsweg": "  Stiege A  ", "funkkanal": "  Kanal 3  "}
    if manual is not None:
        data["planned_duration_min"] = manual
    assert ctx.post(url, data).status_code == 303
    ctx.db.expire_all()
    troop = ctx.troop if redeploy else ctx.db.query(BreathingTroop).filter_by(
        incident_id=ctx.incident_id, name="Phase 1",
    ).one()
    assert troop.planned_duration_min == duration
    assert troop.bottle_preset == (preset or None)
    if not redeploy:
        assert troop.ueberwacher_user_id == user.id
        assert troop.ueberwacher_name == "Überwacher Test"
        assert troop.angriffsweg == "Stiege A"
        assert troop.funkkanal == "Kanal 3"


@pytest.mark.parametrize("redeploy", [False, True])
@pytest.mark.parametrize("duration", [0, 121])
def test_invalid_manual_duration(breathing_routes, redeploy, duration):
    ctx = breathing_routes
    url = f"{ctx.url}/erneut-einsetzen" if redeploy else f"/einsatz/{ctx.incident_id}/atemschutz/trupp"
    response = ctx.post(url, {"name": "Invalid", "bottle_preset": "manuell",
                              "planned_duration_min": duration, **_members_form()})
    assert response.status_code == (400 if redeploy else 303)
    if not redeploy:
        assert response.headers["location"].endswith("?error=invalid_input")
    ctx.broadcast.assert_not_awaited()


def test_create_explicit_supervisor_and_truncation(breathing_routes):
    ctx = breathing_routes
    response = ctx.post(f"/einsatz/{ctx.incident_id}/atemschutz/trupp", {
        "name": "Details", "ueberwacher_name": "X" * 160,
        "angriffsweg": "A" * 310, "funkkanal": "F" * 60, **_members_form(),
    })
    assert response.status_code == 303
    troop = ctx.db.query(BreathingTroop).filter_by(incident_id=ctx.incident_id, name="Details").one()
    assert troop.ueberwacher_name == "X" * 150
    assert troop.angriffsweg == "A" * 300
    assert troop.funkkanal == "F" * 50


def test_kontrolle_requires_pressure_and_completes_due_warning(breathing_routes):
    ctx = breathing_routes
    _active(ctx)
    assert get_time_warning(ctx.troop) == "one_third_due"
    invalid = ctx.post(f"{ctx.url}/kontrolle", {"stufe": "one_third"})
    assert invalid.status_code == 400
    assert "Flaschendruck" in invalid.text
    ctx.broadcast.assert_not_awaited()
    response = ctx.post(f"{ctx.url}/kontrolle", {"stufe": "one_third", "pressure_bar": 200})
    assert response.status_code == 204
    ctx.db.expire_all()
    assert get_time_warning(ctx.troop) == "ok"
    assert ctx.troop.active_kontrollen[0].niedrigster_druck == 200
    ctx.broadcast.assert_awaited_once_with(ctx.incident_id, {
        "type": "troop_kontrolle", "troop_id": ctx.troop.id, "stufe": "one_third", "art": "funk",
    })


def test_funkverlust_on_off(breathing_routes):
    ctx = breathing_routes
    _active(ctx)
    assert ctx.post(f"{ctx.url}/funkverlust", {"aktiv": "1"}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.funk_verloren_at is not None
    ctx.broadcast.assert_any_await(ctx.incident_id, {
        "type": "troop_warning", "troop_id": ctx.troop.id, "kind": "funk_verloren",
    })
    ctx.broadcast.reset_mock()
    assert ctx.post(f"{ctx.url}/funkverlust", {"aktiv": "0"}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.funk_verloren_at is None
    ctx.broadcast.assert_awaited_once_with(ctx.incident_id, {"type": "troop_funk", "troop_id": ctx.troop.id})


@pytest.mark.parametrize(("role", "expected"), [("recorder", 403), ("incident_leader", 204), ("admin", 204)])
def test_notfall_confirmation_and_clear_permissions(breathing_routes, role, expected):
    ctx = breathing_routes
    _active(ctx)
    response = ctx.post(f"{ctx.url}/notfall", {})
    assert response.status_code == 400
    assert response.text == "Notfall muss ausdrücklich bestätigt werden."
    ctx.broadcast.assert_not_awaited()
    assert ctx.post(f"{ctx.url}/notfall", {"bestaetigt": "1"}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.notfall_at is not None
    assert ctx.troop.notfall_von_user_id == _user(ctx).id
    ctx.broadcast.assert_any_await(ctx.incident_id, {
        "type": "troop_warning", "troop_id": ctx.troop.id, "kind": "notfall",
    })
    _role(ctx, role)
    response = ctx.post(f"{ctx.url}/notfall/aufheben", {})
    assert response.status_code == expected
    ctx.db.refresh(ctx.troop)
    assert (ctx.troop.notfall_at is None) == (expected == 204)


@pytest.mark.parametrize(("route", "data"), [
    ("kontrolle", {"stufe": "one_third", "pressure_bar": 200}),
    ("funkverlust", {"aktiv": "1"}), ("notfall", {"bestaetigt": "1"}),
    ("notfall/aufheben", {}), ("details", {"angriffsweg": "Fremd"}),
])
def test_new_routes_reject_cross_incident(breathing_routes, route, data):
    ctx = breathing_routes
    response = ctx.post(f"/einsatz/{ctx.other_incident_id}/atemschutz/{ctx.troop.id}/{route}", data)
    assert response.status_code == 404
    ctx.broadcast.assert_not_awaited()


def test_details_partial_update_and_takeover(breathing_routes):
    ctx = breathing_routes
    ctx.troop.funkkanal = "Kanal 2"
    ctx.troop.ueberwacher_name = "Bisher"
    ctx.db.commit()
    assert ctx.post(f"{ctx.url}/details", {"angriffsweg": "  Hintereingang  "}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.angriffsweg == "Hintereingang"
    assert ctx.troop.funkkanal == "Kanal 2"
    assert ctx.troop.ueberwacher_name == "Bisher"
    assert ctx.post(f"{ctx.url}/details", {"uebernehmen": "1"}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.ueberwacher_user_id == _user(ctx).id
    assert ctx.troop.ueberwacher_name == _user(ctx).username
    assert ctx.post(f"{ctx.url}/details", {"uebernehmen": "1", "ueberwacher_name": "  Melder  "}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.ueberwacher_name == "Melder"
    changes = ctx.db.query(IncidentChange).filter_by(incident_id=ctx.incident_id, action="troop.details").all()
    assert len(changes) == 3
    assert json.loads(changes[0].after_json)["angriffsweg"] == "Hintereingang"


def test_ack_control_requires_pressure(breathing_routes):
    ctx = breathing_routes
    response = ctx.post(f"{ctx.url}/ack", {"kind": "one_third"})
    assert response.status_code == 400
    assert "Druck" in response.text
    assert response.headers["content-type"].startswith("text/plain")
    ctx.broadcast.assert_not_awaited()


def test_settings_clamp_audit_and_missing_fields(breathing_routes):
    ctx = breathing_routes
    _role(ctx, "admin")
    settings = _settings(ctx)
    data = {"asuew_erwartete_einsatzzeit_min": 1, "asuew_rueckzug_untergrenze_bar": 200,
            "asuew_gelb_prozent": 100, "asuew_erholzeit_min": -5}
    assert ctx.post("/admin/settings/org", data).status_code == 303
    ctx.db.refresh(settings)
    expected = dict(zip(data, [5, 100, 95, 0]))
    assert {name: getattr(settings, name) for name in data} == expected
    audits = ctx.db.query(AuditLog).filter_by(org_id=settings.org_id, action="breathing.asuew_settings").all()
    assert len(audits) == 1
    payload = json.loads(audits[0].payload_json)
    assert payload["neu"] == expected
    assert payload["alt"] == dict(zip(data, [30, 60, 75, 30]))
    assert ctx.post("/admin/settings/org", {}).status_code == 303
    ctx.db.refresh(settings)
    assert {name: getattr(settings, name) for name in data} == expected
    assert ctx.db.query(AuditLog).filter_by(org_id=settings.org_id, action="breathing.asuew_settings").count() == 1
    flag = ctx.db.get(SystemSettings, "breathing_enabled")
    flag.value = "false"
    ctx.db.commit()
    assert ctx.post("/admin/settings/org", {"asuew_gelb_prozent": 60}).status_code == 303
    ctx.db.refresh(settings)
    assert settings.asuew_gelb_prozent == 95


@pytest.mark.parametrize(("field", "value", "expected"), [
    ("asuew_erwartete_einsatzzeit_min", 121, 120),
    ("asuew_rueckzug_untergrenze_bar", 29, 30),
    ("asuew_gelb_prozent", 49, 50),
    ("asuew_erholzeit_min", 121, 120),
])
def test_settings_other_clamp_boundaries(breathing_routes, field, value, expected):
    ctx = breathing_routes
    _role(ctx, "admin")
    settings = _settings(ctx)
    assert ctx.post("/admin/settings/org", {field: value}).status_code == 303
    ctx.db.refresh(settings)
    assert getattr(settings, field) == expected


def test_settings_html_defaults_and_saved_values(breathing_routes, client):
    ctx = breathing_routes
    _role(ctx, "admin")
    names = ("asuew_erwartete_einsatzzeit_min", "asuew_rueckzug_untergrenze_bar",
             "asuew_gelb_prozent", "asuew_erholzeit_min")
    page = client.get("/admin/settings")
    assert page.status_code == 200
    for name, value in zip(names, (30, 60, 75, 30)):
        field = re.search(rf'<input[^>]*name="{name}"[^>]*>', page.text)
        assert field and f'value="{value}"' in field.group()
    _settings(ctx, **dict(zip(names, (20, 50, 80, 0))))
    page = client.get("/admin/settings")
    assert page.status_code == 200
    for name, value in zip(names, (20, 50, 80, 0)):
        field = re.search(rf'<input[^>]*name="{name}"[^>]*>', page.text)
        assert field and f'value="{value}"' in field.group()


def test_board_settings_context_and_warning_threshold(breathing_routes, client):
    ctx = breathing_routes
    _settings(ctx, asuew_gelb_prozent=90)
    ctx.troop.active_members[0].current_press = 260
    ctx.db.commit()
    page = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    assert page.status_code == 200
    assert page.context["asuew_settings"].gelb_prozent == 90
    assert page.context["current_user_label"] == _user(ctx).username
    assert page.context["troops_with_warnings"][0][1] == "yellow"


@pytest.mark.parametrize("route", ["status", "druck", "einsatzziel"])
def test_warning_broadcast_uses_org_threshold(breathing_routes, route):
    ctx = breathing_routes
    _settings(ctx, asuew_gelb_prozent=90)
    _active(ctx)
    member = ctx.troop.active_members[0]
    member.current_press = 260
    ctx.db.commit()
    data = {"status": "rueckzug"} if route == "status" else {"troop_member_id": member.id, "pressure_bar": 260}
    assert ctx.post(f"{ctx.url}/{route}", data).status_code == (303 if route == "status" else 204)
    assert ctx.broadcast.await_args.args[1]["warning"] == "yellow"


def test_details_empty_and_long_fields(breathing_routes):
    ctx = breathing_routes
    ctx.troop.funkkanal = "Kanal 1"
    ctx.db.commit()
    assert ctx.post(f"{ctx.url}/details", {
        "angriffsweg": "A" * 310, "funkkanal": "", "ueberwacher_name": "U" * 160,
    }).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.angriffsweg == "A" * 300
    assert ctx.troop.funkkanal is None
    assert ctx.troop.ueberwacher_name == "U" * 150
    assert ctx.post(f"{ctx.url}/details", {"uebernehmen": "1", "ueberwacher_name": ""}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.ueberwacher_name is None
    assert ctx.troop.ueberwacher_user_id == _user(ctx).id
