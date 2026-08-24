"""Tests für dispatch_sms: Auswahl der lebenden/neuesten Gateway-Verbindung
und Entfernen toter Sockets (Reconnect-Härtung)."""
from __future__ import annotations

import pytest

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import SmsGatewayToken


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
    ws._sms_gateways[org_id] = [(1, live), (2, dead)]
    try:
        result = await ws.dispatch_sms(org_id, job_id, "+43660111", "hallo", timeout=2.0)
        assert result["ok"] is True
        assert result["gateway_token_id"] == 1
        assert live.sent, "lebende Verbindung hätte senden müssen"
        assert (2, dead) not in ws._sms_gateways[org_id], "tote Verbindung muss entfernt sein"
        assert (1, live) in ws._sms_gateways[org_id], "lebende Verbindung bleibt registriert"
    finally:
        ws._sms_gateways.pop(org_id, None)
        ws._sms_pending.pop(job_id, None)


async def test_dispatch_all_dead_raises_and_empties_registry():
    import app.routers.ws as ws

    org_id, job_id = 990002, "job-alldead"
    ws._sms_gateways[org_id] = [(1, _DeadWS()), (2, _DeadWS())]
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
    ws._sms_gateways[org_id] = [(1, a), (2, b)]
    try:
        with pytest.raises(RuntimeError, match="Timeout"):
            await ws.dispatch_sms(org_id, job_id, "+43660333", "x", timeout=0.1)
        # Nur genau eine Verbindung kontaktiert
        assert len(a.sent) + len(b.sent) == 1
        # Beide Verbindungen bleiben registriert (kein Senden-Fehler → kein Pruning)
        assert (1, a) in ws._sms_gateways[org_id] and (2, b) in ws._sms_gateways[org_id]
    finally:
        ws._sms_gateways.pop(org_id, None)
        ws._sms_pending.pop(job_id, None)


async def test_dispatch_respects_configured_priority():
    """Die konfigurierte Prioritaet gewinnt gegen die Verbindungsreihenfolge."""
    import app.routers.ws as ws

    org_id, job_id = 990004, "job-priority"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        bevorzugt = SmsGatewayToken(
            label="Bevorzugt", token_hash=hash_api_key("priority-preferred"),
            org_id=org_id, priority=10,
        )
        fallback = SmsGatewayToken(
            label="Fallback", token_hash=hash_api_key("priority-fallback"),
            org_id=org_id, priority=200,
        )
        db.add_all([bevorzugt, fallback])
        db.commit()
        db.refresh(bevorzugt)
        db.refresh(fallback)

        preferred_ws = _LiveWS(job_id)
        fallback_ws = _SilentWS()
        # Fallback zuletzt verbunden: ohne Prioritaet wuerde er zuerst angesprochen.
        ws._sms_gateways[org_id] = [
            (bevorzugt.id, preferred_ws),
            (fallback.id, fallback_ws),
        ]
        result = await ws.dispatch_sms(org_id, job_id, "+43660444", "priorisiert", timeout=2.0)

        assert result["ok"] is True
        assert preferred_ws.sent
        assert fallback_ws.sent == []
    finally:
        ws._sms_gateways.pop(org_id, None)
        ws._sms_pending.pop(job_id, None)
        for gateway in db.query(SmsGatewayToken).filter(SmsGatewayToken.org_id == org_id).all():
            db.delete(gateway)
        db.commit()
        db.close()
