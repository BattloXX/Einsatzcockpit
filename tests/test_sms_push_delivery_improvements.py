import asyncio
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import FcmDeliveryLog, FcmToken, PushLog, User
from app.services import push_service, sms_dispatch_service


@pytest.mark.asyncio
async def test_sms_bulk_distributes_and_limits_concurrency(monkeypatch):
    active = {11: 0, 22: 0}
    maximum = {11: 0, 22: 0}
    assigned: list[int] = []

    monkeypatch.setattr(
        "app.routers.ws.connected_gateway_token_ids_ordered",
        lambda _org_id: [11, 22],
    )

    async def fake_send_sms(_org_id, _to, _text, ctx=None, preferred_gateway_token_id=None):
        gateway_id = preferred_gateway_token_id
        assigned.append(gateway_id)
        active[gateway_id] += 1
        maximum[gateway_id] = max(maximum[gateway_id], active[gateway_id])
        await asyncio.sleep(0.01)
        active[gateway_id] -= 1
        return True

    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send_sms)
    ctx = SimpleNamespace(chain=["gateway"], providers_used=set())
    jobs = [(f"+4366000{index}", "Test") for index in range(16)]

    results = await sms_dispatch_service.send_bulk_detailed(1, jobs, ctx=ctx)

    assert all(result.success for result in results)
    assert assigned.count(11) == assigned.count(22) == 8
    assert maximum[11] <= sms_dispatch_service.SMS_CONCURRENCY_PER_GATEWAY
    assert maximum[22] <= sms_dispatch_service.SMS_CONCURRENCY_PER_GATEWAY


def test_fcm_not_configured_is_logged_for_every_push(monkeypatch):
    db = SessionLocal()
    set_tenant_context(db, None)
    user = User(
        username="fcm_delivery_not_configured",
        display_name="FCM Delivery Test",
        org_id=1,
        active=True,
    )
    db.add(user)
    db.flush()
    token = FcmToken(user_id=user.id, token="fcm-delivery-not-configured-token")
    db.add(token)
    db.flush()
    monkeypatch.setattr(push_service, "_get_fcm_app", lambda _cfg=None: None)

    try:
        for index in range(2):
            push_log = PushLog(title=f"Alarm {index}", body="Test", source="test", org_id=1)
            db.add(push_log)
            db.flush()
            assert push_service._notify_fcm_users(
                db,
                {user.id},
                push_log.title,
                push_log.body,
                None,
                {},
                push_log_id=push_log.id,
            ) == 0
        db.commit()

        deliveries = db.query(FcmDeliveryLog).filter(FcmDeliveryLog.user_id == user.id).all()
        assert [delivery.error_code for delivery in deliveries] == [
            "fcm_not_configured",
            "fcm_not_configured",
        ]
    finally:
        db.rollback()
        for delivery in db.query(FcmDeliveryLog).filter(FcmDeliveryLog.user_id == user.id).all():
            db.delete(delivery)
        for push_log in db.query(PushLog).filter(PushLog.source == "test").all():
            db.delete(push_log)
        db.delete(token)
        db.delete(user)
        db.commit()
        db.close()
