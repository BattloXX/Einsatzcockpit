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
    ws._print_gateways[org_id] = [live]
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
    ws._print_gateways[org_id] = [live, dead]  # dead = neueste → zuerst versucht
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=2.0)
        assert result["status"] == "done"
        assert dead not in ws._print_gateways[org_id]
        assert live in ws._print_gateways[org_id]
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)


async def test_dispatch_timeout_returns_sent_not_retried():
    """Senden ok, keine Antwort → 'sent' (Gateway spoolt), keine zweite Verbindung."""
    import app.routers.ws as ws
    org_id, job_id = 770004, 44
    a, b = _SilentWS(), _SilentWS()
    ws._print_gateways[org_id] = [a, b]
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=0.1)
        assert result["status"] == "sent"
        assert len(a.sent) + len(b.sent) == 1  # nur eine Verbindung kontaktiert
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)
