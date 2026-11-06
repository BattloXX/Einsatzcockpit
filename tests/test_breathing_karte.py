"""Partielle Board-Antworten und die Prioritaet der Karten."""
import re
from datetime import UTC, datetime

import pytest

from app.models.breathing import BreathingTroop
from app.models.master import SystemSettings
from app.models.user import Role, UserRole
from app.services.breathing_service import troop_urgency
from tests.test_breathing_routes import breathing_routes  # noqa: F401
from tests.test_breathing_routes_phase1 import _user


@pytest.mark.parametrize(("status", "pressure", "warnings", "field", "expected"), [
    ("bereit", "red", ["withdraw"], "notfall_at", 0),
    ("im_einsatz", "red", ["withdraw"], "funk_verloren_at", 1),
    ("rueckzug", "ok", ["withdraw"], None, 2),
    ("im_einsatz", "red", [], None, 2),
    ("rueckzug", "yellow", ["max_exceeded"], None, 3),
    ("im_einsatz", "yellow", ["max_exceeded", "two_third_due"], None, 4),
    ("im_einsatz", "yellow", ["two_third_due"], None, 5),
    ("im_einsatz", "ok", ["one_third_due"], None, 5),
    ("im_einsatz", "yellow", [], None, 6),
    ("im_einsatz", "ok", [], None, 7),
    ("bereit", "ok", [], None, 8),
    ("zurueck", "ok", [], None, 9),
    ("erholt", "ok", [], None, 10),
])
def test_troop_urgency(status, pressure, warnings, field, expected):
    troop = BreathingTroop(status=status)
    if field:
        setattr(troop, field, datetime.now(UTC))
    assert troop_urgency(troop, pressure, warnings) == expected


@pytest.mark.parametrize("status", ["bereit", "im_einsatz", "rueckzug", "zurueck", "erholt"])
def test_card_route_matches_board_card(client, breathing_routes, status):
    ctx = breathing_routes
    ctx.troop.status = status
    ctx.db.commit()
    response = client.get(f"{ctx.url}/karte")
    board = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    assert response.status_code == board.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert f'id="troop-{ctx.troop.id}"' in response.text
    urgency = troop_urgency(ctx.troop, "ok", [])
    assert f'data-urgency="{urgency}" style="order: {urgency};"' in response.text
    assert '<html' not in response.text
    assert 'id="troopsGrid"' not in response.text
    assert 'data-server-now="' in response.text
    # Serverzeit ist pro Antwort neu; der restliche Karteninhalt ist identisch.
    normalize = lambda text: re.sub(r'data-server-now="[^"]+"', 'data-server-now=""', text).strip()
    assert normalize(response.text) in normalize(board.text)
    if status == "erholt":
        assert response.text.count(f'id="redeployTroopModal-{ctx.troop.id}"') == 1


def test_card_route_rejects_other_incident_and_missing_records(client, breathing_routes):
    ctx = breathing_routes
    for url in [
        f"/einsatz/{ctx.other_incident_id}/atemschutz/{ctx.troop.id}/karte",
        f"/einsatz/{ctx.incident_id}/atemschutz/999999999/karte",
        "/einsatz/999999999/atemschutz/uebersicht",
    ]:
        assert client.get(url).status_code == 404


def test_summary_is_standalone_but_board_summary_remains_oob(client, breathing_routes):
    ctx = breathing_routes
    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz/uebersicht")
    assert response.status_code == 200
    assert 'id="breathingSummary"' in response.text
    assert 'hx-swap-oob' not in response.text
    assert 'id="troopsGrid"' not in response.text
    board = client.get(f"/einsatz/{ctx.incident_id}/atemschutz").text
    assert 'id="breathingSummary" hx-swap-oob="outerHTML"' in board
    assert re.search(r'id="troopsGrid" data-server-now="[^\"]+Z"', board)


def test_board_targets_troop_for_pressure_events(client, breathing_routes):
    ctx = breathing_routes
    html = client.get(f"/einsatz/{ctx.incident_id}/atemschutz").text
    assert "+ troopId + '/karte'" in html
    assert "target: '#troop-' + troopId, swap: 'outerHTML'" in html
    assert f"'/einsatz/{ctx.incident_id}/atemschutz/uebersicht'" in html
    branch = html.split("} else if (['pressure_logged'", 1)[1].split('} else if', 1)[0]
    assert 'refreshBreathingTroop(ev.troop_id);' in branch
    assert 'doBreathingReload()' not in branch
    assert '/static/js/server_clock.js' in html
    assert 'ECPServerClock.now()' in html


@pytest.mark.parametrize("role", ["readonly", "recorder", "breathing_supervisor"])
def test_fragment_roles_match_board(client, breathing_routes, role):
    ctx = breathing_routes
    user = _user(ctx)
    ctx.db.query(UserRole).filter(UserRole.user_id == user.id).delete()
    assigned = ctx.db.query(Role).filter(Role.code == role).one()
    ctx.db.add(UserRole(user_id=user.id, role_id=assigned.id))
    ctx.db.commit()
    board = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    assert board.status_code == 200
    for url in [f"{ctx.url}/karte", f"/einsatz/{ctx.incident_id}/atemschutz/uebersicht"]:
        response = client.get(url)
        assert response.status_code == board.status_code
        if role == "readonly":
            assert 'hx-post=' not in response.text


def test_disabled_module_blocks_board_and_fragments(client, breathing_routes):
    ctx = breathing_routes
    ctx.db.get(SystemSettings, "breathing_enabled").value = "false"
    ctx.db.commit()
    for url in [f"/einsatz/{ctx.incident_id}/atemschutz", f"{ctx.url}/karte",
                f"/einsatz/{ctx.incident_id}/atemschutz/uebersicht"]:
        assert client.get(url).status_code == 404
