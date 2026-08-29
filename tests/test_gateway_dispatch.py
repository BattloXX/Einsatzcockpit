"""ECPG dispatch_print_job: Zustellung an verbundene Gateways, Pruning toter
Sockets, Timeout → 'sent' (kein Doppeldruck). Muster test_sms_gateway_dispatch."""
from __future__ import annotations

import pytest


class _DeadWS:
    async def send_text(self, payload: str) -> None:
        raise RuntimeError("Socket tot")


class _SilentWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


class _LiveWS:
    """Löst die zugehörige job_status-Future sofort mit 'done' auf."""
    def __init__(self, job_id: int) -> None:
        self.key = str(job_id)
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        import app.routers.ws as ws
        self.sent.append(payload)
        fut = ws._job_pending.get(self.key)
        if fut and not fut.done():
            fut.set_result({"job_id": int(self.key), "status": "done"})


async def test_dispatch_no_gateway_raises():
    import app.routers.ws as ws
    with pytest.raises(RuntimeError, match="Kein Gateway"):
        await ws.dispatch_print_job(770001, 1, {"job_id": 1})


async def test_dispatch_live_returns_status():
    import app.routers.ws as ws
    org_id, job_id = 770002, 42
    live = _LiveWS(job_id)
    ws._print_gateways[org_id] = [(1, live)]
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=2.0)
        assert result["status"] == "done"
        assert live.sent, "Gateway hätte senden müssen"
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)


async def test_dispatch_prunes_dead_and_uses_live():
    import app.routers.ws as ws
    org_id, job_id = 770003, 43
    live = _LiveWS(job_id)
    dead = _DeadWS()
    ws._print_gateways[org_id] = [(1, live), (2, dead)]  # dead = neueste → zuerst versucht
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=2.0)
        assert result["status"] == "done"
        assert (2, dead) not in ws._print_gateways[org_id]
        assert (1, live) in ws._print_gateways[org_id]
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)


async def test_dispatch_timeout_returns_sent_not_retried():
    """Senden ok, keine Antwort → 'sent' (Gateway spoolt), keine zweite Verbindung."""
    import app.routers.ws as ws
    org_id, job_id = 770004, 44
    a, b = _SilentWS(), _SilentWS()
    ws._print_gateways[org_id] = [(1, a), (2, b)]
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=0.1)
        assert result["status"] == "sent"
        assert len(a.sent) + len(b.sent) == 1  # nur eine Verbindung kontaktiert
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)


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
    from app.models.gateway import JOB_QUEUED, JOB_SENT, PrintJob
    from tests.conftest import TestingSession

    db = TestingSession()
    set_tenant_context(db, None)
    job = PrintJob(org_id=1, gateway_id=1, printer_id=1, document_type="einsatzinfo",
                   idempotency_key="race-" + uuid.uuid4().hex, status=JOB_QUEUED)
    db.add(job)
    db.commit()

    async def fake_dispatch(org_id, job_id, payload, timeout=20.0):
        return {"job_id": job_id, "status": "done"}
    monkeypatch.setattr(ws, "dispatch_print_job", fake_dispatch)
    monkeypatch.setattr("app.services.print_artifact_service.artifact_url", lambda j: "http://x/a")

    try:
        result = await pd.dispatch_job(db, job)
        db.refresh(job)
        assert job.status == JOB_SENT       # NICHT 'done' – das schreibt der Callback
        assert job.attempts == 1
        assert result["status"] == "done"   # Rückgabe für den Aufrufer bleibt erhalten
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
                org_id=880001, gateway_id=1, document_type="einsatzinfo", status=status,
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
