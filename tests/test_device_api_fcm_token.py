from datetime import datetime

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceToken, FcmDeliveryLog, FcmToken, PushLog, User


def _make_device_user(username: str) -> tuple[int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            active=True,
            is_device=True,
        )
        db.add(user)
        db.flush()
        device = DeviceToken(
            user_id=user.id,
            token_hash=hash_api_key(f"raw-{username}"),
            label=f"Gerät {username}",
        )
        db.add(device)
        db.commit()
        return user.id, device.id
    finally:
        db.close()


def _authenticate(client, user_id: int, device_id: int) -> None:
    client.cookies.set(
        "session", sign_session(user_id, device=True, device_token_id=device_id)
    )


def test_fcm_token_post_upsert_mit_device_verknuepfung(client, setup_db):
    user_id, device_id = _make_device_user("fcm_api_post")
    _authenticate(client, user_id, device_id)

    response = client.post(
        "/api/v1/device/fcm-token",
        json={"token": "fcm-api-token", "platform": "android"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(FcmToken).filter_by(token="fcm-api-token").one()
        assert row.user_id == user_id
        assert row.device_token_id == device_id
    finally:
        db.close()


def test_fcm_token_post_upsert_per_bearer_token(client, setup_db):
    user_id, device_id = _make_device_user("fcm_api_bearer")

    response = client.post(
        "/api/v1/device/fcm-token",
        json={"token": "fcm-api-bearer-token", "platform": "android"},
        headers={"Authorization": "Bearer raw-fcm_api_bearer"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(FcmToken).filter_by(token="fcm-api-bearer-token").one()
        assert row.user_id == user_id
        assert row.device_token_id == device_id
    finally:
        db.close()


def test_fcm_token_delete_loescht_nur_eigene_tokens(client, setup_db):
    owner_id, owner_device_id = _make_device_user("fcm_api_owner")
    other_id, _ = _make_device_user("fcm_api_other")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add_all([
            FcmToken(user_id=owner_id, device_token_id=owner_device_id,
                     token="fcm-api-own", platform="android"),
            FcmToken(user_id=other_id, token="fcm-api-foreign", platform="android"),
        ])
        db.commit()
    finally:
        db.close()

    _authenticate(client, owner_id, owner_device_id)
    assert client.request(
        "DELETE", "/api/v1/device/fcm-token", json={"token": "fcm-api-own"}
    ).status_code == 200
    assert client.request(
        "DELETE", "/api/v1/device/fcm-token", json={"token": "fcm-api-foreign"}
    ).status_code == 200

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(FcmToken).filter_by(token="fcm-api-own").first() is None
        assert db.query(FcmToken).filter_by(token="fcm-api-foreign").first() is not None
    finally:
        db.close()


def test_fcm_token_api_ohne_session_antwortet_401(client, setup_db):
    assert client.post(
        "/api/v1/device/fcm-token", json={"token": "fcm-unauthenticated"}
    ).status_code == 401
    assert client.request(
        "DELETE", "/api/v1/device/fcm-token", json={"token": "fcm-unauthenticated"}
    ).status_code == 401


def _create_fcm_token(
    user_id: int, token: str, created_at: datetime
) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fcm_token = FcmToken(
            user_id=user_id,
            token=token,
            platform="android",
            created_at=created_at,
        )
        db.add(fcm_token)
        db.commit()
        return fcm_token.id
    finally:
        db.close()


def test_fcm_token_status_registriert_ohne_zustellung(client, setup_db):
    user_id, device_id = _make_device_user("fcm_status_registered")
    created_at = datetime(2026, 8, 24, 10, 11, 12)
    _create_fcm_token(user_id, "fcm-status-registered", created_at)
    _authenticate(client, user_id, device_id)

    response = client.get(
        "/api/v1/device/fcm-token/status",
        params={"token": "fcm-status-registered"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "registered": True,
        "registered_at": created_at.isoformat() + "Z",
        "last_delivery_success": None,
        "last_delivery_at": None,
    }


def test_fcm_token_status_verraet_fremden_token_nicht(client, setup_db):
    user_id, device_id = _make_device_user("fcm_status_requester")
    other_id, _ = _make_device_user("fcm_status_other")
    _create_fcm_token(
        other_id, "fcm-status-foreign", datetime(2026, 8, 24, 11, 12, 13)
    )
    _authenticate(client, user_id, device_id)

    for token in ("fcm-status-foreign", "fcm-status-missing"):
        response = client.get(
            "/api/v1/device/fcm-token/status", params={"token": token}
        )
        assert response.status_code == 200
        assert response.json() == {"registered": False}


def test_fcm_token_status_mit_letzter_zustellung(client, setup_db):
    user_id, device_id = _make_device_user("fcm_status_delivery")
    created_at = datetime(2026, 8, 24, 12, 13, 14)
    sent_at = datetime(2026, 8, 24, 13, 14, 15)
    fcm_token_id = _create_fcm_token(
        user_id, "fcm-status-delivery", created_at
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        push_log = PushLog(title="Status", body="Test", source="test")
        db.add(push_log)
        db.flush()
        db.add(FcmDeliveryLog(
            push_log_id=push_log.id,
            fcm_token_id=fcm_token_id,
            user_id=user_id,
            sent_at=sent_at,
            success=False,
        ))
        db.commit()
    finally:
        db.close()
    _authenticate(client, user_id, device_id)

    response = client.get(
        "/api/v1/device/fcm-token/status",
        params={"token": "fcm-status-delivery"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "registered": True,
        "registered_at": created_at.isoformat() + "Z",
        "last_delivery_success": False,
        "last_delivery_at": sent_at.isoformat() + "Z",
    }


def test_fcm_token_status_ohne_token_antwortet_400(client, setup_db):
    user_id, device_id = _make_device_user("fcm_status_missing_param")
    _authenticate(client, user_id, device_id)

    assert client.get("/api/v1/device/fcm-token/status").status_code == 400
    assert client.get(
        "/api/v1/device/fcm-token/status", params={"token": "   "}
    ).status_code == 400


def test_fcm_token_status_ohne_authentifizierung_antwortet_401(client, setup_db):
    response = client.get(
        "/api/v1/device/fcm-token/status", params={"token": "fcm-status-token"}
    )

    assert response.status_code == 401


def test_fcm_token_status_funktioniert_per_bearer_token(client, setup_db):
    user_id, _ = _make_device_user("fcm_status_bearer")
    created_at = datetime(2026, 8, 24, 14, 15, 16)
    _create_fcm_token(user_id, "fcm-status-bearer", created_at)

    response = client.get(
        "/api/v1/device/fcm-token/status",
        params={"token": "fcm-status-bearer"},
        headers={"Authorization": "Bearer raw-fcm_status_bearer"},
    )

    assert response.status_code == 200
    assert response.json()["registered"] is True
    assert response.json()["registered_at"] == created_at.isoformat() + "Z"
