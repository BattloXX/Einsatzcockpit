"""S5: Rechte, sichere URL-Aufloesung, Audit, Resolver und Retention."""

import json
import os
import re
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.crypto import encrypt_secret
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.core.timezones import to_org_tz
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDisplay, InfoscreenOverride, InfoscreenUrlAllowlist
from app.models.master import FireDept, OrgSettings
from app.models.objekt import AlarmInfoscreenToken
from app.models.stats import StatistikDashboardToken
from app.models.user import AuditLog
from app.models.weather import WeatherDashboardToken
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_overrides as service
from app.services.infoscreen_agent_resolver import Display, resolve
from app.services.infoscreen_aktivitaet import Aktivitaet
from tests.conftest import TestingSession as SessionLocal
from tests.test_infoscreen_agent_admin import admin, create_device, create_display, post  # noqa: F401
from tests.test_systemstatus_nav import _login, _make_org_admin

BASE = "/admin/infoscreen-agent"


@pytest.fixture
def public_dns(monkeypatch):
    lookup = lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(service.socket, "getaddrinfo", lookup)


@pytest.fixture
def monitor(client, admin):
    device_id, code, _ = create_device(client)
    bearer = client.post("/api/v1/infoscreen-agent/pair", json={"code": code}).json()["device_token"]
    raw = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        token = AlarmInfoscreenToken(
            org_id=admin, name="Monitor", token_hash=hash_api_key(raw), token_enc=encrypt_secret(raw), aktiv=True
        )
        db.add(token)
        db.add(WeatherDashboardToken(org_id=admin, label="Wetter", token_hash=hash_api_key(raw)))
        db.commit()
        token_id = token.id
        config = db.query(OrgSettings).filter(OrgSettings.org_id == admin).first()
        if config is None:
            config = OrgSettings(org_id=admin)
            db.add(config)
        config.alarm_infoscreen_wetter_url = "https://ignored.example/wetter/infoscreen/" + raw
        db.commit()
    display_id = create_display(client, device_id, token_id=token_id)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        db.get(InfoscreenDisplay, display_id).gsl_override_erlaubt = True
        db.commit()
    return device_id, display_id, bearer, raw


def form_data(org_id, **values):
    with SessionLocal() as db:
        org = db.get(FireDept, org_id)
        now = datetime.now(UTC)
        start = to_org_tz(now, org).strftime("%Y-%m-%dT%H:%M")
        end = to_org_tz(now + timedelta(hours=1), org).strftime("%Y-%m-%dT%H:%M")
    return dict(kind="manual", url_kind="weather", reason="Monitorwartung", von=start, bis=end, **values)


def test_create_cancel_audit_live_and_version(client, admin, monitor, monkeypatch):
    device_id, display_id, _, raw = monitor
    publish = AsyncMock()
    monkeypatch.setattr(events, "publish_config_changed", publish)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        version = db.get(InfoscreenDevice, device_id).config_version
    response = post(client, "/overrides/neu", form_data(admin, display_id=display_id))
    assert response.status_code == 200, response.text
    assert "Aktuelle Wirkung" in response.text and "zeigt jetzt: Wetter" in response.text
    assert raw not in response.text and "ignored.example" not in response.text
    publish.assert_awaited_once_with(admin, [display_id])
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        row = db.query(InfoscreenOverride).filter_by(org_id=admin).one()
        row_id = row.id
        assert row.url_ref == "/wetter/infoscreen/" + raw and row.created_by
        assert db.get(InfoscreenDevice, device_id).config_version == version + 1
        display = db.get(InfoscreenDisplay, display_id)
        before = display.revision, display.resolution_hash
        audit = db.query(AuditLog).filter_by(org_id=admin, action="infoscreen_agent.override_created").one()
        payload = json.loads(audit.payload_json)
        assert payload == dict(
            created_by=row.created_by, reason="Monitorwartung", display_id=display_id, kind="manual", url_kind="weather"
        )
        assert audit.ip is None and raw not in audit.payload_json
    for _ in range(2):
        assert client.get(BASE + "/uebersteuerung/status", params={"display_id": display_id}).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        display = db.get(InfoscreenDisplay, display_id)
        assert (display.revision, display.resolution_hash) == before
    assert post(client, f"/overrides/{row_id}/aufheben").status_code == 422
    assert post(client, f"/overrides/{row_id}/aufheben", {"reason": "Wartung beendet"}).status_code == 200
    assert post(client, f"/overrides/{row_id}/aufheben", {"reason": "Nochmals"}).status_code == 422
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        assert db.get(InfoscreenOverride, row_id).aufgehoben_am is not None
        assert db.get(InfoscreenDevice, device_id).config_version == version + 2
        audit = db.query(AuditLog).filter_by(org_id=admin, action="infoscreen_agent.override_cancelled").one()
        assert json.loads(audit.payload_json)["reason"] == "Wartung beendet"


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": ""},
        {"bis": ""},
        {"von": ""},
        {"bis": "2020-01-01T00:00"},
        {"von": "2027-01-01T00:00", "bis": "2027-01-01T08:01"},
        {"von": "2027-01-01T01:00", "bis": "2027-01-01T00:00"},
        {"kind": "invalid"},
        {"url_kind": "lagekarte"},
        {"url_kind": "einsatzstellen"},
        {"url_ref": "https://evil.example/wetter/infoscreen/token"},
        {"von": "2027-01-01T00:00+01:00"},
    ],
)
def test_invalid_forms(client, admin, changes):
    assert post(client, "/overrides/neu", form_data(admin) | changes).status_code == 422


def test_max_eight_hours_timezone_and_quick_actions(client, admin, monitor):
    device_id, display_id, _, raw = monitor
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        db.get(FireDept, admin).timezone = "Pacific/Auckland"
        db.commit()
    data = form_data(admin) | {"von": "2027-01-02T08:00", "bis": "2027-01-02T16:00"}
    assert post(client, "/overrides/neu", data).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        row = db.query(InfoscreenOverride).filter_by(org_id=admin).one()
        assert row.von == datetime(2027, 1, 1, 19) and row.bis - row.von == timedelta(hours=8)
    quick = dict(display_id=display_id, kind="gsl_page", url_kind="weather", reason="Wetterlage", minutes="30")
    assert post(client, "/overrides/neu", quick).status_code == 200
    assert post(client, "/overrides/neu", quick | {"minutes": "120"}).status_code == 200
    assert post(client, "/overrides/neu", quick | {"minutes": "31"}).status_code == 422
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        db.get(InfoscreenDisplay, display_id).gsl_override_erlaubt = False
        db.commit()
    assert post(client, "/overrides/neu", quick).status_code == 422


@pytest.mark.parametrize("role", ["readonly", "recorder", "incident_leader", "objekt_verwalter"])
def test_permissions_all_routes(client, role):
    username = "s5role" + uuid4().hex[:10]
    _make_org_admin(username, username, role)
    _login(client, username, "Test1234!")
    for path in ("/uebersteuerung", "/uebersteuerung/status"):
        assert client.get(BASE + path).status_code == 403
    for path in ("/overrides/neu", "/overrides/1/aufheben", "/allowlist/neu", "/allowlist/1/entfernen"):
        assert post(client, path).status_code == 403


def test_cross_org_all_routes_and_csrf(client, admin, monitor, public_dns):
    _, display_id, _, _ = monitor
    assert post(client, "/overrides/neu", form_data(admin, display_id=display_id)).status_code == 200
    assert post(client, "/allowlist/neu", {"host": "example.org"}).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        row_id = db.query(InfoscreenOverride.id).filter_by(org_id=admin).scalar()
        host_id = db.query(InfoscreenUrlAllowlist.id).filter_by(org_id=admin).scalar()
    for path in (
        "/overrides/neu",
        f"/overrides/{row_id}/aufheben",
        "/allowlist/neu",
        f"/allowlist/{host_id}/entfernen",
    ):
        assert client.post(BASE + path, data={"reason": "test"}).status_code == 403
    username = "s5other" + uuid4().hex[:10]
    other = _make_org_admin(username, username, "org_admin")
    client.cookies.clear()
    _login(client, username, "Test1234!")
    assert "Monitorwartung" not in client.get(BASE + "/uebersteuerung").text
    assert "example.org" not in client.get(BASE + "/uebersteuerung/status").text
    assert client.get(BASE + "/uebersteuerung/status", params={"display_id": display_id}).status_code == 404
    assert post(client, "/overrides/neu", form_data(other, display_id=display_id)).status_code == 404
    assert post(client, f"/overrides/{row_id}/aufheben", {"reason": "test"}).status_code == 404
    assert post(client, f"/allowlist/{host_id}/entfernen").status_code == 404


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1",
        "https://[::1]",
        "https://localhost",
        "https://x.local",
        "https://x.internal",
        "http://example.org",
        "https://example.org:444",
        "https://user@example.org",
        "https://user:pass@example.org",
        "//example.org",
        "https://example.org\\@evil.org",
        "https://2130706433",
        "https://example.org\n",
        "https://example.org/" + "a" * 2048,
    ],
)
def test_url_matrix(value, public_dns):
    with SessionLocal() as db, pytest.raises(ValueError):
        service.custom_url(db, 1, value)


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["10.1.1.1"], ["169.254.1.1"], ["::1"], ["fc00::1"], ["93.184.216.34", "192.168.1.1"], []],
)
def test_dns_nonpublic_rejected(addresses, monkeypatch):
    monkeypatch.setattr(
        service.socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses],
    )
    with pytest.raises(ValueError):
        service.hostname("example.org")


def test_allowlist_idna_snapshot_rebinding_removal(client, admin, monitor, public_dns, monkeypatch):
    device_id, display_id, bearer, _ = monitor
    assert post(client, "/allowlist/neu", {"host": "bücher.example"}).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        host = db.query(InfoscreenUrlAllowlist).filter_by(org_id=admin).one()
        assert host.host == "xn--bcher-kva.example"
        host_id = host.id
    data = form_data(admin) | dict(url_kind="custom_allowlisted", url_ref="https://bücher.example:443/status")
    assert post(client, "/overrides/neu", data).status_code == 200
    snapshot = client.get("/api/v1/infoscreen-agent/config", headers={"Authorization": "Bearer " + bearer})
    assert "xn--bcher-kva.example" in snapshot.json()["allowlist"]
    monkeypatch.setattr(
        service.socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
    )
    assert post(client, "/overrides/neu", data).status_code == 422
    assert post(client, "/allowlist/neu", {"host": "private.example"}).status_code == 422
    assert post(client, f"/allowlist/{host_id}/entfernen").status_code == 200
    from app.services.infoscreen_agent_snapshot import lade_overrides

    with SessionLocal() as db:
        set_tenant_context(db, admin)
        assert lade_overrides(db, db.get(InfoscreenDisplay, display_id)) == []


def test_dns_failure_clean(client, admin, monkeypatch):
    def failed(*a, **kw):
        raise socket.gaierror("secret diagnostic")

    monkeypatch.setattr(service.socket, "getaddrinfo", failed)
    response = post(client, "/allowlist/neu", {"host": "example.org"})
    assert response.status_code == 422 and "DNS-Aufloesung fehlgeschlagen" in response.text
    assert "secret diagnostic" not in response.text


def test_token_routes_scoped_and_stats_enabled(client, admin, monitor):
    _, _, _, raw = monitor
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        db.add(StatistikDashboardToken(org_id=admin, label="Stats", token_hash=hash_api_key(raw)))
        db.commit()
        with pytest.raises(ValueError):
            service.page_url(db, admin, "statistik", raw)
        config = db.query(OrgSettings).filter_by(org_id=admin).one()
        config.statistik_infoscreen_enabled = True
        db.commit()
        assert service.page_url(db, admin, "statistik", raw) == "/infoscreen/statistik/" + raw
        with pytest.raises(ValueError):
            service.page_url(db, admin + 100000, "weather", raw)
        with pytest.raises(ValueError):
            service.page_url(db, admin + 100000, "statistik", raw)


@pytest.mark.parametrize(
    "gsl,allowed,incident,ended,expected",
    [
        (True, True, False, False, "override"),
        (True, True, True, False, "override"),
        (True, False, True, False, "gsl"),
        (False, True, True, False, "incident"),
        (False, True, False, False, "default"),
        (False, True, False, True, "nachlauf"),
        (True, True, False, True, "gsl"),
    ],
)
def test_gsl_matrix_and_no_revival(gsl, allowed, incident, ended, expected):
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    override = dict(
        id=1,
        org_id=1,
        display_id=None,
        kind="gsl_page",
        von=now - timedelta(hours=1),
        bis=now + timedelta(hours=1),
        url="/weather",
    )
    activity = Aktivitaet(
        gsl, incident, now - timedelta(minutes=1) if ended else None, now - timedelta(minutes=1) if ended else None
    )
    display = Display(1, 1, "/alarm", gsl_override_erlaubt=allowed)
    result = resolve(display, now, activity, [override], None)
    assert result["mode"] == expected
    assert resolve(display, now + timedelta(hours=1), activity, [override], None)["mode"] != "override"
    future = override | {"von": now + timedelta(minutes=10)}
    expected_until = "2026-11-07T12:09:00Z" if expected == "nachlauf" else "2026-11-07T12:10:00Z"
    assert resolve(display, now, activity, [future], None)["valid_until"] == expected_until
    assert resolve(display, now, Aktivitaet(True), [override], None)["valid_until"] == "2026-11-07T13:00:00Z"


def test_override_retention_org_batches(monkeypatch):
    from app.services import infoscreen_agent_retention as retention

    monkeypatch.setattr(retention, "SessionLocal", SessionLocal)
    now = datetime.now(UTC).replace(tzinfo=None)
    ids = []
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for org_id in (1, 2):
            for days in (31, 32, 29):
                end = now - timedelta(days=days)
                row = InfoscreenOverride(
                    org_id=org_id,
                    kind="manual",
                    url_kind="weather",
                    reason="Test",
                    von=end - timedelta(hours=1),
                    bis=end,
                )
                db.add(row)
                db.flush()
                ids.append((row.id, days))
        db.commit()
    assert retention.purge_old_overrides(chunk_size=1) >= 4
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for row_id, days in ids:
            assert (db.get(InfoscreenOverride, row_id) is None) == (days > 30)
    with pytest.raises(ValueError):
        retention.purge_old_overrides(0)


def test_snapshot_allowlist_org_scope_and_trusted_host(client, admin, monitor, public_dns, monkeypatch):
    from app.config import settings
    from tests.test_infoscreen_agent_s2_api import setup_display

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://trusted.example")
    assert post(client, "/allowlist/neu", {"host": "monitor.example"}).status_code == 200
    other_name = "s5config" + uuid4().hex[:10]
    other_org = _make_org_admin(other_name, other_name, "org_admin")
    _, _, _, headers, _ = setup_display(other_org)
    response = client.get("/api/v1/infoscreen-agent/config", headers=headers | {"Host": "evil.example"})
    assert response.status_code == 200
    assert response.json()["allowlist"] == ["trusted.example"]
    _, _, bearer, _ = monitor
    response = client.get("/api/v1/infoscreen-agent/config", headers={"Authorization": "Bearer " + bearer})
    assert response.json()["allowlist"] == ["monitor.example", "trusted.example"]


def test_mobile_htmx_create_cancel(client, admin, monitor):
    playwright_api = pytest.importorskip("playwright.sync_api")

    data = form_data(admin)
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                channel="chromium", executable_path=os.environ.get("ECP_PLAYWRIGHT_EXECUTABLE")
            )
        except playwright_api.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium nicht installiert")
            raise
        page = browser.new_page(viewport={"width": 390, "height": 844})

        def proxy(route):
            request = route.request
            response = client.request(
                request.method, request.url, content=request.post_data_buffer, headers=request.headers
            )
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            }
            route.fulfill(status=response.status_code, headers=headers, body=response.content)

        page.route("http://testserver/**", proxy)
        page.route(re.compile(r"https://.*"), lambda route: route.abort())
        page.goto("http://testserver" + BASE + "/uebersteuerung")
        page.wait_for_function("typeof htmx !== 'undefined'")
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(frame.url))
        form = page.locator('form[hx-post="' + BASE + '/overrides/neu"]').first
        form.locator('[name="kind"]').select_option("manual")
        form.locator('[name="url_kind"]').select_option("weather")
        for key in ("von", "bis"):
            form.locator('[name="' + key + '"]').fill(data[key])
        form.locator('[name="reason"]').fill("Browserprobe")
        form.get_by_role("button", name="Uebersteuerung anlegen").click()
        page.get_by_text("Display Hauptmonitor zeigt jetzt: Wetter", exact=False).wait_for()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        cancel = page.locator('form[hx-post$="/aufheben"]')
        cancel.locator('[name="reason"]').fill("Browserprobe beendet")
        cancel.get_by_role("button", name="Aufheben", exact=True).click()
        page.get_by_text("Aufgehoben:", exact=False).wait_for()
        assert navigations == []
        browser.close()
