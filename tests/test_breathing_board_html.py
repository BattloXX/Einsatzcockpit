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


def test_board_loads_network_helpers_and_connection_banner(client, breathing_routes):
    ctx = breathing_routes

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert '<script src="/static/js/breathing_net.js?v=' in response.text
    assert "breathing-conn" in response.text
    assert "ECPBreathingNet.send(" in response.text
    assert "requestSubmit(); open = false" not in response.text


@pytest.mark.parametrize(("minutes", "stufe", "label"), [
    (15, "one_third", "1/3"), (25, "two_third", "2/3"),
])
def test_due_control_requires_pressure_or_no_contact(client, breathing_routes, minutes, stufe, label):
    from datetime import UTC, datetime, timedelta

    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    ctx.troop.entry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)
    ctx.troop.planned_duration_min = 30
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert f"Kontrolle fällig ({label}): niedrigsten Druck des Trupps abfragen" in response.text
    forms = re.findall(r'<form\b[^>]*hx-post="' + re.escape(ctx.url) + r'/kontrolle".*?</form>', response.text, re.S)
    due_forms = [form for form in forms if f'name="stufe" value="{stufe}"' in form]
    assert len(due_forms) == 2
    assert 'name="art" value="funk"' in due_forms[0]
    assert 'name="art" value="kein_kontakt"' in due_forms[1]
    for form in due_forms:
        assert 'name="_csrf"' in form
        assert 'hx-swap="none"' in form
        assert "ECPBreathingNet.describeFailure" in form
    assert re.findall(r'name="pressure_bar" value="(\d+)"', due_forms[0]) == [str(v) for v in range(300, 0, -10)]
    assert not re.search(r"ackWarning\([^\n]*'(one_third|two_third)'", response.text)
    assert 'name="stufe" value="frei"' in response.text
    assert "Kontrolle (frei)" in response.text


@pytest.mark.parametrize(("field", "banner", "route"), [
    ("notfall_at", "ATEMSCHUTZNOTFALL seit", "notfall/aufheben"),
    ("funk_verloren_at", "Kein Funkkontakt seit", "funkverlust"),
])
def test_persistent_safety_banner(client, breathing_routes, field, banner, route):
    from datetime import UTC, datetime

    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    setattr(ctx.troop, field, datetime.now(UTC).replace(tzinfo=None))
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert response.text.count(banner) == 1
    assert f'hx-post="{ctx.url}/{route}"' in response.text
    if field == "notfall_at":
        assert re.search(r'class="troop-card [^"]*troop-card--notfall"', response.text)
        assert "Atemschutznotfall wirklich melden?" in response.text
        assert 'name="bestaetigt" value="1"' in response.text
    else:
        assert "Funkkontakt wieder da" in response.text
        assert 'name="aktiv" value="0"' in response.text
        assert 'name="aktiv" value="1"' in response.text


def test_new_troop_dialog_uses_org_duration_and_supervision_fields(client, breathing_routes):
    ctx = breathing_routes

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    dialog = response.text.split('id="newTroopModal"', 1)[1].split('</dialog>', 1)[0]
    for field in ("ueberwacher_name", "angriffsweg", "funkkanal"):
        assert f'name="{field}"' in dialog
    from html import escape

    from tests.test_breathing_routes_phase1 import _user

    user = _user(ctx)
    assert f'value="{escape(user.full_name or user.username, quote=True)}"' in dialog
    assert "Flasche (Anzeige)" in dialog
    assert "Einsatzzeit 30 Min. (Einstellung der Organisation), Kontrollen nach 10 und 20 Min." in dialog
    options = dialog.split('name="bottle_preset"', 1)[1].split('</select>', 1)[0]
    assert not re.search(r'\d+ min', options)


def test_member_pressure_uses_configured_yellow_threshold(client, breathing_routes):
    from tests.test_breathing_routes_phase1 import _settings

    ctx = breathing_routes
    _settings(ctx, asuew_gelb_prozent=80)

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert re.search(r'memberPressRow\(\d+, 300.0, [\d.]+, 300.0, 80\)', response.text)
    assert "this.startPressAvg * this.gelbProzent / 100" in response.text


@pytest.mark.parametrize("status", ["erholt", "zurueck"])
@pytest.mark.parametrize("recovery_min", [0, 30])
def test_recovery_countdown_for_returned_troop(client, breathing_routes, status, recovery_min):
    from datetime import UTC, datetime

    from tests.test_breathing_routes_phase1 import _settings

    ctx = breathing_routes
    ctx.troop.status = status
    ctx.troop.back_at = datetime.now(UTC).replace(tzinfo=None)
    ctx.db.commit()
    _settings(ctx, asuew_erholzeit_min=recovery_min)

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert ('x-text="recoveryDisplay">Erholzeit</div>' in response.text) is (recovery_min > 0)


def test_control_summary_shows_three_latest_active_controls(client, breathing_routes):
    from datetime import UTC, datetime, timedelta

    from app.models.breathing import BreathingKontrolle

    ctx = breathing_routes
    now = datetime.now(UTC).replace(tzinfo=None)
    for index, (stufe, art, pressure) in enumerate([
        ("frei", "funk", 290), ("one_third", "funk", 190),
        ("two_third", "kein_kontakt", None), ("frei", "funk", 150),
    ]):
        ctx.db.add(BreathingKontrolle(
            troop_id=ctx.troop.id, stufe=stufe, art=art, niedrigster_druck=pressure,
            erledigt_at=now + timedelta(seconds=index),
        ))
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert "Kontrolle 1/3 (Funk): 190 bar," in response.text
    assert "Kontrolle 2/3: kein Funkkontakt," in response.text
    assert "Kontrolle (Funk): 150 bar," in response.text
    assert "Kontrolle (Funk): 290 bar," not in response.text
