from uuid import uuid4

import pytest

from app.config import settings
from app.core.security import hash_api_key, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.user import DeviceToken, User
from app.routers.device_api import DEVICE_API_VERSION, DEVICE_FEATURE_ROUTES, router
from tests.conftest import all_app_paths


@pytest.fixture
def device_access(setup_db):
    raw_token = f"capabilities-{uuid4().hex}"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = User(username=raw_token, display_name="Capabilities", active=True, is_device=True)
        db.add(user)
        db.flush()
        device = DeviceToken(user_id=user.id, token_hash=hash_api_key(raw_token), label="Testgeraet")
        db.add(device)
        db.commit()
        return raw_token, sign_session(user.id, device=True, device_token_id=device.id)


@pytest.mark.parametrize("authorization", [None, "Bearer unbekannt", "Bearer "])
def test_capabilities_erfordern_authentifizierung(client, authorization):
    headers = {"Authorization": authorization} if authorization is not None else {}

    response = client.get("/api/v1/device/capabilities", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht eingeloggt"}


@pytest.mark.parametrize("auth", ["bearer", "session"])
def test_capabilities_antwortformat(client, device_access, auth):
    raw_token, session = device_access
    headers = {}
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {raw_token}"
    else:
        client.cookies.set("session", session)

    response = client.get("/api/v1/device/capabilities", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "api_version": DEVICE_API_VERSION,
        "server_version": settings.APP_VERSION,
        "features": [feature for feature, _ in DEVICE_FEATURE_ROUTES],
    }
    assert type(payload["api_version"]) is int
    assert payload["api_version"] == 1
    assert isinstance(payload["server_version"], str)
    assert isinstance(payload["features"], list)
    assert all(isinstance(feature, str) for feature in payload["features"])
    assert len(payload["features"]) == len(set(payload["features"]))
    assert set(payload["features"]) == {
        "diagnose_report", "location", "duty_state", "push_ack",
        "fcm_test_alarm", "fcm_test_push", "kontakte_sync",
    }


@pytest.mark.parametrize("feature,path", DEVICE_FEATURE_ROUTES)
def test_jedes_feature_hat_registrierte_device_route(feature, path):
    full_path = router.prefix + path

    assert feature
    assert full_path.startswith("/api/v1/device/")
    assert full_path in all_app_paths(app)
    assert full_path in all_app_paths(router)
