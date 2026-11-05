"""HTML-Regressionen fuer Board und Bildschirmschoner mit echtem Login."""

import re

import pytest

from app.models.breathing import TROOP_STATUS_LABELS
from tests.test_breathing_routes import breathing_routes  # noqa: F401


@pytest.mark.parametrize("status", ["bereit", "im_einsatz", "rueckzug"])
def test_live_troop_disables_auto_screensaver(client, breathing_routes, status):
    ctx = breathing_routes
    ctx.troop.status = status
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert "/screensaver" not in response.text
    assert "var TIMEOUT_MS = 3 * 60 * 1000;" not in response.text
    assert TROOP_STATUS_LABELS[status] in response.text


@pytest.mark.parametrize("with_recovered_troop", [False, True])
def test_without_live_troops_enables_auto_screensaver(client, breathing_routes, with_recovered_troop):
    ctx = breathing_routes
    if with_recovered_troop:
        ctx.troop.status = "erholt"
    else:
        ctx.troop.incident_id = ctx.other_incident_id
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert f"/einsatz/{ctx.incident_id}/screensaver" in response.text
    assert "var TIMEOUT_MS = 3 * 60 * 1000;" in response.text


def test_screensaver_returns_to_board_on_troop_events(client, breathing_routes):
    ctx = breathing_routes

    response = client.get(f"/einsatz/{ctx.incident_id}/screensaver")

    assert response.status_code == 200
    assert re.search(
        r"else if \(msg.type === 'troop_warning' \|\| msg.type === 'troop_started'"
        r"\s*\|\| msg.type === 'troop_created' \|\| msg.type === 'troop_status_changed'\)"
        r"\s*\{\s*location.href = '/einsatz/" + str(ctx.incident_id) + r"/atemschutz';\s*\}",
        response.text,
    )
    assert "if (msg.type === 'incident_closed') location.href = '/';" in response.text
    assert "else if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'pong' }));" in response.text


@pytest.mark.parametrize(("error", "message"), [
    ("invalid_input", "Ungültige Eingabe: Bitte Druckwerte (0 bis 330 bar) und Namen prüfen."),
    ("min_two_members", "Es müssen mindestens 2 Truppmitglieder ausgefüllt sein."),
])
def test_creation_error_shows_alert_and_opens_dialog(client, breathing_routes, error, message):
    ctx = breathing_routes

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz?error={error}")

    assert response.status_code == 200
    assert re.search(r'<div class="alert alert--error"[^>]*>\s*' + re.escape(message), response.text)
    assert "document.getElementById('newTroopModal').showModal();" in response.text


@pytest.mark.parametrize(("status", "has_pressure_form"), [
    ("bereit", False), ("im_einsatz", True), ("rueckzug", True),
])
def test_pressure_picker_only_for_deployed_troop(client, breathing_routes, status, has_pressure_form):
    ctx = breathing_routes
    ctx.troop.status = status
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert (f'<form hx-post="{ctx.url}/druck"' in response.text) is has_pressure_form
    assert "document.getElementById('newTroopModal').showModal();" not in response.text
