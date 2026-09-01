"""Tests der externen Nachrichten-API und ihres Dispatch-Loops."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.api_message import ApiMessage, ApiMessageRecipient
from app.models.mailing import MailingSuppressionEntry
from app.models.master import FireDept, Member
from app.models.sms import SmsGroup, SmsGroupMember, SmsLog, SmsLogRecipient
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
        db.add(
            ApiKey(
                key_hash=hash_api_key(raw),
                label="Messaging Test",
                org_id=org.id,
                scopes="sms:send,mail:send",
            )
        )
        db.commit()
        return raw, org.id
    finally:
        db.close()


def _sms_payload(key: str | None = None, numbers: list[str] | None = None):
    return {
        "Key": key or f"sms-{uuid.uuid4().hex}",
        "text": "Probealarm",
        "empfaenger": {"nummern": numbers or ["+436641234567"]},
    }


def _mail_payload(key: str | None = None, addresses: list[str] | None = None):
    return {
        "Key": key or f"mail-{uuid.uuid4().hex}",
        "betreff": "Probe",
        "text": "Nur ein Test",
        "empfaenger": {"adressen": addresses or ["a@example.at"]},
    }


def test_auth_und_scopes(client, setup_db):
    assert client.post("/api/v1/sms", json=_sms_payload()).status_code == 422
    assert client.post("/api/v1/sms", json=_sms_payload(), headers={"X-API-Key": "ungueltig"}).status_code == 401
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Ohne Scope", org_id=org.id))
        db.commit()
    finally:
        db.close()
    assert client.post("/api/v1/sms", json=_sms_payload(), headers={"X-API-Key": raw}).status_code == 403


def test_sms_aufloesung_deduplizierung_ablehnung_und_idempotenz(
    client,
    messaging_key,
    monkeypatch,
):
    raw, org_id = messaging_key
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        member = Member(org_id=org_id, firstname="Max", lastname="Muster", phone="00436641234567", active=True)
        group = SmsGroup(org_id=org_id, name=f"API {uuid.uuid4().hex}")
        db.add_all([member, group])
        db.flush()
        db.add(SmsGroupMember(sms_group_id=group.id, member_id=member.id))
        db.commit()
        member_id, group_id = member.id, group.id
    finally:
        db.close()
    payload = _sms_payload(numbers=["+436641234567", "0664 abc"])
    payload["empfaenger"].update({"gruppen_ids": [group_id], "mitglieder_ids": [member_id]})
    response = client.post("/api/v1/sms", json=payload, headers={"X-API-Key": raw})
    assert response.status_code == 202, response.text
    data = response.json()
    assert data["empfaenger_anzahl"] == 1
    assert data["abgelehnt"] == [{"wert": "0664 abc", "grund": "ungueltige_nummer"}]
    again = client.post("/api/v1/sms", json=payload, headers={"X-API-Key": raw})
    assert again.status_code == 202
    assert again.json()["idempotent_hit"] is True
    assert again.json()["id"] == data["id"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(ApiMessageRecipient).filter_by(message_id=data["id"]).count() == 1
        assert db.query(SmsLog).filter_by(source="api").count() >= 1
    finally:
        db.close()


def test_alle_sms_ungueltig_422_und_provider_fehlt_409(client, messaging_key, monkeypatch):
    raw, _ = messaging_key
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    assert client.post("/api/v1/sms", json=_sms_payload(numbers=["abc"]), headers={"X-API-Key": raw}).status_code == 422
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: False)
    assert client.post("/api/v1/sms", json=_sms_payload(), headers={"X-API-Key": raw}).status_code == 409


def test_empfaenger_und_tageslimit(client, messaging_key, monkeypatch):
    raw, _ = messaging_key
    from app.config import settings

    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    monkeypatch.setattr(settings, "API_MESSAGE_MAX_RECIPIENTS", 1)
    assert (
        client.post(
            "/api/v1/sms",
            json=_sms_payload(numbers=["+436641111111", "+436642222222"]),
            headers={"X-API-Key": raw},
        ).status_code
        == 422
    )
    monkeypatch.setattr(settings, "API_MESSAGE_MAX_RECIPIENTS", 200)
    monkeypatch.setattr(settings, "API_SMS_DAILY_LIMIT", 1)
    assert client.post("/api/v1/sms", json=_sms_payload(), headers={"X-API-Key": raw}).status_code == 429


def test_mail_suppression_idempotenz_und_keine_config(client, messaging_key, monkeypatch):
    raw, org_id = messaging_key
    blocked_email = f"blocked-{uuid.uuid4().hex}@example.at"
    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_: True)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(MailingSuppressionEntry(org_id=org_id, email=blocked_email, reason="complaint"))
        db.commit()
    finally:
        db.close()
    payload = _mail_payload(addresses=[blocked_email, "ok@example.at"])
    response = client.post("/api/v1/mail", json=payload, headers={"X-API-Key": raw})
    assert response.status_code == 202, response.text
    message_id = response.json()["id"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        statuses = dict(
            db.query(ApiMessageRecipient.ziel, ApiMessageRecipient.status).filter_by(message_id=message_id).all()
        )
        assert statuses == {blocked_email: "suppressed", "ok@example.at": "queued"}
    finally:
        db.close()
    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_: False)
    assert client.post("/api/v1/mail", json=_mail_payload(), headers={"X-API-Key": raw}).status_code == 409


@pytest.mark.asyncio
async def test_sms_dispatch_schreibt_status_und_protokoll(messaging_key, monkeypatch):
    _, org_id = messaging_key
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        key = db.query(ApiKey).filter(ApiKey.org_id == org_id, ApiKey.label == "Messaging Test").first()
        log = SmsLog(org_id=org_id, source="api", text="x", recipient_count=2)
        db.add(log)
        db.flush()
        message = ApiMessage(
            org_id=org_id,
            api_key_id=key.id,
            external_key=f"dispatch-{uuid.uuid4().hex}",
            kanal="sms",
            body_text="x",
            recipient_count=2,
            sms_log_id=log.id,
        )
        db.add(message)
        db.flush()
        db.add_all(
            [
                ApiMessageRecipient(org_id=org_id, message_id=message.id, ziel="+436641111111"),
                ApiMessageRecipient(org_id=org_id, message_id=message.id, ziel="+436642222222"),
            ]
        )
        db.commit()
        message_id = message.id

        async def fake_bulk(org, jobs, ctx, callback):
            results = []
            for index, (phone, _) in enumerate(jobs):
                result = SmsSendResult(phone, index == 0, datetime.now(UTC).replace(tzinfo=None), "gateway", None)
                await callback(result)
                results.append(result)
            return results

        monkeypatch.setattr("app.services.api_message_dispatch_loop._send_bulk_with_progress", fake_bulk)
        await dispatch_once(db)
        db.expire_all()
        result = db.get(ApiMessage, message_id)
        assert result.status == "partial"
        assert (result.success_count, result.failed_count) == (1, 1)
        assert db.query(SmsLogRecipient).filter_by(sms_log_id=log.id).count() == 2
        assert all(r.attempt_count == 1 for r in result.recipients)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_mail_retry_und_status_get(client, messaging_key, monkeypatch):
    raw, org_id = messaging_key
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        key = db.query(ApiKey).filter(ApiKey.org_id == org_id, ApiKey.label == "Messaging Test").first()
        message = ApiMessage(
            org_id=org_id,
            api_key_id=key.id,
            external_key=f"mail-dispatch-{uuid.uuid4().hex}",
            kanal="mail",
            betreff="x",
            body_text="x",
            recipient_count=1,
        )
        db.add(message)
        db.flush()
        db.add(ApiMessageRecipient(org_id=org_id, message_id=message.id, ziel="retry@example.at", max_attempts=3))
        db.commit()
        message_id = message.id
        monkeypatch.setattr(
            "app.services.api_message_dispatch_loop.mail_service.deliver",
            AsyncMock(side_effect=RuntimeError("kaputt")),
        )
        await dispatch_once(db)
        db.expire_all()
        recipient = db.query(ApiMessageRecipient).filter_by(message_id=message_id).one()
        assert recipient.status == "queued" and recipient.next_attempt_at is not None
        assert db.get(ApiMessage, message_id).status == "queued"
    finally:
        db.close()
    response = client.get(f"/api/v1/nachricht/{message_id}", headers={"X-API-Key": raw})
    assert response.status_code == 200
    assert response.json()["erstellt_am"].endswith("Z")
