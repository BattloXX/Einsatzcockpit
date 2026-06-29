"""Tests für dispatch_sms: Auswahl der lebenden/neuesten Gateway-Verbindung
und Entfernen toter Sockets (Reconnect-Härtung)."""
from __future__ import annotations

import pytest


class _DeadWS:
    """Sendet wirft – simuliert eine tote/halboffene Verbindung."""
    async def send_text(self, payload: str) -> None:
        raise RuntimeError("Socket tot (broken pipe)")


class _SilentWS:
    """Akzeptiert das Senden, antwortet aber nie – simuliert Timeout."""
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


class _LiveWS:
    """Akzeptiert das Senden und löst die zugehörige sms.result-Future sofort auf."""
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        import app.routers.ws as ws
        self.sent.append(payload)
        fut = ws._sms_pending.get(self.job_id)
        if fut and not fut.done():
            fut.set_result({"type": "sms.result", "id": self.job_id, "ok": True})


async def test_dispatch_no_gateway_raises():
    import app.routers.ws as ws
    with pytest.raises(RuntimeError, match="Kein SMS-Gateway"):
        await ws.dispatch_sms(123456, "job-x", "+43660000", "hi")


async def test_dispatch_prunes_dead_and_uses_live():
    """Tote (neueste) Verbindung wird übersprungen und entfernt, lebende genutzt."""
    import app.routers.ws as ws

    org_id, job_id = 990001, "job-prune"
    live = _LiveWS(job_id)
    dead = _DeadWS()
    # dead zuletzt angehängt → "neueste" → wird zuerst versucht, dann verworfen
    ws._sms_gateways[org_id] = [live, dead]
    try:
        result = await ws.dispatch_sms(org_id, job_id, "+43660111", "hallo", timeout=2.0)
        assert result["ok"] is True
        assert live.sent, "lebende Verbindung hätte senden müssen"
        assert dead not in ws._sms_gateways[org_id], "tote Verbindung muss entfernt sein"
        assert live in ws._sms_gateways[org_id], "lebende Verbindung bleibt registriert"
    finally:
        ws._sms_gateways.pop(org_id, None)
        ws._sms_pending.pop(job_id, None)


async def test_dispatch_all_dead_raises_and_empties_registry():
    import app.routers.ws as ws

    org_id, job_id = 990002, "job-alldead"
    ws._sms_gateways[org_id] = [_DeadWS(), _DeadWS()]
    try:
        with pytest.raises(RuntimeError, match="Kein erreichbares"):
            await ws.dispatch_sms(org_id, job_id, "+43660222", "x")
        assert ws._sms_gateways[org_id] == [], "alle toten Sockets entfernt"
    finally:
        ws._sms_gateways.pop(org_id, None)
        ws._sms_pending.pop(job_id, None)


async def test_dispatch_timeout_is_not_retried():
    """Bei Timeout (Senden ok, keine Antwort) wird KEINE zweite Verbindung versucht
    – verhindert doppelten SMS-Versand."""
    import app.routers.ws as ws

    org_id, job_id = 990003, "job-timeout"
    a, b = _SilentWS(), _SilentWS()
    ws._sms_gateways[org_id] = [a, b]
    try:
        with pytest.raises(RuntimeError, match="Timeout"):
            await ws.dispatch_sms(org_id, job_id, "+43660333", "x", timeout=0.1)
        # Nur genau eine Verbindung kontaktiert
        assert len(a.sent) + len(b.sent) == 1
        # Beide Verbindungen bleiben registriert (kein Senden-Fehler → kein Pruning)
        assert a in ws._sms_gateways[org_id] and b in ws._sms_gateways[org_id]
    finally:
        ws._sms_gateways.pop(org_id, None)
        ws._sms_pending.pop(job_id, None)
