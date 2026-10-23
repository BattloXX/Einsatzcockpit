"""Windows-Druckagent: WebSocket /ws/print-agent, Lease-Weiche in dispatch_job, Watchdog,
gezielter Abbruch und Auflösen unklarer Aufträge."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import (
    DELIVERY_LEASE,
    DELIVERY_PUSH,
    GATEWAY_KIND_LINUX,
    GATEWAY_KIND_WINDOWS_AGENT,
    GATEWAY_STATUS_ONLINE,
    JOB_DONE,
    JOB_FAILED,
    JOB_PRINTING,
    JOB_QUEUED,
    JOB_SENT,
    JOB_UNCERTAIN,
    Gateway,
    Printer,
    PrinterAgentRoute,
    PrintJob,
)
from app.services import print_agent_service


def _jetzt():
    return datetime.now(UTC).replace(tzinfo=None)


def _anlegen(org_id: int, *, kind=GATEWAY_KIND_WINDOWS_AGENT, routen=True) -> dict:
    """Agent mit Token, Druckziel und bereiter Route; gibt IDs + Klartext-Token zurück."""
    token = "agent-" + uuid4().hex
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = Gateway(org_id=org_id, name="Kommandofunk PC A", kind=kind,
                     device_token_hash=hash_api_key(token), status=GATEWAY_STATUS_ONLINE,
                     last_seen_at=_jetzt(), agent_connection_id=uuid4().hex,
                     agent_presence_expires_at=_jetzt() + timedelta(seconds=90))
        db.add(gw)
        db.flush()
        printer = Printer(org_id=org_id, gateway_id=gw.id, name="Kommandofunk", uri="agent://kf", aktiv=True)
        db.add(printer)
        db.flush()
        route_id = None
        if routen:
            route = PrinterAgentRoute(org_id=org_id, printer_id=printer.id, gateway_id=gw.id, priority=10,
                                      local_queue="Kyocera Kommandofunk", ready=True,
                                      readiness={"state": "ready", "checked_at": _jetzt().isoformat()})
            db.add(route)
            db.flush()
            route_id = route.id
        db.commit()
        return {"token": token, "gateway_id": gw.id, "printer_id": printer.id, "route_id": route_id,
                "org_id": org_id}
    finally:
        db.close()


def _job(daten: dict, *, enqueue=True, **werte) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = PrintJob(org_id=daten["org_id"], gateway_id=daten["gateway_id"], printer_id=daten["printer_id"],
                       document_type="einsatzinfo", idempotency_key="agent-ws-" + uuid4().hex, **werte)
        db.add(job)
        db.commit()
        if enqueue:
            print_agent_service.enqueue_for_agents(db, job)
        return job.id
    finally:
        db.close()


def _laden(job_id: int) -> PrintJob:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = db.get(PrintJob, job_id)
        db.expunge(job)
        return job
    finally:
        db.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _recv(ws, typ: str, versuche: int = 10) -> dict:
    for _ in range(versuche):
        msg = json.loads(ws.receive_text())
        if msg.get("type") == typ:
            return msg
    raise AssertionError(f"keine Nachricht {typ}")


def test_agent_token_auf_linux_kanal_und_umgekehrt_abgelehnt(client):
    agent = _anlegen(993101)
    linux = _anlegen(993102, kind=GATEWAY_KIND_LINUX, routen=False)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/gateway", headers=_auth(agent["token"])) as ws:
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/print-agent", headers=_auth(linux["token"])) as ws:
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/print-agent", headers=_auth("falsch")) as ws:
            ws.receive_text()


def test_kompletter_lease_ablauf_ueber_websocket(client):
    daten = _anlegen(993103)
    job_id = _job(daten)
    with client.websocket_connect("/ws/print-agent", headers=_auth(daten["token"])) as ws:
        sync = _recv(ws, "config_sync")
        assert [r["route_id"] for r in sync["payload"]["routes"]] == [daten["route_id"]]
        assert sync["payload"]["routes"][0]["local_queue"] == "Kyocera Kommandofunk"

        ws.send_text(json.dumps({"type": "hello", "payload": {"version": "0.1.0", "open_jobs": [],
                                                              "host_info": {"host": "PC-A"}}}))
        _recv(ws, "config_sync")
        ws.send_text(json.dumps({"type": "lease_request", "rid": "r1", "payload": {"max": 5}}))
        grant_msg = _recv(ws, "lease_grant")
        assert grant_msg["rid"] == "r1"
        grant = grant_msg["payload"]["jobs"][0]
        assert grant["job_id"] == job_id
        assert grant["artifact_url"].startswith("http") or "/api/v1/print/" in grant["artifact_url"]

        ws.send_text(json.dumps({"type": "print_intent", "rid": "r2",
                                 "payload": {"job_id": job_id, "lease_token": grant["lease_token"]}}))
        intent = _recv(ws, "intent_result")
        assert (intent["rid"], intent["payload"]["ok"]) == ("r2", True)

        ws.send_text(json.dumps({"type": "job_status", "id": "o1", "payload": {
            "job_id": job_id, "lease_token": grant["lease_token"], "status": "printing",
            "phase": "spooled", "spool_ref": "win:12"}}))
        assert _recv(ws, "ack")["payload"]["id"] == "o1"
        assert _laden(job_id).status == JOB_PRINTING

        ws.send_text(json.dumps({"type": "job_status", "id": "o2", "payload": {
            "job_id": job_id, "lease_token": grant["lease_token"], "status": "done", "phase": "spooled"}}))
        _recv(ws, "ack")
    job = _laden(job_id)
    assert (job.status, job.spool_ref, job.lease_gateway_id) == (JOB_DONE, "win:12", daten["gateway_id"])

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Gateway, daten["gateway_id"]).agent_info["host"] == {"host": "PC-A"}
    finally:
        db.close()


def test_veraltete_statusmeldung_wird_bestaetigt_aber_nicht_angewendet(client):
    daten = _anlegen(993104)
    job_id = _job(daten)
    with client.websocket_connect("/ws/print-agent", headers=_auth(daten["token"])) as ws:
        _recv(ws, "config_sync")
        ws.send_text(json.dumps({"type": "job_status", "id": "x", "payload": {
            "job_id": job_id, "lease_token": "alt", "status": "done", "phase": "spooled"}}))
        assert _recv(ws, "ack")["payload"]["id"] == "x"
    assert _laden(job_id).status == JOB_QUEUED


def test_fehlschlag_nach_uebergabe_loest_ersatzdruck_aus(client, monkeypatch):
    aufrufe = []

    async def fallback(job_id):
        aufrufe.append(job_id)

    monkeypatch.setattr("app.services.print_dispatcher.dispatch_fallback_for_failed_job", fallback)
    daten = _anlegen(993105)
    job_id = _job(daten)
    with client.websocket_connect("/ws/print-agent", headers=_auth(daten["token"])) as ws:
        _recv(ws, "config_sync")
        ws.send_text(json.dumps({"type": "lease_request", "rid": "r", "payload": {"max": 1}}))
        token = _recv(ws, "lease_grant")["payload"]["jobs"][0]["lease_token"]
        ws.send_text(json.dumps({"type": "print_intent", "rid": "i",
                                 "payload": {"job_id": job_id, "lease_token": token}}))
        _recv(ws, "intent_result")
        ws.send_text(json.dumps({"type": "job_status", "id": "f", "payload": {
            "job_id": job_id, "lease_token": token, "status": "failed", "phase": "submitting",
            "error": "Queue fehlt"}}))
        _recv(ws, "ack")
    assert _laden(job_id).status == JOB_FAILED
    assert aufrufe == [job_id]


def test_hello_mit_unbekanntem_offenen_job_bekommt_abandon(client):
    daten = _anlegen(993106)
    with client.websocket_connect("/ws/print-agent", headers=_auth(daten["token"])) as ws:
        _recv(ws, "config_sync")
        ws.send_text(json.dumps({"type": "hello", "payload": {
            "version": "0.1.0", "open_jobs": [{"job_id": 987650001, "lease_token": "x",
                                               "local_state": "spooled"}]}}))
        abandon = _recv(ws, "abandon")
        assert abandon["payload"]["job_id"] == 987650001


def test_readiness_ueber_websocket_aktualisiert_route(client):
    daten = _anlegen(993107)
    with client.websocket_connect("/ws/print-agent", headers=_auth(daten["token"])) as ws:
        _recv(ws, "config_sync")
        ws.send_text(json.dumps({"type": "readiness", "payload": {"routes": [{
            "route_id": daten["route_id"], "ready": False, "state": "offline", "detail": "Papierstau"}]}}))
        ws.send_text(json.dumps({"type": "ping"}))
        _recv(ws, "pong")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route = db.get(PrinterAgentRoute, daten["route_id"])
        assert (route.ready, route.readiness["detail"]) == (False, "Papierstau")
    finally:
        db.close()


def test_fremder_agent_least_ueber_websocket_keine_jobs(client):
    daten = _anlegen(993108)
    _job(daten)
    fremd = _anlegen(993109, routen=False)
    with client.websocket_connect("/ws/print-agent", headers=_auth(fremd["token"])) as ws:
        _recv(ws, "config_sync")
        ws.send_text(json.dumps({"type": "lease_request", "rid": "r", "payload": {"max": 5}}))
        assert _recv(ws, "lease_grant")["payload"]["jobs"] == []


def test_dispatch_job_mit_agent_route_reiht_ein_statt_zu_pushen(monkeypatch):
    from app.services import print_dispatcher

    gesendet, geweckt = [], []

    async def push(*args, **kwargs):
        gesendet.append(args)
        return {}

    async def wecken(org_id, gateway_ids, job_ids):
        geweckt.append((org_id, gateway_ids, job_ids))

    monkeypatch.setattr("app.routers.ws.dispatch_print_job", push)
    monkeypatch.setattr("app.routers.ws.notify_agents_job_available", wecken)
    daten = _anlegen(993110)
    job_id = _job(daten, enqueue=False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        ergebnis = asyncio.run(print_dispatcher.dispatch_job(db, db.get(PrintJob, job_id)))
    finally:
        db.close()
    assert ergebnis["status"] == JOB_QUEUED
    assert gesendet == []
    assert geweckt == [(daten["org_id"], [daten["gateway_id"]], [job_id])]
    job = _laden(job_id)
    assert (job.delivery, job.status) == (DELIVERY_LEASE, JOB_QUEUED)


def test_dispatch_job_ohne_agent_route_bleibt_push(monkeypatch):
    from app.services import print_dispatcher

    gesendet = []

    async def push(org_id, job_id, payload, timeout=20.0):
        gesendet.append(job_id)
        return {"job_id": job_id, "status": JOB_SENT}

    monkeypatch.setattr("app.routers.ws.dispatch_print_job", push)
    daten = _anlegen(993111, kind=GATEWAY_KIND_LINUX, routen=False)
    job_id = _job(daten, enqueue=False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        asyncio.run(print_dispatcher.dispatch_job(db, db.get(PrintJob, job_id)))
    finally:
        db.close()
    assert gesendet == [job_id]
    assert _laden(job_id).delivery == DELIVERY_PUSH


def test_watchdog_eskaliert_keine_agent_jobs():
    from app.services.print_watchdog import _pruefe_haengende_jobs

    daten = _anlegen(993112)
    alt = _jetzt() - timedelta(hours=2)
    job_id = _job(daten, enqueue=False, status=JOB_SENT, delivery=DELIVERY_LEASE, phase="spooled",
                  aktualisiert_am=alt, agent_contact_at=_jetzt())
    assert job_id not in _pruefe_haengende_jobs()
    assert _laden(job_id).status == JOB_SENT


def _admin_client(client, username: str):
    from tests.test_gateway_druckregeln_render import _login, _setup
    gw_id = _setup(username, "org_admin")
    _login(client, username, "Test1234!")
    return gw_id


def _org1_job(gw_id: int, **werte) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        printer = Printer(org_id=1, gateway_id=gw_id, name="Agent-Ziel " + uuid4().hex[:6], uri="agent://x",
                          aktiv=True)
        db.add(printer)
        db.flush()
        job = PrintJob(org_id=1, gateway_id=gw_id, printer_id=printer.id, document_type="einsatzinfo",
                       idempotency_key="agent-ui-" + uuid4().hex, **werte)
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


@pytest.mark.parametrize(("aktion", "erwartet"), [("done", JOB_DONE), ("discard", "canceled")])
def test_unklaren_auftrag_aufloesen(client, aktion, erwartet):
    gw_id = _admin_client(client, f"agent_resolve_{aktion}")
    job_id = _org1_job(gw_id, status=JOB_UNCERTAIN, delivery=DELIVERY_LEASE, error="Neustart")
    antwort = client.post(f"/gateway/jobs/{job_id}/resolve",
                          data={"action": aktion, "_csrf": client.cookies.get("ec_csrf")},
                          headers={"HX-Request": "true"})
    assert antwort.status_code == 200
    assert 'id="historie"' in antwort.text
    job = _laden(job_id)
    assert job.status == erwartet
    assert "geprüft von" in job.error


def test_unklaren_auftrag_erneut_drucken_legt_neuen_job_an(client, monkeypatch):
    gesendet = []

    async def dispatch(db, job):
        gesendet.append(job.id)
        return {"status": JOB_QUEUED}

    monkeypatch.setattr("app.services.print_dispatcher.dispatch_job", dispatch)
    gw_id = _admin_client(client, "agent_resolve_reprint")
    job_id = _org1_job(gw_id, status=JOB_UNCERTAIN, delivery=DELIVERY_LEASE)
    antwort = client.post(f"/gateway/jobs/{job_id}/resolve",
                          data={"action": "reprint", "_csrf": client.cookies.get("ec_csrf")},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert len(gesendet) == 1
    neu = _laden(gesendet[0])
    assert (neu.fallback_of_job_id, neu.source) == (job_id, "manual")
    assert _laden(job_id).status == "canceled"


def test_aufloesen_nur_fuer_unklare_auftraege_und_nur_admin(client):
    gw_id = _admin_client(client, "agent_resolve_guard")
    job_id = _org1_job(gw_id, status=JOB_PRINTING, delivery=DELIVERY_LEASE)
    client.post(f"/gateway/jobs/{job_id}/resolve",
                data={"action": "done", "_csrf": client.cookies.get("ec_csrf")})
    assert _laden(job_id).status == JOB_PRINTING
    assert client.post(f"/gateway/jobs/{job_id}/resolve",
                       data={"action": "boese", "_csrf": client.cookies.get("ec_csrf")}).status_code == 400

    from tests.test_gateway_druckregeln_render import _login, _setup
    _setup("agent_resolve_recorder", "recorder")
    _login(client, "agent_resolve_recorder", "Test1234!")
    assert client.post(f"/gateway/jobs/{job_id}/resolve",
                       data={"action": "done", "_csrf": client.cookies.get("ec_csrf")}).status_code == 403


def test_abbruch_eines_agent_jobs_geht_gezielt_an_den_halter(client, monkeypatch):
    nachrichten = []

    async def noop(*_args, **_kwargs):
        return None

    async def agent_push(org_id, gateway_ids, message):
        nachrichten.append((org_id, gateway_ids, message))

    monkeypatch.setattr("app.routers.ws.push_gateway_command", noop)
    monkeypatch.setattr("app.routers.ws.push_agent_message", agent_push)
    gw_id = _admin_client(client, "agent_cancel_admin")
    job_id = _org1_job(gw_id, status=JOB_SENT, delivery=DELIVERY_LEASE, lease_gateway_id=gw_id,
                       lease_token="t")
    antwort = client.post(f"/gateway/jobs/{job_id}/cancel", data={"_csrf": client.cookies.get("ec_csrf")},
                          headers={"HX-Request": "true"})
    assert antwort.status_code == 200
    assert nachrichten == [(1, [gw_id], {"type": "cancel_job", "payload": {"job_id": job_id}})]


def test_historie_zeigt_aufloesen_fuer_unklare_auftraege(client):
    gw_id = _admin_client(client, "agent_historie_admin")
    job_id = _org1_job(gw_id, status=JOB_UNCERTAIN, delivery=DELIVERY_LEASE)
    antwort = client.get(f"/gateway/{gw_id}/historie")
    assert f"/gateway/jobs/{job_id}/resolve" in antwort.text
    assert "Unklar – bitte prüfen" in antwort.text
    assert 'name="_csrf"' in antwort.text


def test_sweep_iteration_meldet_und_ersatzdruckt(monkeypatch):
    from app.services import print_agent_loop

    ersatz, geweckt = [], []

    async def fallback(job_id):
        ersatz.append(job_id)

    async def wecken(org_id, gateway_ids, job_ids):
        geweckt.append(job_ids[0])

    async def broadcast(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.print_dispatcher.dispatch_fallback_for_failed_job", fallback)
    monkeypatch.setattr("app.routers.ws.notify_agents_job_available", wecken)
    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)
    daten = _anlegen(993113)
    alt = _jetzt() - timedelta(seconds=print_agent_service.QUEUE_MAX_WAIT_S + 5)
    wartend = _job(daten, enqueue=False, status=JOB_QUEUED, delivery=DELIVERY_LEASE, queued_at=alt)
    abgelaufen = _job(daten, enqueue=False, status=JOB_SENT, delivery=DELIVERY_LEASE, phase="leased",
                      lease_gateway_id=daten["gateway_id"], lease_token="t",
                      lease_expires_at=_jetzt() - timedelta(seconds=5), attempts=1)
    asyncio.run(print_agent_loop.print_agent_sweep_iteration())
    assert wartend in ersatz
    assert abgelaufen in geweckt
    assert _laden(abgelaufen).status == JOB_QUEUED
