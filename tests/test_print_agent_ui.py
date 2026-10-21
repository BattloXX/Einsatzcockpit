"""Admin-UI für Windows-Druckagents: Anlegen, Inventar, Routen, Erreichbarkeit im Druckdialog."""
from datetime import UTC, datetime
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import (
    GATEWAY_KIND_WINDOWS_AGENT,
    GATEWAY_STATUS_ONLINE,
    Gateway,
    Printer,
    PrinterAgentRoute,
    PrintRule,
)
from tests.test_gateway_druckregeln_render import ORG_ID, _login, _setup


def _jetzt():
    return datetime.now(UTC).replace(tzinfo=None)


def _admin(client, name):
    _setup(name, "org_admin")
    _login(client, name, "Test1234!")
    return client.cookies.get("ec_csrf")


def _agent(name="PC A", queues=None, online=True) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = Gateway(
            org_id=ORG_ID, name=name + " " + uuid4().hex[:4], kind=GATEWAY_KIND_WINDOWS_AGENT,
            device_token_hash="ui-" + uuid4().hex, status=GATEWAY_STATUS_ONLINE if online else "offline",
            last_seen_at=_jetzt(),
            agent_info={"host": {"host": "KOMMANDOFUNK-1", "os": "Windows 11"}, "queues": queues or [{
                "queue": "Kyocera Kommandofunk", "driver": "Kyocera TASKalfa", "host": "10.0.0.50",
                "identity": {"ip": "10.0.0.50"}, "capabilities": {"media": ["A4", "A3"], "duplex": True},
            }]},
        )
        db.add(gw)
        db.commit()
        return gw.id
    finally:
        db.close()


def _push_mitschneiden(monkeypatch) -> list:
    nachrichten: list = []

    async def push(org_id, gateway_ids, message):
        nachrichten.append((org_id, gateway_ids, message))

    monkeypatch.setattr("app.routers.ws.push_agent_message", push)
    return nachrichten


def _routen(printer_id: int) -> list[PrinterAgentRoute]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        routen = (db.query(PrinterAgentRoute).filter(PrinterAgentRoute.printer_id == printer_id)
                  .order_by(PrinterAgentRoute.priority).all())
        for route in routen:
            db.expunge(route)
        return routen
    finally:
        db.close()


def test_gateway_anlegen_als_windows_agent(client):
    csrf = _admin(client, "agent_ui_create")
    antwort = client.post("/gateway/create", data={"name": "Kommandofunk PC A", "kind": "windows_agent",
                                                   "_csrf": csrf}, follow_redirects=False)
    assert antwort.status_code == 303
    gw_id = int(antwort.headers["location"].split("/gateway/")[1].split("?")[0])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Gateway, gw_id).kind == GATEWAY_KIND_WINDOWS_AGENT
    finally:
        db.close()
    seite = client.get(f"/gateway/{gw_id}?pairing_code=ABCD1234")
    assert seite.status_code == 200
    assert "EinsatzcockpitPrintAgent.exe pair --url" in seite.text
    assert "--code ABCD1234" in seite.text
    # Linux-Gateway-Abschnitte (Alarmleitung) erscheinen nicht.
    assert "Alarmleitung" not in seite.text

    unbekannt = client.post("/gateway/create", data={"name": "X", "kind": "boese", "_csrf": csrf},
                            follow_redirects=False)
    neu_id = int(unbekannt.headers["location"].split("/gateway/")[1].split("?")[0])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Gateway, neu_id).kind == "linux"
    finally:
        db.close()


def test_agent_seite_zeigt_inventar(client):
    _admin(client, "agent_ui_inventar")
    gw_id = _agent()
    seite = client.get(f"/gateway/{gw_id}")
    assert seite.status_code == 200
    assert "Kyocera Kommandofunk" in seite.text
    assert "KOMMANDOFUNK-1" in seite.text
    assert f'action="/gateway/{gw_id}/routes/add"' in seite.text
    assert 'name="_csrf"' in seite.text


def test_queue_als_neues_druckziel_zuordnen_und_redundanz(client, monkeypatch):
    nachrichten = _push_mitschneiden(monkeypatch)
    csrf = _admin(client, "agent_ui_route")
    a = _agent("PC A")
    b = _agent("PC B")
    antwort = client.post(f"/gateway/{a}/routes/add", data={
        "_csrf": csrf, "local_queue": "Kyocera Kommandofunk", "ziel": "neu",
        "name": "Kommandofunk " + uuid4().hex[:4], "priority": "10", "render_mode": "driver",
    }, follow_redirects=False)
    assert antwort.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route_a = db.query(PrinterAgentRoute).filter(PrinterAgentRoute.gateway_id == a).one()
        printer = db.get(Printer, route_a.printer_id)
        assert (printer.aktiv, printer.gateway_id, printer.org_id) == (True, a, ORG_ID)
        assert printer.capabilities["media"] == ["A4", "A3"]
        assert printer.identity == {"ip": "10.0.0.50"}
        printer_id = printer.id
    finally:
        db.close()
    assert nachrichten[-1][1] == [a]
    assert nachrichten[-1][2]["type"] == "config_sync"
    assert nachrichten[-1][2]["payload"]["routes"][0]["local_queue"] == "Kyocera Kommandofunk"

    # Zweiter PC auf dasselbe Druckziel (Redundanz).
    client.post(f"/gateway/{b}/routes/add", data={
        "_csrf": csrf, "local_queue": "Kyocera Kommandofunk", "ziel": str(printer_id), "priority": "20",
        "render_mode": "pdf_direct",
    }, follow_redirects=False)
    routen = _routen(printer_id)
    assert [(r.gateway_id, r.priority, r.render_mode) for r in routen] == [(a, 10, "driver"), (b, 20, "pdf_direct")]
    seite = client.get(f"/gateway/{b}")
    assert 'title="Redundanz: Reihenfolge der Agents für dieses Druckziel"' in seite.text
    assert "1. PC A" in seite.text

    doppelt = client.post(f"/gateway/{b}/routes/add", data={
        "_csrf": csrf, "local_queue": "Kyocera Kommandofunk", "ziel": str(printer_id),
    }, follow_redirects=False)
    assert "error=route_doppelt" in doppelt.headers["location"]
    assert len(_routen(printer_id)) == 2


def test_route_bearbeiten_und_entfernen(client, monkeypatch):
    nachrichten = _push_mitschneiden(monkeypatch)
    csrf = _admin(client, "agent_ui_route_edit")
    a = _agent()
    client.post(f"/gateway/{a}/routes/add", data={
        "_csrf": csrf, "local_queue": "Kyocera Kommandofunk", "ziel": "neu", "name": "Ziel " + uuid4().hex[:4],
    })
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route_id = db.query(PrinterAgentRoute).filter(PrinterAgentRoute.gateway_id == a).one().id
    finally:
        db.close()
    client.post(f"/gateway/routes/{route_id}/save", data={
        "_csrf": csrf, "priority": "5", "render_mode": "folder", "local_queue": "Archiv",
        "target_dir": r"D:\Archiv", "tray": "Fach 2", "dpi": "5000", "command": "C:\\Tools\\x.exe\n{pdf}",
        "aktiv": "1",
    })
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route = db.get(PrinterAgentRoute, route_id)
        assert (route.priority, route.render_mode, route.local_queue, route.aktiv) == (5, "folder", "Archiv", True)
        assert route.identity_check is False
        assert route.options == {"tray": "Fach 2", "dpi": 1200, "target_dir": r"D:\Archiv",
                                 "command": ["C:\\Tools\\x.exe", "{pdf}"]}
    finally:
        db.close()
    client.post(f"/gateway/routes/{route_id}/delete", data={"_csrf": csrf})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(PrinterAgentRoute, route_id) is None
    finally:
        db.close()
    assert all(eintrag[2]["type"] == "config_sync" for eintrag in nachrichten)
    assert len(nachrichten) == 3


def test_routen_nur_fuer_admins_und_nur_eigene_org(client, monkeypatch):
    _push_mitschneiden(monkeypatch)
    a = _agent()
    _setup("agent_ui_recorder", "recorder")
    _login(client, "agent_ui_recorder", "Test1234!")
    antwort = client.post(f"/gateway/{a}/routes/add", data={
        "_csrf": client.cookies.get("ec_csrf"), "local_queue": "X", "ziel": "neu"})
    assert antwort.status_code == 403

    csrf = _admin(client, "agent_ui_fremd")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fremd = Gateway(org_id=993999, name="Fremd", kind=GATEWAY_KIND_WINDOWS_AGENT)
        db.add(fremd)
        db.flush()
        fremd_printer = Printer(org_id=993999, gateway_id=fremd.id, name="Fremd", uri="agent://f", aktiv=True)
        db.add(fremd_printer)
        db.commit()
        fremd_id, fremd_printer_id = fremd.id, fremd_printer.id
    finally:
        db.close()
    assert client.post(f"/gateway/{fremd_id}/routes/add", data={
        "_csrf": csrf, "local_queue": "X", "ziel": "neu"}).status_code == 404
    assert client.post(f"/gateway/{a}/routes/add", data={
        "_csrf": csrf, "local_queue": "X", "ziel": str(fremd_printer_id)}).status_code == 404


def test_druckdialog_erreichbarkeit_aus_routen(client, monkeypatch):
    _push_mitschneiden(monkeypatch)
    csrf = _admin(client, "agent_ui_dialog")
    a = _agent()
    name = "Dialogziel " + uuid4().hex[:4]
    client.post(f"/gateway/{a}/routes/add", data={
        "_csrf": csrf, "local_queue": "Kyocera Kommandofunk", "ziel": "neu", "name": name})

    def eintrag():
        daten = client.get("/gateway/printers.json").json()
        return next(p for p in daten["printers"] if p["name"] == name)

    assert eintrag()["reachable"] is False  # Route noch nie als bereit gemeldet
    assert eintrag()["media"] == ["A4", "A3"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route = db.query(PrinterAgentRoute).filter(PrinterAgentRoute.gateway_id == a).one()
        route.ready = True
        route.readiness = {"state": "ready", "checked_at": _jetzt().isoformat()}
        route.identity = {"ip": "10.0.0.50"}
        db.commit()
    finally:
        db.close()
    assert eintrag()["reachable"] is True


def test_druckregeln_zeigen_drucker_aller_gateways(client, monkeypatch):
    _push_mitschneiden(monkeypatch)
    csrf = _admin(client, "agent_ui_regeln")
    gw_linux = _setup("agent_ui_regeln2", "org_admin")
    a = _agent()
    name = "Regelziel " + uuid4().hex[:4]
    client.post(f"/gateway/{a}/routes/add", data={
        "_csrf": csrf, "local_queue": "Kyocera Kommandofunk", "ziel": "neu", "name": name})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(PrintRule(org_id=ORG_ID, name="Agent-Regel " + uuid4().hex[:4], trigger="einsatz_created",
                         documents=["einsatzinfo"], printer_ids=[]))
        db.commit()
    finally:
        db.close()
    seite = client.get(f"/gateway/{gw_linux}/druckregeln")
    assert seite.status_code == 200
    assert name in seite.text
