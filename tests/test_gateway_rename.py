"""Gateway- und Druckzielnamen: Adminrechte, Tenant-Grenzen und Config-Push."""
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Printer, PrinterAgentRoute
from app.models.master import FireDept
from app.services.dienst_monitor_service import pruefe_dienste
from tests.test_gateway_offline_alerts import gateway
from tests.test_print_agent_ui import _admin, _push_mitschneiden


@pytest.fixture
def db(client):
    with SessionLocal() as session:
        set_tenant_context(session, None)
        yield session


@pytest.fixture
def foreign_org(db):
    org = FireDept(slug="rename-" + uuid4().hex, name="Andere Organisation")
    db.add(org)
    db.commit()
    return org.id


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
def test_gateway_rename_speichert_und_zeigt_namen(client, db, monkeypatch, kind):
    csrf = _admin(client, "rename_" + kind)
    gw = gateway(db, 1, kind=kind)
    messages = _push_mitschneiden(monkeypatch)
    linux = AsyncMock()
    monkeypatch.setattr("app.routers.ws.push_config_sync", linux)
    response = client.post(f"/gateway/{gw.id}/rename", data={
        "_csrf": csrf, "name": "  Neuer Gatewayname  ", "standort": "  Fahrzeughalle  "})
    assert response.status_code == 200
    db.refresh(gw)
    assert (gw.name, gw.standort) == ("Neuer Gatewayname", "Fahrzeughalle")
    assert f'action="/gateway/{gw.id}/rename"' in response.text
    assert 'value="Neuer Gatewayname"' in response.text
    assert "Neuer Gatewayname" in client.get("/gateway").text
    check = next(c for c in pruefe_dienste(db, 1) if c.key == "print_gateway")
    assert any(t.name == "Neuer Gatewayname" for t in check.teile)
    if kind == "windows_agent":
        assert len(messages) == 1
        assert messages[0][1] == [gw.id]
        assert messages[0][2]["payload"]["name"] == "Neuer Gatewayname"
    else:
        assert messages == []
    linux.assert_not_awaited()


def test_gateway_rename_grenzen_und_leer(client, db, monkeypatch):
    csrf = _admin(client, "rename_limits")
    gw = gateway(db, 1)
    push = AsyncMock()
    monkeypatch.setattr("app.routers.ui_gateway._agent_config_push", push)
    url = f"/gateway/{gw.id}/rename"
    response = client.post(url, data={"_csrf": csrf, "name": "   ", "standort": "X"})
    assert "Bitte einen Namen angeben" in response.text
    db.refresh(gw)
    assert gw.name == "Wichtig"
    assert gw.standort is None
    response = client.post(url, data={"_csrf": csrf, "name": " " + "N" * 151,
                                          "standort": " " + "S" * 201})
    assert response.status_code == 200
    db.refresh(gw)
    assert (len(gw.name), len(gw.standort)) == (150, 200)
    client.post(url, data={"_csrf": csrf, "name": "Name", "standort": "   "})
    db.refresh(gw)
    assert gw.standort is None
    push.assert_not_awaited()


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
def test_printer_rename_push_und_rueckkehr(client, db, monkeypatch, kind):
    csrf = _admin(client, "rename_printer_" + kind)
    owner = gateway(db, 1, kind=kind)
    second = gateway(db, 1, kind="windows_agent")
    unrelated = gateway(db, 1, kind="windows_agent")
    printer = Printer(org_id=1, gateway_id=owner.id, name="Alt", uri="ipp://test", aktiv=True)
    db.add(printer)
    db.flush()
    agents = [second] if kind == "linux" else [owner, second]
    for agent in agents:
        db.add(PrinterAgentRoute(org_id=1, printer_id=printer.id, gateway_id=agent.id, local_queue="Queue"))
    db.commit()
    messages = _push_mitschneiden(monkeypatch)
    linux = AsyncMock()
    monkeypatch.setattr("app.routers.ws.push_config_sync", linux)
    page = client.get(f"/gateway/{second.id}")
    assert f'action="/gateway/printers/{printer.id}/rename"' in page.text
    assert f'name="return_gateway_id" value="{second.id}"' in page.text
    response = client.post(f"/gateway/printers/{printer.id}/rename", data={
        "_csrf": csrf, "name": "  Neuer Druckzielname  ", "return_gateway_id": second.id},
        follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/gateway/{second.id}?saved=1#routen"
    db.refresh(printer)
    assert printer.name == "Neuer Druckzielname"
    assert {m[1][0] for m in messages} == {a.id for a in agents}
    assert all(m[1] != [unrelated.id] for m in messages)
    assert all(m[2]["type"] == "config_sync" for m in messages)
    assert "Neuer Druckzielname" in client.get(f"/gateway/{second.id}").text
    if kind == "linux":
        linux.assert_awaited_once_with(1, owner.id)
    else:
        linux.assert_not_awaited()


def test_rename_tenant_rechte_csrf(client, db, foreign_org, monkeypatch):
    from tests.test_gateway_druckregeln_render import _login, _setup
    csrf = _admin(client, "rename_security")
    own, foreign = gateway(db, 1), gateway(db, foreign_org, kind="windows_agent")
    printer = Printer(org_id=foreign_org, gateway_id=foreign.id, name="Fremd", uri="ipp://foreign")
    db.add(printer)
    db.commit()
    push = AsyncMock()
    linux = AsyncMock()
    monkeypatch.setattr("app.routers.ui_gateway._agent_config_push", push)
    monkeypatch.setattr("app.routers.ws.push_config_sync", linux)
    urls = [f"/gateway/{foreign.id}/rename", f"/gateway/printers/{printer.id}/rename"]
    for url in urls:
        assert client.post(url, data={"_csrf": csrf, "name": "Angriff"}).status_code == 404
    own_url = f"/gateway/{own.id}/rename"
    assert client.post(own_url, data={"name": "CSRF"}).status_code == 403
    _setup("rename_recorder", "recorder")
    _login(client, "rename_recorder", "Test1234!")
    for url in [own_url, *urls]:
        assert client.post(url, data={"_csrf": client.cookies.get("ec_csrf"), "name": "Angriff"}).status_code == 403
    db.refresh(foreign)
    db.refresh(printer)
    assert foreign.name == "Wichtig"
    assert printer.name == "Fremd"
    push.assert_not_awaited()
    linux.assert_not_awaited()


def test_printer_rename_leer_grenzen_und_fremdes_ruecksprungziel(client, db, foreign_org, monkeypatch):
    csrf = _admin(client, "rename_printer_limits")
    owner = gateway(db, 1)
    foreign = gateway(db, foreign_org)
    printer = Printer(org_id=1, gateway_id=owner.id, name="Alt", uri="ipp://test")
    db.add(printer)
    db.commit()
    push = AsyncMock()
    linux = AsyncMock()
    monkeypatch.setattr("app.routers.ui_gateway._agent_config_push", push)
    monkeypatch.setattr("app.routers.ws.push_config_sync", linux)
    url = f"/gateway/printers/{printer.id}/rename"
    response = client.post(url, data={"_csrf": csrf, "name": "   "}, follow_redirects=False)
    assert response.headers["location"] == f"/gateway/{owner.id}?error=printer#drucker"
    response = client.post(url, data={"_csrf": csrf, "name": "Angriff", "return_gateway_id": foreign.id})
    assert response.status_code == 404
    db.refresh(printer)
    assert printer.name == "Alt"
    push.assert_not_awaited()
    linux.assert_not_awaited()
    response = client.post(url, data={"_csrf": csrf, "name": " " + "N" * 151}, follow_redirects=False)
    assert response.headers["location"] == f"/gateway/{owner.id}?saved=1#drucker"
    db.refresh(printer)
    assert printer.name == "N" * 150
    linux.assert_awaited_once_with(1, owner.id)
