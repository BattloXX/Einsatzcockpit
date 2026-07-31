from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceToken, FcmToken, User


def _make_user(username: str, password: str = "Test1234!") -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=username,
            active=True,
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _make_device(username: str, raw_token: str) -> tuple[int, int]:
    user_id = _make_user(username)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        device = DeviceToken(
            user_id=user_id,
            token_hash=hash_api_key(raw_token),
            label=f"Gerät {username}",
        )
        db.add(device)
        db.commit()
        return user_id, device.id
    finally:
        db.close()


def _post_login(client, username: str, fcm_token: str):
    client.get("/login", params={"fcm_token": fcm_token})
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={
            "username": username,
            "password": "Test1234!",
            "fcm_token": fcm_token,
            "_csrf": csrf,
        },
        follow_redirects=False,
    )


def test_geraet_login_mit_fcm_token_legt_token_an(client, setup_db):
    raw_token = "device-login-handoff-secret"
    user_id, device_id = _make_device("fcm_device_login", raw_token)

    response = client.get(
        "/geraet-login",
        params={"token": raw_token, "fcm_token": "fcm-device-handoff"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(FcmToken).filter_by(token="fcm-device-handoff").one()
        assert row.user_id == user_id
        assert row.device_token_id == device_id
    finally:
        db.close()


def test_geraet_login_ohne_fcm_token_bleibt_unveraendert(client, setup_db):
    raw_token = "device-login-no-fcm-secret"
    user_id, _ = _make_device("fcm_device_login_none", raw_token)

    response = client.get(
        "/geraet-login", params={"token": raw_token}, follow_redirects=False
    )
    assert response.status_code == 302

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(FcmToken).filter_by(user_id=user_id).count() == 0
    finally:
        db.close()


def test_login_mit_fcm_token_legt_token_ohne_device_id_an(client, setup_db):
    user_id = _make_user("fcm_account_login")
    response = _post_login(client, "fcm_account_login", "fcm-account-handoff")
    assert response.status_code == 302

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(FcmToken).filter_by(token="fcm-account-handoff").one()
        assert row.user_id == user_id
        assert row.device_token_id is None
    finally:
        db.close()


def test_session_request_mit_fcm_token_legt_token_fuer_user_an(client, setup_db):
    user_id = _make_user("fcm_session_handoff")
    assert _post_login(client, "fcm_session_handoff", "").status_code == 302

    response = client.get("/", params={"fcm_token": "fcm-session-handoff"})
    assert response.status_code == 200

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(FcmToken).filter_by(token="fcm-session-handoff").one()
        assert row.user_id == user_id
        assert row.device_token_id is None
    finally:
        db.close()


def test_fcm_token_wird_bei_erneutem_login_aktualisiert_nicht_dupliziert(client, setup_db):
    first_user_id = _make_user("fcm_upsert_first")
    second_user_id = _make_user("fcm_upsert_second")
    token = "fcm-shared-upsert-token"

    assert _post_login(client, "fcm_upsert_first", token).status_code == 302
    client.cookies.clear()
    assert _post_login(client, "fcm_upsert_second", token).status_code == 302

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(FcmToken).filter_by(token=token).all()
        assert len(rows) == 1
        assert rows[0].user_id == second_user_id
        assert rows[0].user_id != first_user_id
        assert rows[0].device_token_id is None
    finally:
        db.close()
