"""EL-Kachel: reine Zusammenfassung und gerenderte Dashboard-/Partial-Routen."""
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.models.breathing import BreathingKontrolle, BreathingTroop, TroopMember
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.services.breathing_service import AsuewSettings, dashboard_summary
from tests.test_breathing_routes import breathing_routes  # noqa: F401

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
SETTINGS = AsuewSettings(30, 60, 75, 30)


def _troop(troop_id=1, status="im_einsatz", pressure=250, **kwargs):
    return BreathingTroop(
        id=troop_id, name=f"Trupp {troop_id}", status=status, is_sicherheitstrupp=False,
        entry_at=NOW - timedelta(minutes=5), planned_duration_min=30,
        members=[TroopMember(free_text_name="Person A", start_press=300,
                             current_press=pressure, withdraw_press=60)],
        **kwargs,
    )


def test_summary_counts_all_statuses_and_only_lists_active():
    troops = [_troop(i, status) for i, status in enumerate(
        ("im_einsatz", "rueckzug", "bereit", "bereit", "zurueck", "erholt"), 1,
    )]
    troops[3].is_sicherheitstrupp = True
    result = dashboard_summary(troops, SETTINGS, NOW)
    assert result["zaehler"] == {
        "im_einsatz": 2, "bereit": 2, "sicherheitstrupp_bereit": 1, "in_erholung": 2,
    }
    assert {t["id"] for t in result["trupps"]} == {1, 2}
    assert result["hinweise"] == {"assp": False, "kein_sicherheitstrupp": False}
    assert dashboard_summary([], SETTINGS, NOW)["gesamt"] == 0


@pytest.mark.parametrize(("pressure", "expected"), [(60, "rot"), (59, "rot"), (225, "gelb"),
                                                          (226, "gruen"), (None, "unbekannt")])
def test_summary_pressure_classes_and_lowest_name(pressure, expected):
    row = dashboard_summary([_troop(pressure=pressure)], SETTINGS, NOW)["trupps"][0]
    assert row["druck_klasse"] == expected
    assert row["niedrigster_druck"] == pressure
    assert row["niedrigster_name"] == ("Person A" if pressure is not None else None)


def test_summary_uses_org_yellow_limit_and_worst_member():
    troop = _troop(pressure=200)
    assert dashboard_summary([troop], AsuewSettings(30, 60, 50, 30), NOW)["trupps"][0]["druck_klasse"] == "gruen"
    troop.members.append(TroopMember(free_text_name="Person B", start_press=300,
                                     current_press=220, withdraw_press=230))
    row = dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]
    assert row["druck_klasse"] == "rot"
    assert row["niedrigster_name"] == "Person A"
    troop.members = [TroopMember(current_press=100)]
    assert dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]["druck_klasse"] == "unbekannt"


def test_summary_sorts_by_urgency_and_keeps_both_emergency_flags():
    troops = [_troop(1), _troop(2, pressure=50), _troop(3, funk_verloren_at=NOW),
              _troop(4, notfall_at=NOW, funk_verloren_at=NOW), _troop(5, "rueckzug")]
    rows = dashboard_summary(troops, SETTINGS, NOW)["trupps"]
    assert [r["id"] for r in rows] == [4, 3, 2, 5, 1]
    assert rows[0]["notfall"] and rows[0]["funk_verloren"]
    assert "withdraw" in rows[2]["warnungen"]


def test_summary_deadlines_use_supplied_now_and_skip_completed_controls():
    troop = _troop()
    row = dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]
    assert row["laufzeit_s"] == 300
    assert row["entry_iso"].endswith("+00:00")
    assert row["naechste_frist"]["kind"] == "one_third"
    assert row["naechste_frist"]["in_s"] == 300
    assert not row["warnungen"]
    row = dashboard_summary([troop], SETTINGS, NOW + timedelta(minutes=6))["trupps"][0]
    assert row["naechste_frist"]["in_s"] == -60
    assert "one_third_due" in row["warnungen"]
    troop.kontrollen = [BreathingKontrolle(stufe="one_third", art="funk", erledigt_at=NOW)]
    row = dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]
    assert row["naechste_frist"]["kind"] == "two_third"
    troop.kontrollen.append(BreathingKontrolle(stufe="two_third", art="funk", erledigt_at=NOW))
    assert dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]["naechste_frist"]["kind"] == "max"
    overdue = dashboard_summary([troop], SETTINGS, NOW + timedelta(minutes=26))["trupps"][0]
    assert overdue["naechste_frist"]["in_s"] == -60
    assert "max_exceeded" in overdue["warnungen"]
    troop.planned_duration_min = None
    assert dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]["naechste_frist"] is None
    troop.entry_at = None
    assert dashboard_summary([troop], SETTINGS, NOW)["trupps"][0]["laufzeit_s"] == 0


def test_summary_naive_utc_and_incident_hints():
    troops = [_troop(i) for i in range(1, 4)]
    troops[0].entry_at = troops[0].entry_at.replace(tzinfo=None)
    result = dashboard_summary(troops, SETTINGS, NOW.replace(tzinfo=None))
    assert result["hinweise"] == {"assp": True, "kein_sicherheitstrupp": True}
    assert result["trupps"][0]["laufzeit_s"] == 300


def _activate(ctx):
    ctx.troop.status = "im_einsatz"
    ctx.troop.entry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=11)
    ctx.troop.planned_duration_min = 30
    for member in ctx.troop.active_members:
        member.current_press, member.withdraw_press = 210, 60
    ctx.db.commit()


@pytest.mark.parametrize("suffix", ["dashboard", "dashboard/inhalt", "atemschutz/kachel"])
def test_dashboard_tile_counters_and_troop(breathing_routes, suffix):  # noqa: F811
    ctx = breathing_routes
    _activate(ctx)
    response = ctx.client.get(f"/einsatz/{ctx.incident_id}/{suffix}")
    assert response.status_code == 200
    assert 'id="breathingKachel"' in response.text
    tile = response.text.split('id="breathingKachel"', 1)[1].split('</section>', 1)[0]
    assert "1 im Einsatz" in tile and "0 bereit" in tile
    assert "Kein Sicherheitstrupp" in tile and "0 in Erholung" in tile
    assert tile.count('class="asuew-kachel__troop"') == 1
    assert "Trupp A" in tile and "210 bar" in tile
    assert "Kontrolle 1/3 überfällig seit" in tile and "Kontrolle fällig" in tile
    assert re.search(r'data-entry="[^\"]+\+00:00"', tile)
    assert 'asuew-kachel__pressure asuew-kachel--gelb' in tile
    assert "bs-strip" not in response.text
    if suffix == "atemschutz/kachel":
        assert "<html" not in response.text


def test_dashboard_assp_and_standby_counter_row(breathing_routes):  # noqa: F811
    from app.services.breathing_service import create_troop
    ctx = breathing_routes
    _activate(ctx)
    members = [{"free_text_name": name, "start_press": 300} for name in ("Person C", "Person D")]
    for name in ("Trupp B", "Trupp C"):
        troop = create_troop(ctx.db, ctx.incident_id, name, members)
        troop.status = "rueckzug"
    standby = create_troop(ctx.db, ctx.incident_id, "Sicherheitstrupp", members, is_sicherheitstrupp=True)
    ctx.db.commit()
    response = ctx.client.get(f"/einsatz/{ctx.incident_id}/dashboard/inhalt")
    assert response.status_code == 200
    assert "Ab 3 Trupps: ASSP alarmieren" in response.text
    assert "/atemschutz/assp" not in response.text
    assert "3 im Einsatz" in response.text and "1 bereit" in response.text
    assert "Sicherheitstrupp bereit" in response.text
    for troop in ctx.db.query(BreathingTroop).filter(BreathingTroop.incident_id == ctx.incident_id):
        if troop.id != standby.id:
            troop.status = "zurueck"
    ctx.db.commit()
    response = ctx.client.get(f"/einsatz/{ctx.incident_id}/atemschutz/kachel")
    assert "0 im Einsatz" in response.text and "3 in Erholung" in response.text
    assert 'class="asuew-kachel__troop"' not in response.text
    assert "ASSP alarmieren" not in response.text


@pytest.mark.parametrize("suffix", ["dashboard", "dashboard/inhalt", "atemschutz/kachel"])
def test_dashboard_no_tile_without_troops(breathing_routes, suffix):  # noqa: F811
    ctx = breathing_routes
    response = ctx.client.get(f"/einsatz/{ctx.other_incident_id}/{suffix}")
    assert response.status_code == 200
    assert 'id="breathingKachel"' not in response.text


def test_dashboard_module_disabled_and_foreign_incident(breathing_routes):  # noqa: F811
    ctx = breathing_routes
    incident = ctx.db.get(Incident, ctx.incident_id)
    org_settings = OrgSettings(org_id=incident.primary_org_id, atemschutz_ueberwachung_modul_aktiv=False)
    ctx.db.add(org_settings)
    ctx.db.commit()
    for suffix in ("dashboard", "dashboard/inhalt"):
        response = ctx.client.get(f"/einsatz/{ctx.incident_id}/{suffix}")
        assert response.status_code == 200
        assert 'id="breathingKachel"' not in response.text
    assert ctx.client.get(f"/einsatz/{ctx.incident_id}/atemschutz/kachel").status_code == 404
    org_settings.atemschutz_ueberwachung_modul_aktiv = True
    foreign_org = FireDept(slug=f"foreign-kachel-{ctx.incident_id}", name="Andere Feuerwehr")
    ctx.db.add(foreign_org)
    ctx.db.flush()
    other = ctx.db.get(Incident, ctx.other_incident_id)
    other.primary_org_id = foreign_org.id
    ctx.db.commit()
    assert ctx.client.get(f"/einsatz/{ctx.other_incident_id}/atemschutz/kachel").status_code in (403, 404)


def test_dashboard_eager_loading_has_no_per_troop_queries(breathing_routes):  # noqa: F811
    from types import SimpleNamespace

    from sqlalchemy import event

    from app.routers.ui_incident import _dashboard_breathing_summary
    from app.services.breathing_service import create_troop

    ctx = breathing_routes
    _activate(ctx)
    incident = ctx.db.get(Incident, ctx.incident_id)
    request = SimpleNamespace(state=SimpleNamespace(breathing_module_enabled=True))
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(ctx.db.bind, "before_cursor_execute", record)
    try:
        _dashboard_breathing_summary(incident, ctx.db, request)
        count_one = len(statements)
        for i in range(4):
            troop = create_troop(ctx.db, ctx.incident_id, f"Zusatz {i}", [
                {"free_text_name": "Person C", "start_press": 300},
                {"free_text_name": "Person D", "start_press": 300},
            ])
            troop.status = "im_einsatz"
        ctx.db.commit()
        # Auch den bestehenden Einsatz vor der Messung frisch laden.
        ctx.db.refresh(incident)
        statements.clear()
        summary = _dashboard_breathing_summary(incident, ctx.db, request)
        assert summary["gesamt"] == 5
        assert len(statements) == count_one
        statements.clear()
        request.state.breathing_module_enabled = False
        assert _dashboard_breathing_summary(incident, ctx.db, request) is None
        assert statements == []
    finally:
        event.remove(ctx.db.bind, "before_cursor_execute", record)
