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
    assert f"Kontrolle fällig ({label})" in response.text
    warning_row = response.text.split('class="warning-actions"', 1)[1].split('</div>\n        </div>', 1)[0]
    assert ">Druck abgefragt</button>" in warning_row
    assert ">Kein Funkkontakt</button>" in warning_row
    forms = re.findall(r'<form\b[^>]*hx-post="' + re.escape(ctx.url) + r'/kontrolle".*?</form>', response.text, re.S)
    due_forms = [form for form in forms if f'name="stufe" value="{stufe}"' in form]
    assert len(due_forms) == 2
    assert 'name="art" value="funk"' in due_forms[0]
    assert 'name="art" value="kein_kontakt"' in due_forms[1]
    for form in due_forms:
        assert 'name="_csrf"' in form
        assert 'hx-swap="none"' in form
        assert "ECPBreathingNet.formFailed($event)" in form
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


def _board_summary(html):
    return html.split('<div id="breathingSummary"', 1)[1].split('<div class="troops-grid"', 1)[0]


def _add_board_troop(ctx, name, status="bereit", safety=False):
    from app.models.breathing import BreathingTroop

    troop = BreathingTroop(
        incident_id=ctx.incident_id, name=name, status=status, is_sicherheitstrupp=safety,
    )
    ctx.db.add(troop)
    ctx.db.flush()
    return troop


def test_three_deployed_troops_show_assp_hint(client, breathing_routes):
    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    _add_board_troop(ctx, "Trupp B", "im_einsatz")
    third = _add_board_troop(ctx, "Trupp C", "rueckzug")
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    summary = _board_summary(response.text)
    assert "Kritische Hinweise" in summary
    assert "Atemschutzsammelplatz (ASSP)" in summary
    assert "die Überwachung bleibt beim Gruppenkommandanten" in summary
    assert 'hx-swap-oob="outerHTML"' in summary
    assert response.text.index('id="breathingSummary"') < response.text.index('id="troopsGrid"')

    third.status = "zurueck"
    ctx.db.commit()
    assert "Atemschutzsammelplatz (ASSP) über die Einsatzleitung" not in _board_summary(
        client.get(f"/einsatz/{ctx.incident_id}/atemschutz").text,
    )


@pytest.mark.parametrize("safety_status", [None, "bereit", "im_einsatz", "erholt"])
def test_missing_ready_safety_troop_hint(client, breathing_routes, safety_status):
    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    if safety_status:
        _add_board_troop(ctx, "Rettungstrupp", safety_status, safety=True)
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    summary = _board_summary(response.text)
    assert ("Kein bereiter Sicherheitstrupp" in summary) is (safety_status != "bereit")
    assert ("Kritische Hinweise" in summary) is (safety_status != "bereit")


def test_summary_links_to_emergency_and_radio_loss(client, breathing_routes):
    from datetime import UTC, datetime

    ctx = breathing_routes
    ctx.troop.notfall_at = datetime.now(UTC).replace(tzinfo=None)
    radio = _add_board_troop(ctx, "Funktrupp")
    radio.funk_verloren_at = ctx.troop.notfall_at
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    summary = _board_summary(response.text)
    assert f'href="#troop-{ctx.troop.id}">ATEMSCHUTZNOTFALL: Trupp A</a>' in summary
    assert f'href="#troop-{radio.id}">Kein Funkkontakt: Funktrupp</a>' in summary
    assert 'class="breathing-critical__notfall"' in summary


@pytest.mark.parametrize("closed", [False, True])
def test_notification_table_only_for_open_warnings(client, breathing_routes, closed):
    from datetime import UTC, datetime

    from app.models.breathing import BreathingWarnung, BreathingZustellung

    ctx = breathing_routes
    now = datetime.now(UTC).replace(tzinfo=None)
    warning = BreathingWarnung(
        incident_id=ctx.incident_id, troop_id=ctx.troop.id, kind="withdraw",
        ausgeloest_at=now, erledigt_at=now if closed else None,
    )
    ctx.db.add(warning)
    ctx.db.flush()
    for index, (channel, status, error) in enumerate([
        ("push", "sent", None), ("sms", "pending", None),
        ("sms", "failed", "Versand fehlgeschlagen"), ("sms", "suppressed", "Übung"),
    ]):
        ctx.db.add(BreathingZustellung(
            warnung_id=warning.id, stufe=index, kanal=channel,
            empfaenger_label=f"Empfänger {index}", status=status, fehler=error,
            gesendet_at=now if status == "sent" else None,
        ))
    foreign = _add_board_troop(ctx, "Anderer Einsatz")
    foreign.incident_id = ctx.other_incident_id
    ctx.db.add(BreathingWarnung(
        incident_id=ctx.other_incident_id, troop_id=foreign.id,
        kind="notfall", ausgeloest_at=now,
    ))
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert ('<summary>Benachrichtigungen</summary>' in response.text) is not closed
    if not closed:
        table = response.text.split('aria-label="Zustellungsprotokoll"', 1)[1].split('</table>', 1)[0]
        for value in ["Rückzugsdruck", "Ausgelöst um", "Stufe", "Kanal", "Sendezeit",
                      "Push", "SMS", "gesendet", "wartend", "fehlgeschlagen", "unterdrückt: Übung"]:
            assert value in table
        for index in range(4):
            assert f"Empfänger {index}" in table
        assert ">Notfall<" not in table


def test_without_warnings_has_no_notifications(client, breathing_routes):
    response = client.get(f"/einsatz/{breathing_routes.incident_id}/atemschutz")

    assert response.status_code == 200
    assert '<summary>Benachrichtigungen</summary>' not in response.text


@pytest.mark.parametrize("with_safety", [False, True])
def test_radio_loss_help_checklist_and_safety_link(client, breathing_routes, with_safety):
    from datetime import UTC, datetime

    ctx = breathing_routes
    ctx.troop.funk_verloren_at = datetime.now(UTC).replace(tzinfo=None)
    if with_safety:
        first = _add_board_troop(ctx, "Sicherheit 1", safety=True)
        second = _add_board_troop(ctx, "Sicherheit 2", safety=True)
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    banner = response.text.split('class="troop-safety-banner troop-safety-banner--funk"', 1)[1].split('</details>', 1)[0]
    assert "Vorgehen bei fehlendem Funkkontakt" in banner
    for item in ["Gruppenkommandant des Trupps verständigt", "Trupp im Innenangriff muss den Rückzug antreten",
                 "Rettungstrupp bzw. Sicherheitstrupp einsetzen (Entscheidung GRKDT)",
                 "Überwacher und Einsatzleitung informiert"]:
        assert item in banner
    assert banner.count('type="checkbox" x-model="checked"') == 4
    assert 'x-data="{checked: []}"' in banner
    assert ("Zum Sicherheitstrupp" in banner) is with_safety
    if with_safety:
        assert f'href="#troop-{first.id}"' in banner
        assert f'href="#troop-{second.id}"' not in banner


def test_overview_counts_all_statuses(client, breathing_routes):
    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    for name, status, safety in [
        ("Rückzug", "rueckzug", False), ("Bereit", "bereit", False),
        ("Sicherheit", "bereit", True), ("Erholung 1", "zurueck", False),
        ("Erholung 2", "zurueck", False), ("Erholung 3", "zurueck", False),
        ("Erholt", "erholt", False),
    ]:
        _add_board_troop(ctx, name, status, safety)
    ctx.db.commit()

    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    for chip in ["2 im Einsatz", "2 bereit", "1 Sicherheitstrupp bereit", "3 in Erholung"]:
        assert f"<span>{chip}</span>" in _board_summary(response.text)


def test_warning_event_refreshes_card_for_all_kinds(client, breathing_routes):
    response = client.get(f"/einsatz/{breathing_routes.incident_id}/atemschutz")

    assert response.status_code == 200
    handler = response.text.split("window.addEventListener('breathing-warning',", 1)[1].split('});', 1)[0]
    assert "_playWarning(kind, troopId);" in handler
    assert "if (document.getElementById('troopsGrid')) refreshBreathingTroop(troopId);" in handler
    assert "if (!src) return;" in response.text


@pytest.mark.parametrize("status", ["bereit", "im_einsatz", "rueckzug", "zurueck", "erholt"])
@pytest.mark.parametrize("mode", ["off", "missing", "valid"])
def test_app_readonly_board(client, breathing_routes, status, mode):
    from datetime import UTC, datetime, timedelta

    from tests.test_breathing_routes_phase1 import _settings
    from tests.test_breathing_routes_phase3 import _session

    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=mode != "off")
    ctx.troop.status = status
    ctx.troop.entry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    ctx.db.commit()
    if mode == "valid":
        _session(ctx, client)
    page = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    assert page.status_code == 200
    readonly = mode == "missing"
    assert page.context["asuew_readonly"] is readonly
    assert ("Nur-Lese-Ansicht:" in page.text) is readonly
    assert f"const ASUEW_READONLY = {'true' if readonly else 'false'};" in page.text
    if readonly:
        assert 'href="/app"' in page.text
        posts = re.findall(r'hx-post="([^"]+)"', page.text)
        assert all(url.endswith(("/notfall", "/funkverlust")) for url in posts)
        assert len(posts) == (2 if status in ("im_einsatz", "rueckzug") else 0)
        for text in ["ackWarning(", "Trupp anlegen", 'id="newTroopModal"',
                     "requestSubmit", 'id="redeployTroopModal-']:
            assert text not in page.text
        assert "if (ASUEW_READONLY) return;" in page.text
    else:
        assert "Trupp anlegen" in page.text
        assert 'id="newTroopModal"' in page.text
        assert 'hx-post=' in page.text


@pytest.mark.parametrize("pflicht,age,status,expected", [
    (True, None, "im_einsatz", True), (True, 61, "rueckzug", True),
    (True, 0, "im_einsatz", False), (False, None, "im_einsatz", False),
    (True, None, "bereit", False),
])
def test_board_heartbeat_hint(client, breathing_routes, pflicht, age, status, expected):
    from datetime import UTC, datetime, timedelta

    from tests.test_breathing_routes_phase1 import _settings
    from tests.test_breathing_routes_phase3 import _session

    ctx = breathing_routes
    _settings(ctx, asuew_app_pflicht=pflicht)
    ctx.troop.status = status
    ctx.db.commit()
    if age is not None:
        _session(ctx, client, letzter_heartbeat_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=age))
    page = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    assert page.status_code == 200
    assert page.context["kein_heartbeat"] is expected
    assert ("Kein Überwachungsgerät in der App aktiv (kein Heartbeat seit über 60 Sekunden)." in page.text) is expected
    if expected:
        assert "Überwachung in der Einsatzcockpit-App öffnen oder Funkkontrolle der Trupps durchführen." in page.text


def test_board_offline_outbox_and_pressure_correction(client, breathing_routes):
    ctx = breathing_routes
    ctx.troop.status = "im_einsatz"
    ctx.db.commit()
    response = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")

    assert response.status_code == 200
    assert '<script src="/static/js/breathing_outbox.js?v=' in response.text
    assert 'id="breathing-outbox" role="status"' in response.text
    pressure_forms = re.findall(r'<form\b[^>]*hx-post="' + re.escape(ctx.url) + r'/druck".*?</form>', response.text, re.S)
    assert pressure_forms
    for form in pressure_forms:
        assert 'name="korrektur_grund"' in form
        assert "Korrektur (Wert war falsch)" in form
        assert '@breathing-queued="open = false;' in form
    for form in re.findall(r'<form\b.*?</form>', response.text, re.S):
        assert "ECPBreathingNet.showError(" not in form
    assert "ECPBreathingNet.formFailed($event)" in response.text


@pytest.mark.parametrize("status,label", [
    ("bereit", "Einsetzen"), ("im_einsatz", "Rückzug"), ("rueckzug", "Zurück"),
    ("zurueck", "Erholt"), ("erholt", "Erneut einsetzen"),
])
def test_redesigned_card_primary_and_pressure_bars(client, breathing_routes, status, label):
    from datetime import UTC, datetime, timedelta

    ctx = breathing_routes
    ctx.troop.status = status
    ctx.troop.entry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=11)
    ctx.troop.planned_duration_min = 30
    for index, member in enumerate(ctx.troop.active_members):
        member.current_press = 220 - index * 10
        member.withdraw_press = 60
    ctx.db.commit()
    page = client.get(f"/einsatz/{ctx.incident_id}/atemschutz")
    card = client.get(f"{ctx.url}/karte")
    assert page.status_code == card.status_code == 200
    assert '<link rel="stylesheet" href="/static/css/breathing.css?v=' in page.text
    for html in [page.text, card.text]:
        primary = html.split('class="troop-card__actions troop-primary-actions"', 1)[1].split('</div>', 1)[0]
        assert f" {label}</button>" in primary
        assert primary.count('<button') == 1
        assert 'gauge-svg' not in html
        assert 'needleAngle' not in html
        assert html.count('class="member-withdraw-marker"') == len(ctx.troop.active_members)
        assert html.count('class="member-pressure-track"') == len(ctx.troop.active_members)
        assert 'class="member-lowest">niedrigster</span>' in html
        timeline = html.split('aria-label="Fristen"', 1)[1].split('</div>', 1)[0]
        for event in ["Eintritt", "1/3", "2/3", "Max", "fällig", "offen"]:
            assert event in timeline
        if status == "erholt":
            assert html.index(' Erneut einsetzen</button>') < html.index('<details class="recovered-details">')
