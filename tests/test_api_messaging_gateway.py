"""Tests für den synchronen Uptime-Kuma-SMS-Gateway-Endpunkt."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.api_message import ApiMessage, ApiMessageRecipient
from app.models.master import FireDept
from app.models.sms import SmsLog, SmsLogRecipient
from app.models.user import ApiKey
from app.services.api_message_dispatch_loop import dispatch_once
from app.services.sms_dispatch_service import SmsSendResult


@pytest.fixture
def messaging_key(setup_db):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Gateway Test", org_id=org.id, scopes="sms:send"))
        db.commit()
        return raw, org.id
    finally:
        db.close()


def _post(client, raw: str, *, to: str = "+436641234567", body: str = "Probealarm"):
    return client.post(
        "/api/v1/sms/send",
        json={"to": to, "body": body},
        headers={"X-API-Key": raw},
    )


def _mock_dispatch(monkeypatch, outcomes: list[bool]) -> None:
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)

    async def fake_bulk(org, jobs, ctx, callback):
        results = []
        for (phone, _), success in zip(jobs, outcomes, strict=True):
            result = SmsSendResult(
                phone, success, datetime.now(UTC).replace(tzinfo=None), "gateway", "Test",
            )
            await callback(result)
            results.append(result)
        return results

    monkeypatch.setattr(
        "app.services.api_message_dispatch_loop._send_bulk_with_progress", fake_bulk,
    )


def test_gateway_alle_empfaenger_erfolgreich(client, messaging_key, monkeypatch):
    raw, org_id = messaging_key
    _mock_dispatch(monkeypatch, [True, True])
    response = _post(client, raw, to=" +436641111111 , +436642222222 ")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert response.json()["gesendet"] == 2
    assert response.json()["fehlgeschlagen"] == 0

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        message = db.get(ApiMessage, response.json()["id"])
        assert message.org_id == org_id
        assert message.status == "sent"
        log = db.get(SmsLog, message.sms_log_id)
        assert log.source == "api"
        assert db.query(SmsLogRecipient).filter_by(sms_log_id=log.id).count() == 2
    finally:
        db.close()


@pytest.mark.parametrize(
    ("outcomes", "expected_status"),
    [([True, False], "partial"), ([False, False], "failed")],
)
def test_gateway_versandfehler_bleiben_http_200(
    client, messaging_key, monkeypatch, outcomes, expected_status,
):
    raw, _ = messaging_key
    _mock_dispatch(monkeypatch, outcomes)
    response = _post(client, raw, to="+436641111111,+436642222222")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "SMS-Versand fehlgeschlagen"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ApiMessage, response.json()["id"]).status == expected_status
    finally:
        db.close()


def test_gateway_trimmt_und_dedupliziert(client, messaging_key, monkeypatch):
    raw, _ = messaging_key
    _mock_dispatch(monkeypatch, [True])
    response = _post(client, raw, to=" +436641111111 , +436641111111 ")
    assert response.status_code == 200
    assert response.json()["gesendet"] == 1
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(ApiMessageRecipient).filter_by(message_id=response.json()["id"]).count() == 1
    finally:
        db.close()


def test_gateway_validierung(client, messaging_key, monkeypatch):
    raw, _ = messaging_key
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    assert _post(client, raw, to="abc, 0664").status_code == 422
    assert _post(client, raw, body=" ").status_code == 422
    monkeypatch.setattr(settings, "API_MESSAGE_MAX_BODY_CHARS", 3)
    assert _post(client, raw, body="vier").status_code == 422


def test_gateway_sync_empfaengerlimit(client, messaging_key, monkeypatch):
    raw, _ = messaging_key
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    monkeypatch.setattr(settings, "API_SMS_SYNC_MAX_RECIPIENTS", 1)
    response = _post(client, raw, to="+436641111111,+436642222222")
    assert response.status_code == 422
    assert "POST /api/v1/sms" in response.json()["detail"]


def test_gateway_auth(client, setup_db):
    payload = {"to": "+436641234567", "body": "Probealarm"}
    assert client.post("/api/v1/sms/send", json=payload).status_code == 422
    assert client.post(
        "/api/v1/sms/send", json=payload, headers={"X-API-Key": "ungueltig"},
    ).status_code == 401
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Ohne Scope", org_id=org.id))
        db.commit()
    finally:
        db.close()
    assert client.post(
        "/api/v1/sms/send", json=payload, headers={"X-API-Key": raw},
    ).status_code == 403


def test_gateway_provider_und_tageslimit(client, messaging_key, monkeypatch):
    raw, _ = messaging_key
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: False)
    assert _post(client, raw).status_code == 409
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    monkeypatch.setattr(settings, "API_SMS_DAILY_LIMIT", 0)
    monkeypatch.setattr(
        "app.services.api_message_service.enforce_recipient_limits",
        lambda *_: (_ for _ in ()).throw(__import__("fastapi").HTTPException(429, "Limit")),
    )
    assert _post(client, raw).status_code == 429


@pytest.mark.asyncio
async def test_gateway_auftrag_wird_nicht_erneut_dispatcht(
    client, messaging_key, monkeypatch,
):
    raw, _ = messaging_key
    _mock_dispatch(monkeypatch, [True])
    response = _post(client, raw)
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert await dispatch_once(db) == 0
    finally:
        db.close()
