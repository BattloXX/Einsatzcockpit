"""App-Bindung der Eingaben und bewusst offene Alarmmeldungen."""
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.breathing import BreathingGeraetSitzung
from app.models.master import SystemSettings
from app.models.user import AuditLog
from tests.test_breathing_routes import _members_form, breathing_routes  # noqa: F401
from tests.test_breathing_routes_phase1 import _role, _settings, _user


def _session(ctx, client, **values):
    now = datetime.now(UTC).replace(tzinfo=None)
    data = dict(incident_id=ctx.incident_id, user_id=_user(ctx).id,
                session_key=uuid4().hex, platform="android", gestartet_at=now,
                letzter_heartbeat_at=now)
    data.update(values)
    session = BreathingGeraetSitzung(**data)
    ctx.db.add(session)
    ctx.db.commit()
    client.cookies.set("ec_asuew", session.session_key)
    return session


WRITES = [
    ("druck", {"pressure_bar": 200, "troop_member_id": 0}),
    ("status", {"status": "rueckzug"}),
    ("einsatzziel", {"pressure_bar": 200, "troop_member_id": 0}),
    ("meldung", {"text": "Test"}), ("ack", {"kind": "withdraw"}),
    ("kontrolle", {"stufe": "frei", "pressure_bar": 200}), ("standort", {"location_text": "A"}),
    ("auftrag", {"task_text": "A"}), ("details", {"funkkanal": "3"}),
    ("starten", {"override_reason": "Freigabe fuer Test"}), ("enddruck", {"pressure_bar": 50, "troop_member_id": 0}),
    ("erneut-einsetzen", _members_form()), ("notfall/aufheben", {}),
    ("funkverlust", {"aktiv": "0"}), ("create", {"name": "B", **_members_form()}),
]


@pytest.mark.parametrize("route,data", WRITES)
@pytest.mark.parametrize("mode", ["off", "missing", "valid"])
def test_write_gating(client, breathing_routes, route, data, mode):
    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=mode != "off")
    ctx.troop.status = {"starten": "bereit", "erneut-einsetzen": "erholt",
                        "enddruck": "zurueck"}.get(route, "im_einsatz")
    ctx.troop.entry_at = datetime.now(UTC).replace(tzinfo=None)
    ctx.db.commit()
    data = dict(data)
    if "troop_member_id" in data:
        data["troop_member_id"] = ctx.troop.active_members[0].id
    if mode == "valid":
        _session(ctx, client)
    url = f"/einsatz/{ctx.incident_id}/atemschutz/trupp" if route == "create" else f"{ctx.url}/{route}"
    response = ctx.post(url, data)
    if mode == "missing":
        assert response.status_code == 403
        assert "Atemschutzüberwachung nur in der Einsatzcockpit-App." in response.text
    else:
        assert response.status_code == (303 if route in ("create", "erneut-einsetzen", "status") else 204)


@pytest.mark.parametrize("route,data", [("notfall", {"bestaetigt": "1"}), ("funkverlust", {"aktiv": "1"})])
def test_alarm_without_session(client, breathing_routes, route, data):
    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=True)
    ctx.troop.status = "im_einsatz"
    ctx.db.commit()
    assert ctx.post(f"{ctx.url}/{route}", data).status_code == 204


@pytest.mark.parametrize("invalid", ["incident", "user", "old", "ended", "expired"])
def test_invalid_session(client, breathing_routes, invalid):
    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=True)
    values = {}
    now = datetime.now(UTC).replace(tzinfo=None)
    if invalid == "incident":
        values["incident_id"] = ctx.other_incident_id
    elif invalid == "user":
        # An existing different user is enough: validation binds to the logged-in user.
        from app.models.user import User
        values["user_id"] = ctx.db.query(User).filter(User.id != _user(ctx).id).first().id
    elif invalid == "old":
        values["letzter_heartbeat_at"] = now - timedelta(seconds=61)
    elif invalid == "ended":
        values["beendet_at"] = now
    else:
        values["gestartet_at"] = now - timedelta(hours=25)
    _session(ctx, client, **values)
    assert ctx.post(f"{ctx.url}/meldung", {}).status_code == 403


def test_header_session(client, breathing_routes):
    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=True)
    session = _session(ctx, client)
    client.cookies.delete("ec_asuew")
    response = client.post(f"{ctx.url}/meldung", data={"_csrf": client.cookies.get("ec_csrf")},
                           headers={"X-ECP-ASUEW": session.session_key})
    assert response.status_code == 204


def test_app_settings_marker_audit_and_system_flag(client, breathing_routes):
    ctx = breathing_routes
    _role(ctx, "admin")
    row = _settings(ctx)
    path = "/admin/settings/org"
    assert ctx.post(path, {"asuew_app_pflicht_raw": "1"}).status_code == 303
    ctx.db.refresh(row)
    assert not row.asuew_app_pflicht
    data = {"asuew_app_form": "1", "asuew_app_pflicht_raw": "1"}
    assert ctx.post(path, data).status_code == 303
    ctx.db.refresh(row)
    assert row.asuew_app_pflicht
    query = ctx.db.query(AuditLog).filter_by(org_id=row.org_id, action="breathing.asuew_app_pflicht")
    assert query.count() == 1
    assert json.loads(query.one().payload_json) == {"alt": False, "neu": True}
    assert ctx.post(path, data).status_code == 303
    assert query.count() == 1
    assert ctx.post(path, {}).status_code == 303
    ctx.db.refresh(row)
    assert row.asuew_app_pflicht
    flag = ctx.db.get(SystemSettings, "breathing_enabled")
    flag.value = "false"
    ctx.db.commit()
    assert ctx.post(path, {"asuew_app_form": "1"}).status_code == 303
    ctx.db.refresh(row)
    assert row.asuew_app_pflicht
    flag.value = "true"
    ctx.db.commit()
    page = client.get("/admin/settings")
    assert 'name="asuew_app_form" value="1"' in page.text
    assert 'name="asuew_app_pflicht_raw"' in page.text
    assert ctx.post(path, {"asuew_app_form": "1"}).status_code == 303
    ctx.db.refresh(row)
    assert not row.asuew_app_pflicht
    assert query.count() == 2


def test_alarm_exceptions_keep_role_check(client, breathing_routes):
    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=True)
    _role(ctx, "readonly")
    for route, data in [("notfall", {"bestaetigt": "1"}), ("funkverlust", {"aktiv": "1"})]:
        assert ctx.post(f"{ctx.url}/{route}", data).status_code == 403
    assert client.get(f"/einsatz/{ctx.incident_id}/atemschutz/aktive-warnungen").status_code == 200
    board = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    assert board.status_code == 200
    assert not board.context["can_alarm"]
    assert 'hx-post=' not in board.text
