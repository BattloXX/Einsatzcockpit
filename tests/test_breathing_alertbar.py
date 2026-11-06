"""Paket 3: Alarmleiste, Outdoor-Modus und reduzierte Bereitschaftskarten."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_breathing_routes import breathing_routes  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_no_alert_bar_without_warnings(client, breathing_routes):
    ctx = breathing_routes
    assert 'id="breathingAlertBar"' not in client.get(f"/einsatz/{ctx.incident_id}/atemschutz/uebersicht").text


def test_emergency_precedes_due_control_in_summary_route(client, breathing_routes):
    ctx = breathing_routes
    now = datetime.now(UTC).replace(tzinfo=None)
    ctx.troop.status = "im_einsatz"
    ctx.troop.is_sicherheitstrupp = True
    ctx.troop.entry_at = now - timedelta(minutes=11)
    ctx.troop.planned_duration_min = 30
    ctx.troop.notfall_at = now
    ctx.db.commit()
    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz/uebersicht")
    assert response.status_code == 200
    assert 'id="breathingAlertBar"' in response.text
    assert '<strong data-alert-text>Trupp A: ATEMSCHUTZNOTFALL</strong>' in response.text
    assert '+1 weitere' in response.text
    data = json.loads(re.search(r"data-alerts='([^']+)'", response.text)[1])
    assert [alert['rank'] for alert in data] == [0, 4]
    assert 'role="alert"' not in response.text  # JS meldet nur neue Eskalationen.
    assert 'data-alert-jump aria-label=' in response.text
    assert 'data-alert-next aria-label=' in response.text


def test_board_has_contrast_control_and_grid_styles(client, breathing_routes):
    html = client.get(f"/einsatz/{breathing_routes.incident_id}/atemschutz").text
    assert 'id="breathingContrastToggle"' in html
    assert 'Hochkontrast' in html
    assert 'ECPBreathingContrast.restore' in html
    assert html.index('ECPBreathingContrast.restore') < html.index('id="troopsGrid"')
    js = (ROOT / 'app/static/js/breathing_contrast.js').read_text()
    assert 'ec-breathing-hc' in js
    css = (ROOT / 'app/static/css/breathing.css').read_text()
    assert '.breathing-hc' in css
    assert 'minmax(340px' in css


@pytest.mark.parametrize('status', ['bereit', 'im_einsatz'])
@pytest.mark.parametrize('unequal', [False, True])
def test_card_pressure_polish(client, breathing_routes, status, unequal):
    ctx = breathing_routes
    ctx.troop.status = status
    for index, member in enumerate(ctx.troop.active_members):
        member.current_press = 300 - (index * 20 if unequal else 0)
        member.withdraw_press = None
    ctx.db.commit()
    html = client.get(f'{ctx.url}/karte').text
    assert ('>niedrigster</span>' in html) is (status == 'im_einsatz' and unequal)
    if status == 'bereit':
        assert 'member-druck-withdraw' not in html
        assert 'member-withdraw-marker' not in html
        assert 'troop-pressure-age' not in html
    else:
        assert 'Noch keine Druckmeldung' in html
        assert 'Rückzug: noch offen' in html
        assert 'class="btn btn--druck press-trigger"' in html
        assert 'class="btn btn--primary press-trigger"' not in html
        assert 'class="btn btn--danger press-trigger"' not in html
