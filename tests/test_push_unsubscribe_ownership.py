from app.core.security import hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import PushSubscription, User


def _make_user(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            active=True,
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _add_subscription(user_id: int, endpoint: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh="test-p256dh",
            auth="test-auth",
        ))
        db.commit()
    finally:
        db.close()


def _subscription_exists(endpoint: str) -> bool:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(PushSubscription).filter_by(endpoint=endpoint).first() is not None
    finally:
        db.close()


def test_user_cannot_delete_another_users_subscription(client, setup_db):
    user_a_id = _make_user("push_unsubscribe_user_a")
    user_b_id = _make_user("push_unsubscribe_user_b")
    endpoint = "https://push.example/owned-by-user-b"
    _add_subscription(user_b_id, endpoint)
    client.cookies.set("session", sign_session(user_a_id))

    response = client.post("/push/unsubscribe", json={"endpoint": endpoint})

    assert response.status_code == 200
    assert _subscription_exists(endpoint)


def test_anonymous_unsubscribe_returns_401(client, setup_db):
    response = client.post(
        "/push/unsubscribe",
        json={"endpoint": "https://push.example/anonymous"},
    )

    assert response.status_code == 401


def test_owner_can_delete_own_subscription(client, setup_db):
    user_id = _make_user("push_unsubscribe_owner")
    endpoint = "https://push.example/owned-by-requester"
    _add_subscription(user_id, endpoint)
    client.cookies.set("session", sign_session(user_id))

    response = client.post("/push/unsubscribe", json={"endpoint": endpoint})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert not _subscription_exists(endpoint)
