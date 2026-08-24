from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceToken, FcmToken, User


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
