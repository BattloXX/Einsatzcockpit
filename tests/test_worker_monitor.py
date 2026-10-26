import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.security import hash_api_key
from app.models.dienst_monitor import DienstMonitorToken
from app.models.gateway import Gateway, PrintJob
from app.models.master import FireDept
from app.models.sms import SmsDispatchJob
from app.models.worker_runtime import WorkerRuntime
from app.services import gateway_presence as presence
from app.services.sms_outbox import database, ensure
from app.services.worker_monitor import checks, snapshot
from tests.gateway_helpers import bind, cleanup


@pytest.fixture
def runtime_orgs():
    with database() as db:
        orgs = [FireDept(slug=uuid4().hex, name=f"Audit {n}", color="#112233", bos="Feuerwehr") for n in range(2)]
        db.add_all(orgs)
        db.query(WorkerRuntime).delete()
        db.commit()
        ids = [o.id for o in orgs]
    yield ids
    with database() as db:
        db.query(WorkerRuntime).delete()
        db.query(DienstMonitorToken).filter(DienstMonitorToken.org_id.in_(ids)).delete()
        db.query(SmsDispatchJob).filter(SmsDispatchJob.org_id.in_(ids)).delete()
        db.query(FireDept).filter(FireDept.id.in_(ids)).delete()
        db.commit()


def worker(db, name, *, leader=False, age=0, redis=True, loops=None):
    now = presence.utc() - timedelta(seconds=age)
    db.add(WorkerRuntime(worker_id=name, hostname="test-host", pid=123, started_at=now,
                         heartbeat_at=now, expires_at=now + timedelta(seconds=20), is_leader=leader,
                         redis={"configured": True, "connected": redis, "subscriber_connected": redis},
                         loops=loops or {}))


def test_shared_snapshot_fences_tenants_tokens_and_expiry(runtime_orgs):
    first, second = runtime_orgs
    connections = [bind(kind, org)[0] for kind, org in (("sms", first), ("linux", first),
                                                      ("windows_agent", first), ("sms", second), ("linux", second))]
    try:
        now = presence.utc()
        one = ensure(first, "one", "+43660000", "PRIVATE_SMS_CONTENT", ["gateway"])
        two = ensure(first, "two", "+43660001", "PRIVATE_SMS_CONTENT", ["gateway"])
        foreign = ensure(second, "foreign", "+43660002", "FOREIGN_PRIVATE_SMS", ["gateway"])
        with database() as db:
            worker(db, "worker-a", leader=True)
            worker(db, "worker-b", redis=False)
            worker(db, "dead-worker", leader=True, age=60)
            db.get(SmsDispatchJob, one).status = "uncertain"
            job = db.get(SmsDispatchJob, two)
            job.status, job.lease_until = "dispatched", now - timedelta(seconds=1)
            db.get(SmsDispatchJob, foreign).status = "uncertain"
            for org, gateway, status in ((first, connections[1], "sent"), (first, connections[1], "uncertain"),
                                         (second, connections[4], "uncertain")):
                db.add(PrintJob(org_id=org, gateway_id=gateway.gateway_id, document_type="einsatzinfo",
                                idempotency_key=uuid4().hex, status=status, phase="prepared",
                                lease_expires_at=now - timedelta(seconds=1)))
            db.commit()
            data = snapshot(db, first, now)
            assert data["worker_count"] == 2 and data["leader_workers"] == ["worker-a"]
            assert data["redis"]["state"] == "partial"
            assert data["sms_connected"] == 1 and data["print_connected"] == 2
            assert data["sms_jobs"]["open"] == 1 and data["sms_jobs"]["uncertain"] == 1
            assert data["sms_jobs"]["overdue_acks"] == 1
            assert data["print_jobs"]["open"] == 1 and data["print_jobs"]["uncertain"] == 1
            assert data["print_jobs"]["overdue_acks"] == 1
            from app.models.user import SmsGatewayToken
            db.get(SmsGatewayToken, connections[0].gateway_id).token_hash = "rotated-test-hash"
            db.commit()
            assert snapshot(db, first, now)["sms_connected"] == 0
            assert snapshot(db, second, now)["sms_connected"] == 1
            expired = snapshot(db, first, now + timedelta(seconds=100))
            assert expired["worker_count"] == expired["print_connected"] == 0
            assert next(c for c in checks(expired) if c.key == "worker_runtime").state == "down"
    finally:
        for connection in connections:
            cleanup(connection)


def test_hanging_loop_and_offline_queue_are_visible(runtime_orgs):
    org, _ = runtime_orgs
    connection, _ = bind("linux", org)
    try:
        now = presence.utc()
        jid = ensure(org, "stuck", "+43660000", "PRIVATE", ["gateway"])
        with database() as db:
            loops = {"dibos_poll_loop": {"state": "running", "interval_s": 20,
                     "last_started_at": (now - timedelta(minutes=5)).isoformat(),
                     "last_success_at": (now - timedelta(minutes=6)).isoformat()}}
            worker(db, "leader", leader=True, loops=loops)
            db.get(SmsDispatchJob, jid).created_at = now - timedelta(minutes=10)
            db.add(PrintJob(org_id=org, gateway_id=connection.gateway_id, document_type="einsatzinfo",
                            idempotency_key=uuid4().hex, status="queued", erstellt_am=now - timedelta(minutes=10)))
            db.commit()
            data = snapshot(db, org)
            assert data["sms_jobs"]["overdue"] == data["print_jobs"]["overdue"] == 1
            assert data["workers"][0]["loops"][0]["overdue"]
            assert next(c for c in checks(data) if c.key == "worker_runtime").state == "teilweise"
            assert data["last_success_at"] is not None
    finally:
        cleanup(connection)


def test_monitor_api_does_not_expose_payload_or_foreign_gateway(client, runtime_orgs):
    first, second = runtime_orgs
    own, _ = bind("sms", first)
    foreign, _ = bind("linux", second)
    try:
        ensure(first, "private", "+436601234567", "PRIVATE_SMS_BODY", ["gateway"])
        with database() as db:
            worker(db, "leader", leader=True)
            db.get(Gateway, foreign.gateway_id).name = "FOREIGN_GATEWAY_NAME"
            db.add(DienstMonitorToken(org_id=first, label="Test", token_hash=hash_api_key("MONITOR_SECRET"),
                                      created_at=presence.utc()))
            db.commit()
        response = client.get("/health/dienste", headers={"Authorization": "Bearer MONITOR_SECRET"})
        assert response.status_code in (200, 207)
        data = response.json()["runtime"]
        assert data["worker_count"] == data["sms_connected"] == 1
        assert not data["print_connected"]
        assert all(secret not in response.text for secret in
                   ("PRIVATE_SMS_BODY", "+436601234567", "MONITOR_SECRET", "FOREIGN_GATEWAY_NAME"))
    finally:
        cleanup(own)
        cleanup(foreign)


def test_runtime_template_renders_dates_and_worker_details(runtime_orgs):
    from starlette.requests import Request

    from app.core.templating import templates
    from app.models.dienst_monitor import DIENST_LABELS

    org, _ = runtime_orgs
    now = presence.utc()
    with database() as db:
        worker(db, "preview-worker", leader=True, loops={"proof_loop": {
            "state": "ok", "last_success_at": now.isoformat(), "last_started_at": now.isoformat(),
        }})
        db.commit()
        data = snapshot(db, org)
    request = Request({"type": "http", "headers": [], "method": "GET", "scheme": "http",
                       "server": ("localhost", 80), "path": "/admin/systemstatus", "query_string": b""})
    html = templates.env.get_template("admin/_systemstatus_liste.html").render(
        request=request, org=None, runtime=data, checks=checks(data), status_rows={}, dienst_zustaende={},
        labels=DIENST_LABELS)
    assert "preview-worker" in html and "proof_loop" in html and "Uncertain" in html
    assert "noch nicht bestätigt" not in html
    directory = os.environ.get("ECP_PHASE5_PREVIEW_DIR")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        css = (Path.cwd() / "app/static/css/app.css").as_uri()
        (destination / "systemstatus.html").write_text(
            '<!doctype html><html lang="de"><meta charset="utf-8">'
            f'<link rel="stylesheet" href="{css}"><title>Systemstatus Vorschau</title>'
            f'<body><main class="page-content"><h1>Systemstatus</h1>{html}</main></body></html>')
