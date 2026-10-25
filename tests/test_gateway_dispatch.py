"""ECPG dispatch_print_job: Zustellung an verbundene Gateways, Pruning toter
Sockets, Timeout → 'sent' (kein Doppeldruck). Muster test_sms_gateway_dispatch."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import PrintJob
from tests.gateway_helpers import CaptureSocket, bind, cleanup


def make_job(connection):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        job = PrintJob(
            org_id=connection.org_id,
            gateway_id=connection.gateway_id,
            document_type="einsatzinfo",
            idempotency_key=uuid4().hex,
        )
        db.add(job)
        db.commit()
        return job.id


async def test_dispatch_no_gateway_raises():
    import app.routers.ws as ws

    with pytest.raises(RuntimeError, match="Kein Gateway"):
        await ws.dispatch_print_job(770001, 1, {"job_id": 1})


async def test_dispatch_live_returns_status():
    import app.routers.ws as ws

    connection, socket = bind("linux", socket=CaptureSocket(reply=True))
    job_id = make_job(connection)
    try:
        result = await ws.dispatch_print_job(1, job_id, {"job_id": job_id}, timeout=2)
        assert result["status"] == "done" and len(socket.sent) == 1
    finally:
        cleanup(connection)


async def test_dead_target_never_sends_to_another_gateway():
    import app.routers.ws as ws

    dead, _ = bind("linux", socket=CaptureSocket(dead=True))
    other, other_socket = bind("linux", socket=CaptureSocket(reply=True))
    job_id = make_job(dead)
    try:
        with pytest.raises(RuntimeError):
            await ws.dispatch_print_job(1, job_id, {"job_id": job_id}, timeout=2)
        assert not other_socket.sent
        assert not ws.gateway_online_id(dead.gateway_id, 1)
        assert ws.gateway_online_id(other.gateway_id, 1)
    finally:
        cleanup(dead)
        cleanup(other)


async def test_dispatch_timeout_returns_sent_not_retried():
    import app.routers.ws as ws

    connection, socket = bind("linux")
    other, other_socket = bind("linux")
    job_id = make_job(connection)
    try:
        result = await ws.dispatch_print_job(1, job_id, {"job_id": job_id}, timeout=0.15)
        assert result["status"] == "sent"
        assert len(socket.sent) == 1 and not other_socket.sent
    finally:
        cleanup(connection)
        cleanup(other)


def test_ein_gateway_trennt_anderes_bleibt_verbunden():
    """Offline-Markierung prueft das konkrete Gateway statt die ganze Org."""
    import app.routers.ws as ws
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.gateway import GATEWAY_STATUS_OFFLINE, GATEWAY_STATUS_ONLINE, Gateway

    token_hashes = ["gateway-online-regression-a", "gateway-online-regression-b"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw_a = Gateway(
            org_id=1,
            name="A",
            device_token_hash=token_hashes[0],
            status=GATEWAY_STATUS_ONLINE,
        )
        gw_b = Gateway(
            org_id=1,
            name="B",
            device_token_hash=token_hashes[1],
            status=GATEWAY_STATUS_ONLINE,
        )
        db.add_all([gw_a, gw_b])
        db.commit()
        org_id = 1
        ws_a, ws_b = object(), object()
        ws._print_gateways[org_id] = [(gw_a.id, ws_a), (gw_b.id, ws_b)]
        try:
            ws._discard_print_gateway(org_id, ws_a)
            ws._mark_gateway_offline(gw_a.id)

            db.refresh(gw_a)
            db.refresh(gw_b)
            assert gw_a.status == GATEWAY_STATUS_OFFLINE
            assert gw_b.status == GATEWAY_STATUS_ONLINE
            assert ws.connected_print_gateway_ids(org_id) == {gw_b.id}
        finally:
            ws._print_gateways.pop(org_id, None)
    finally:
        db.query(Gateway).filter(
            Gateway.org_id == 1,
            Gateway.device_token_hash.in_(token_hashes),
        ).delete()
        db.commit()
        db.close()


def test_job_status_log_contains_gateway_context(caplog):
    import logging

    import app.routers.ws as ws

    with caplog.at_level(logging.INFO, logger="einsatzleiter.ws"):
        ws._log_gateway_job_status(42, "done", 7, 9, None)

    assert "job_id=42" in caplog.text
    assert "status=done" in caplog.text
    assert "org_id=7" in caplog.text
    assert "gateway_id=9" in caplog.text


async def test_dispatch_job_commits_sent_before_await_no_status_overwrite(monkeypatch):
    """Race-Fix: print_dispatcher.dispatch_job committet attempts+'sent' VOR dem Await
    und schreibt den Status danach NICHT erneut – der Laufzeitstatus (printing/done)
    gehört dem Gateway-Callback _apply_job_status. Ein zweiter Commit hier kollidierte
    sonst auf der print_job-Zeile (MariaDB 1020 „Record has changed since last read")."""
    import uuid

    import app.routers.ws as ws
    import app.services.print_dispatcher as pd
    from app.core.tenant import set_tenant_context
    from app.models.gateway import JOB_QUEUED, JOB_SENT, Gateway, Printer, PrintJob
    from tests.conftest import TestingSession

    db = TestingSession()
    set_tenant_context(db, None)
    gateway = Gateway(org_id=1, name="Legacy-Race-" + uuid.uuid4().hex)
    db.add(gateway)
    db.flush()
    printer = Printer(org_id=1, gateway_id=gateway.id, name="Legacy-Ziel", uri="ipp://example.test")
    db.add(printer)
    db.flush()
    job = PrintJob(
        org_id=1,
        gateway_id=gateway.id,
        printer_id=printer.id,
        document_type="einsatzinfo",
        idempotency_key="race-" + uuid.uuid4().hex,
        status=JOB_QUEUED,
    )
    db.add(job)
    db.commit()

    async def fake_dispatch(org_id, job_id, payload, timeout=20.0):  # noqa: ASYNC109
        return {"job_id": job_id, "status": "done"}

    monkeypatch.setattr(ws, "dispatch_print_job", fake_dispatch)
    monkeypatch.setattr("app.services.print_artifact_service.artifact_url", lambda j: "http://x/a")

    try:
        result = await pd.dispatch_job(db, job)
        db.refresh(job)
        assert job.status == JOB_SENT  # NICHT 'done' – das schreibt der Callback
        assert job.attempts == 1
        assert result["status"] == "done"  # Rückgabe für den Aufrufer bleibt erhalten
    finally:
        db.close()


def test_apply_job_status_guards_transitions():
    import uuid

    import app.routers.ws as ws
    from app.core.tenant import set_tenant_context
    from app.models.gateway import PrintJob
    from tests.conftest import TestingSession

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        jobs = []
        for status in ("sent", "done", "canceled"):
            job = PrintJob(
                org_id=880001,
                gateway_id=1,
                document_type="einsatzinfo",
                status=status,
                idempotency_key=f"status-{uuid.uuid4().hex}",
            )
            db.add(job)
            jobs.append(job)
        db.commit()
        ids = [job.id for job in jobs]
        assert ws._apply_job_status(ids[0], "printing", None) == 880001
        assert ws._apply_job_status(ids[1], "printing", None) is None
        assert ws._apply_job_status(ids[2], "failed", "x") is None
        assert ws._apply_job_status(ids[0], "unbekannt", None) is None
        assert ws._apply_job_status(999999999, "printing", None) is None
        db.expire_all()
        assert db.get(PrintJob, ids[0]).status == "printing"
        assert db.get(PrintJob, ids[1]).status == "done"
        assert db.get(PrintJob, ids[2]).status == "canceled"
    finally:
        db.rollback()
        db.close()
