"""Fachliche Grenzfälle des datensparsamen Statistik-Monitors."""
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentColumn, IncidentOrg, IncidentVehicle
from app.models.master import AlarmType, FireDept, VehicleMaster
from app.services import stats_infoscreen_service as service
from app.services.stats_service import get_stats


def utc(*args):
    """Naive UTC wie in der Datenbank."""
    return datetime(*args, tzinfo=UTC).replace(tzinfo=None)


@pytest.fixture
def screen_db(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 23, 12, tzinfo=UTC)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(service, "datetime", FrozenDatetime)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        set_tenant_context(db, None)
        org = FireDept(slug="stats-screen", name="Feuerwehr Statistik", timezone="Europe/Vienna")
        other = FireDept(slug="stats-other", name="Andere Feuerwehr", timezone="Europe/Vienna")
        db.add_all([org, other])
        db.flush()
        db.add_all([AlarmType(org_id=org.id, code="F14", label="Brand", category="F"),
                    AlarmType(org_id=org.id, code="T1", label="Technisch", category="T")])
        db.flush()
        yield db, org, other
    engine.dispose()


def add_incident(db, org, started, **kwargs):
    incident = Incident(primary_org_id=org.id, started_at=started, alarm_type_code="T1", **kwargs)
    db.add(incident)
    db.flush()
    return incident


def test_screen_calendar_coverage_timezone_and_scope(screen_db):
    db, org, other = screen_db
    # 1. April lokal, Mittwoch, 00–06 Uhr (DB UTC).
    add_incident(db, org, utc(2026, 3, 31, 22, 30))
    fire = add_incident(db, org, utc(2026, 10, 23, 10))
    fire.alarm_type_code = "F14"
    add_incident(db, org, utc(2026, 10, 23, 11))
    add_incident(db, org, utc(2026, 10, 23, 12, 30))  # Noch zukünftig.
    add_incident(db, org, utc(2026, 2, 1), is_exercise=True)
    add_incident(db, org, utc(2025, 12, 20))
    add_incident(db, other, utc(2026, 9, 1))
    collab = add_incident(db, other, utc(2026, 10, 22))
    db.add(IncidentOrg(incident_id=collab.id, org_id=org.id))
    db.flush()
    data = service.get_infoscreen_data(db, org)
    assert data["kpis"]["total"] == 4
    assert data["kpis"]["fire"] == 1
    assert data["kpis"]["technical"] == 3
    assert data["kpis"]["current_month"] == 3
    assert data["kpis"]["monthly_average"] == .6  # 4 / 7 Monate, inkl. Oktober.
    assert data["observed_months"] == 7
    assert data["coverage_from"] == "2026-04-01"
    assert len(data["months"]) == 12
    assert data["months"][2]["count"] is None
    assert data["months"][3]["technical"] == 1
    assert data["months"][4]["count"] == 0
    assert data["months"][9]["current"] is True
    assert data["months"][10]["future"] is True
    assert data["months"][10]["count"] is None
    assert data["time_of_day"][0]["count"] == 2
    assert data["weekdays"][2]["count"] == 1
    assert sum(row["count"] for row in data["weekdays"]) == 4
    assert data["alarm_types"][0]["code"] == "T1"


def test_screen_valid_times_medians_boundaries_and_auto_close(screen_db):
    db, org, _ = screen_db
    started = utc(2026, 10, 1, 8)
    for minutes in (0, 29, 30, 59, 60, 120, 121):
        add_incident(db, org, started, ready_again_at=started + timedelta(minutes=minutes),
                     closed_at=started + timedelta(days=5), closed_via_lis_auto=True)
    # Fehlend, umgekehrt, automatisch oder noch zukünftig: kein Ersatz durch Null.
    add_incident(db, org, started)
    add_incident(db, org, started, closed_at=started - timedelta(minutes=1))
    add_incident(db, org, started, closed_at=started + timedelta(days=2), closed_via_lis_auto=True)
    add_incident(db, org, started, closed_at=started + timedelta(days=3), autoclose_warn_sent_at=started)
    add_incident(db, org, started, closed_at=utc(2026, 12, 1))
    # Fachlicher manueller Abschluss bleibt gültig; Ankunft nach Ende nicht.
    add_incident(db, org, started, closed_at=started + timedelta(minutes=40),
                 on_scene_at=started + timedelta(minutes=45))
    for minutes in (5, 15):
        add_incident(db, org, started, on_scene_at=started + timedelta(minutes=minutes))
    add_incident(db, org, started, on_scene_at=started - timedelta(minutes=1))
    add_incident(db, org, started, departed_at=started + timedelta(minutes=20),
                 on_scene_at=started + timedelta(minutes=10))
    add_incident(db, org, started, on_scene_at=utc(2026, 12, 1))
    data = service.get_infoscreen_data(db, org)
    assert data["samples"] == {"duration": 8, "arrival": 2}
    assert data["kpis"]["duration"] == 49.5
    assert data["kpis"]["arrival"] == 10
    assert [row["count"] for row in data["durations"]] == [2, 3, 2, 1]


def test_screen_empty_data_has_no_invented_averages(screen_db):
    db, org, _ = screen_db
    data = service.get_infoscreen_data(db, org)
    assert data["kpis"] == {"total": 0, "fire": 0, "technical": 0, "current_month": None,
                            "monthly_average": None, "duration": None, "arrival": None}
    assert all(row["count"] is None for row in data["months"] + data["durations"] + data["time_of_day"])


def test_fleet_counts_distinct_incidents_across_aliases_and_reassignments(screen_db):
    db, org, other = screen_db
    incident = add_incident(db, org, utc(2026, 10, 1, 8))
    second = add_incident(db, org, utc(2026, 10, 2, 8))
    column = IncidentColumn(incident_id=incident.id, code="active", title="Aktiv", column_kind="vehicles")
    own = VehicleMaster(dept_id=org.id, code="RLF-A", name="RLF")
    alias = VehicleMaster(dept_id=org.id, code=" rlf-a ", name="Alias", is_external=True)
    foreign = VehicleMaster(dept_id=other.id, code="RLF-A", name="Fremd")
    external = VehicleMaster(dept_id=org.id, code="DLK", name="Nachbar", is_external=True)
    db.add_all([column, own, alias, foreign, external])
    db.flush()
    for vehicle, target, removed in [(own, incident, None), (own, incident, None), (alias, incident, None),
                                     (alias, second, None), (foreign, incident, None),
                                     (own, second, utc(2026, 10, 3)), (external, incident, None)]:
        db.add(IncidentVehicle(incident_id=target.id, column_id=column.id, vehicle_master_id=vehicle.id,
                               removed_at=removed))
    db.flush()
    stats = get_stats(db, org.id, date(2026, 1, 1), date(2026, 10, 23))
    assert stats.vehicle_fleet_stats[0]["anzahl_einsaetze"] == 2
    assert service.get_infoscreen_data(db, org)["fleet"] == [{"code": "RLF-A", "count": 2}]


def test_screen_map_omits_details_and_rounds_locations(screen_db):
    db, org, _ = screen_db
    add_incident(db, org, utc(2026, 10, 1), lat=47.467321, lng=9.753214,
                 address_street="Geheimstraße", address_no="42", report_text="Vertraulich")
    add_incident(db, org, utc(2026, 10, 2), lat=0, lng=0)
    data = service.get_infoscreen_data(db, org)
    assert data["map"] == [{"lat": 47.47, "lng": 9.75, "category": "T"}]
    assert data["map_center"] == [47.466, 9.752]
    assert "Geheimstraße" not in str(data)
    assert "Vertraulich" not in str(data)


def test_screen_browser_fullhd_4k_rotation_and_refresh(screen_db, tmp_path):
    """Echte vendorte Charts/Leaflet; kein Server, keine externen Kachelanfragen."""
    import json
    import os
    from pathlib import Path
    from urllib.parse import urlsplit

    playwright = pytest.importorskip("playwright.sync_api")
    from app.core.templating import templates

    db, org, _ = screen_db
    for month in range(4, 11):
        for index in range(month):
            incident = add_incident(db, org, utc(2026, month, index + 1, 8),
                                    lat=47.466 + index * .001, lng=9.752 + index * .001,
                                    ready_again_at=utc(2026, month, index + 1, 9))
            incident.alarm_type_code = "F14" if index % 3 == 0 else "T1"
    add_incident(db, org, utc(2026, 10, 1), lat=48.2, lng=16.3)
    data = service.get_infoscreen_data(db, org)
    data["fleet"] = [{"code": code, "count": 18 - index * 2}
                     for index, code in enumerate(["RLF-A", "KDOF", "TMB 27", "LFB-C", "MTF-2", "VFA"])]
    html = templates.env.get_template("infoscreen/statistik.html").render(
        org=org, data=data, ASSET_VERSION="test", brand="Einsatzcockpit",
    )
    state = {"status": 503, "requests": 0, "body": None}
    static = Path("app/static").resolve()

    def handle(route):
        path = urlsplit(route.request.url).path
        if path == "/infoscreen/statistik/test/daten":
            state["requests"] += 1
            route.fulfill(status=state["status"], content_type="application/json",
                          body=state["body"] or json.dumps(data))
        elif path.startswith("/static/"):
            asset = (static / path.removeprefix("/static/")).resolve()
            if asset.is_relative_to(static) and asset.is_file():
                route.fulfill(path=str(asset))
            else:
                route.fulfill(status=404)
        elif path.startswith("/karten/"):
            route.fulfill(content_type="image/svg+xml", body=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
                '<rect width="256" height="256" fill="#d8e3d0"/></svg>'
            ))
        else:
            route.fulfill(content_type="text/html", body=html)

    with playwright.sync_playwright() as runner:
        try:
            browser = runner.chromium.launch(executable_path=os.environ.get("ECP_PLAYWRIGHT_EXECUTABLE"))
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Playwright Chromium nicht installiert")
            raise
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.emulate_media(reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("http://ecp.test/**", handle)
        page.clock.install(time=datetime(2026, 10, 23, 12, tzinfo=UTC))
        page.goto("http://ecp.test/infoscreen/statistik/test")
        page.wait_for_function("Object.keys(Chart.instances).length === 5")
        page.evaluate("document.fonts.ready")
        assert not errors
        assert page.locator(".stats-cluster").count() > 0
        assert "außerhalb" in page.locator("#map-note").inner_text()
        original = page.locator('[data-kpi="total"]').inner_text()
        page.screenshot(path=str(tmp_path / "statistik-fullhd.png"))
        for width, height in [(1920, 1080), (3840, 2160), (1280, 720)]:
            page.set_viewport_size({"width": width, "height": height})
            page.clock.run_for(100)
            page.wait_for_function("document.documentElement.scrollWidth <= innerWidth && "
                                   "document.documentElement.scrollHeight <= innerHeight")
            dimensions = page.evaluate("""({width: document.documentElement.scrollWidth,
                height: document.documentElement.scrollHeight, innerWidth, innerHeight})""")
            assert dimensions["width"] <= dimensions["innerWidth"], dimensions
            assert dimensions["height"] <= dimensions["innerHeight"], dimensions
            overflow = page.evaluate("""() => Array.from(document.querySelectorAll('.stats-panel, .stats-kpi'))
                .filter(el => el.scrollHeight > el.clientHeight + 1)
                .map(el => ({title: el.innerText.slice(0, 80), height: el.clientHeight, scroll: el.scrollHeight}))""")
            assert not overflow, overflow
            assert page.evaluate("""() => Object.values(Chart.instances).every(chart =>
                Math.abs(chart.width - chart.canvas.parentElement.clientWidth) <= 1 &&
                Math.abs(chart.height - chart.canvas.parentElement.clientHeight) <= 1)""")
        page.set_viewport_size({"width": 3840, "height": 2160})
        page.clock.fast_forward(31000)
        page.clock.run_for(700)
        assert page.locator("#stats-development").get_attribute("aria-hidden") == "false"
        page.screenshot(path=str(tmp_path / "statistik-4k-entwicklung.png"))
        assert page.evaluate("Chart.getChart('stats-month-chart').options.devicePixelRatio") == 2
        page.locator("#rotation-toggle").click()
        page.clock.fast_forward(31000)
        assert page.locator("#stats-development").get_attribute("aria-hidden") == "false"
        page.clock.fast_forward(300000)
        page.wait_for_function("document.getElementById('refresh-status').classList.contains('is-stale')")
        assert page.locator('[data-kpi="total"]').inner_text() == original
        assert "Datenstand" in page.locator("#last-update").inner_text()
        state["status"] = 200
        state["body"] = "{}"  # Erfolgreiches HTTP, aber ungültiger Inhalt.
        page.clock.fast_forward(61000)
        page.wait_for_timeout(50)
        assert page.locator('[data-kpi="total"]').inner_text() == original
        assert page.locator("#refresh-status").get_attribute("class") == "stats-status-dot is-stale"
        state["body"] = None
        data["kpis"]["total"] += 1
        page.clock.fast_forward(61000)
        page.wait_for_function("!document.getElementById('refresh-status').classList.contains('is-stale')")
        assert page.locator('[data-kpi="total"]').inner_text() != original
        assert len(page.evaluate("Object.keys(Chart.instances)")) == 5
        assert not errors
        state["status"] = 401
        page.clock.fast_forward(301000)
        page.wait_for_function("document.getElementById('refresh-label').textContent.includes('Zugriff')")
        attempts = state["requests"]
        page.clock.fast_forward(601000)
        assert state["requests"] == attempts
        assert page.locator("#rotation-toggle").get_attribute("aria-pressed") == "false"
        # Abschaltbarkeit per URL und gespeicherte Monitorpräferenz.
        page.goto("http://ecp.test/infoscreen/statistik/test?rotate=0&interval=60")
        page.wait_for_function("Object.keys(Chart.instances).length === 5")
        page.clock.fast_forward(61000)
        assert page.locator("#stats-overview").get_attribute("aria-hidden") == "false"
        assert page.locator("#rotation-toggle").get_attribute("aria-pressed") == "false"
        browser.close()
