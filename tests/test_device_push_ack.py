from datetime import UTC, datetime
from uuid import uuid4

from app.core.security import hash_api_key, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import DeviceToken, FcmDeliveryLog, FcmToken, PushLog, User


def _create_delivery():
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    try:
        org = FireDept(slug=f"ack-{suffix}", name=f"Ack Org {suffix}")
        db.add(org)
        db.flush()
        user = User(username=f"ack-{suffix}", display_name="Ack", active=True, org_id=org.id)
        db.add(user)
        db.flush()
        raw_token = f"ack-raw-{suffix}"
        db.add(DeviceToken(
            user_id=user.id,
            token_hash=hash_api_key(raw_token),
            label="Ack Gerät",
        ))
        fcm_token = FcmToken(user_id=user.id, token=f"ack-fcm-{suffix}")
        push_log = PushLog(title="Ack", body="Test", source="test", org_id=org.id)
        db.add_all([fcm_token, push_log])
        db.flush()
        delivery = FcmDeliveryLog(
            push_log_id=push_log.id,
            fcm_token_id=fcm_token.id,
            user_id=user.id,
            sent_at=datetime.now(UTC).replace(tzinfo=None),
            success=True,
        )
        db.add(delivery)
        db.commit()
        return user.id, raw_token, delivery.id
    finally:
        db.close()


def test_push_ack_per_bearer_ist_idempotent(client, setup_db):
    _, raw_token, delivery_id = _create_delivery()
    headers = {"Authorization": f"Bearer {raw_token}"}

    first = client.post("/api/v1/device/push-ack", json={"delivery_id": str(delivery_id)}, headers=headers)
    assert first.status_code == 200
    assert first.json() == {"ok": True}
    db = SessionLocal()
    try:
        delivered_at = db.get(FcmDeliveryLog, delivery_id).delivered_at
        assert delivered_at is not None
        assert db.get(FcmDeliveryLog, delivery_id).fcm_token.last_ack_at == delivered_at
    finally:
        db.close()

    second = client.post("/api/v1/device/push-ack", json={"delivery_id": delivery_id}, headers=headers)
    assert second.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(FcmDeliveryLog, delivery_id).delivered_at == delivered_at
        assert db.get(FcmDeliveryLog, delivery_id).fcm_token.last_ack_at == delivered_at
    finally:
        db.close()


def test_push_ack_cookie_fallback_funktioniert(client, setup_db):
    user_id, _, delivery_id = _create_delivery()
    client.cookies.set("session", sign_session(user_id))

    response = client.post("/api/v1/device/push-ack", json={"delivery_id": delivery_id})

    assert response.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(FcmDeliveryLog, delivery_id).delivered_at is not None
    finally:
        db.close()


def test_push_ack_fremde_id_wird_diagnostizierbar_abgelehnt(client, setup_db):
    _, owner_token, owner_delivery_id = _create_delivery()
    _, attacker_token, _ = _create_delivery()

    response = client.post(
        "/api/v1/device/push-ack",
        json={"delivery_id": owner_delivery_id},
        headers={"Authorization": f"Bearer {attacker_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "reason": "unknown_delivery"}
    db = SessionLocal()
    try:
        assert db.get(FcmDeliveryLog, owner_delivery_id).delivered_at is None
    finally:
        db.close()


def test_push_ack_unbekannte_id_wird_diagnostizierbar_abgelehnt(client, setup_db, caplog):
    _, raw_token, _ = _create_delivery()

    response = client.post(
        "/api/v1/device/push-ack",
        json={"delivery_id": 999999999},
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "reason": "unknown_delivery"}

    assert "unknown_delivery" in caplog.text
    assert raw_token not in caplog.text
    assert "999999999" not in caplog.text


def test_neue_delivery_aktualisiert_ack_aber_wiederholung_nicht(client, setup_db):
    _, raw_token, delivery_id = _create_delivery()
    headers = {"Authorization": f"Bearer {raw_token}"}
    response = client.post("/api/v1/device/push-ack", json={"delivery_id": delivery_id}, headers=headers)
    assert response.json() == {"ok": True}
    with SessionLocal() as db:
        original = db.get(FcmDeliveryLog, delivery_id)
        earlier = original.delivered_at
        second = FcmDeliveryLog(
            push_log_id=original.push_log_id, fcm_token_id=original.fcm_token_id,
            user_id=original.user_id, success=True,
        )
        db.add(second)
        db.commit()
        second_id = second.id
    response = client.post("/api/v1/device/push-ack", json={"delivery_id": second_id}, headers=headers)
    assert response.json() == {"ok": True}
    with SessionLocal() as db:
        second = db.get(FcmDeliveryLog, second_id)
        latest_ack = second.delivered_at
        assert latest_ack >= earlier
        assert second.fcm_token.last_ack_at == latest_ack
    response = client.post("/api/v1/device/push-ack", json={"delivery_id": delivery_id}, headers=headers)
    assert response.json() == {"ok": True}
    with SessionLocal() as db:
        assert db.get(FcmDeliveryLog, delivery_id).fcm_token.last_ack_at == latest_ack
