"""Contract tests for the offline contact feed."""

from uuid import uuid4

import pytest

from app.core.security import generate_api_key, hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.bma_import import BmaImportSatz
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt
from app.models.user import ApiKey, DeviceToken, Role, User, UserRole
from app.services import kontakt_service
from app.services.bma_import.bma_sync import _sync_kontakte


def _setup_device_sync_user(*, role_code: str = "readonly", org_id: int | None = None):
    """Create an authenticated app user and enable contacts for its organization."""
    db = SessionLocal()
    set_tenant_context(db, None)
    suffix = uuid4().hex
    try:
        if org_id is None:
            org = FireDept(slug=f"kontakt-sync-{suffix}", name=f"Kontakt Sync {suffix}")
            db.add(org)
            db.flush()
            org_id = org.id
        user = User(
            username=f"kontakt-sync-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name="Kontakt Sync",
            active=True,
            org_id=org_id,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        raw_token = f"kontakt-device-{suffix}"
        db.add(DeviceToken(label="Kontakt Sync", token_hash=hash_api_key(raw_token), user_id=user.id))
        system = db.get(SystemSettings, "kontakte_module_enabled")
        if system is None:
            db.add(SystemSettings(key="kontakte_module_enabled", value="true"))
        else:
            system.value = "true"
        org_settings = (
            db.query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .execution_options(include_all_tenants=True)
            .first()
        )
        if org_settings is None:
            org_settings = OrgSettings(org_id=org_id)
            db.add(org_settings)
        org_settings.kontakte_module_enabled = True
        db.commit()
        return user.id, org_id, raw_token
    finally:
        db.close()


def test_device_kontakt_sync_accepts_session_auth_and_returns_org_id(client):
    user_id, org_id, _ = _setup_device_sync_user()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kontakt = kontakt_service.create_kontakt(
            db, {"typ": "person", "anzeigename": "Session Kontakt"}, [], [], org_id=org_id, user_id=None
        )
        kontakt_id = kontakt.id
    finally:
        db.close()
    client.cookies.set("session", sign_session(user_id))

    response = client.get("/api/v1/device/kontakte/sync")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "snapshot"
    assert payload["org_id"] == org_id
    assert any(item["id"] == kontakt_id for item in payload["contacts"])


def test_device_kontakt_sync_accepts_device_bearer_token(client):
    _, org_id, raw_token = _setup_device_sync_user()

    response = client.get(
        "/api/v1/device/kontakte/sync", headers={"Authorization": f"Bearer {raw_token}"}
    )

    assert response.status_code == 200
    assert response.json()["org_id"] == org_id


def test_device_kontakt_sync_requires_authentication(client):
    response = client.get("/api/v1/device/kontakte/sync")

    assert response.status_code == 401


def test_device_kontakt_sync_rejects_user_without_read_role(client):
    _, _, raw_token = _setup_device_sync_user(role_code="probenverwalter")

    response = client.get(
        "/api/v1/device/kontakte/sync", headers={"Authorization": f"Bearer {raw_token}"}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("disabled_flag", ("org", "system"))
def test_device_kontakt_sync_returns_404_when_module_is_disabled(client, disabled_flag):
    _, org_id, raw_token = _setup_device_sync_user()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        if disabled_flag == "org":
            db.query(OrgSettings).filter(OrgSettings.org_id == org_id).one().kontakte_module_enabled = False
        else:
            db.get(SystemSettings, "kontakte_module_enabled").value = "false"
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/v1/device/kontakte/sync", headers={"Authorization": f"Bearer {raw_token}"}
    )

    assert response.status_code == 404


def test_device_kontakt_sync_bearer_token_isolates_organizations(client):
    _, org_a_id, raw_token = _setup_device_sync_user()
    _, org_b_id, _ = _setup_device_sync_user()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kontakt_b = kontakt_service.create_kontakt(
            db, {"typ": "person", "anzeigename": "Geheimer Kontakt Org B"}, [], [], org_id=org_b_id, user_id=None
        )
        kontakt_b_id = kontakt_b.id
    finally:
        db.close()

    response = client.get(
        "/api/v1/device/kontakte/sync", headers={"Authorization": f"Bearer {raw_token}"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["org_id"] == org_a_id
    assert all(item["id"] != kontakt_b_id for item in payload["contacts"])


def test_snapshot_delta_and_tombstone(client):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org.is_(True)).first()
        assert org is not None
        org_id = org.id
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Kontakt-Sync", org_id=org_id))
        kontakt = kontakt_service.create_kontakt(
            db, {"typ": "person", "anzeigename": "Offline Kontakt"}, [], [], org_id=org_id, user_id=None
        )
        kontakt_id = kontakt.id
    finally:
        db.close()
    headers = {"X-API-Key": raw}
    snapshot = client.get("/api/v1/kontakte/sync", headers=headers).json()
    assert snapshot["schema_version"] == 1
    assert snapshot["org_id"] == org_id
    assert any(item["id"] == kontakt_id for item in snapshot["contacts"])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kontakt_service.archive_kontakt(db, kontakt_id, user_id=None)
    finally:
        db.close()
    delta = client.get(f"/api/v1/kontakte/sync?cursor={snapshot['cursor']}", headers=headers).json()
    assert delta["org_id"] == org_id
    assert any(item["operation"] == "tombstone" and item["id"] == kontakt_id for item in delta["changes"])


def test_bma_import_emits_contact_and_mapping_delta(client):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org.is_(True)).first()
        assert org is not None
        org_id = org.id
        db.add(ApiKey(key_hash=hash_api_key(raw), label="BMA-Kontakt-Sync", org_id=org.id))
        db.commit()
    finally:
        db.close()

    headers = {"X-API-Key": raw}
    snapshot = client.get("/api/v1/kontakte/sync", headers=headers).json()

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        objekt = Objekt(org_id=org_id, nummer=987654, name="BMA Sync Objekt")
        db.add(objekt)
        db.flush()
        satz = BmaImportSatz(org_id=org_id, objekt_id=objekt.id, extern_id="pdf:sync-test")
        db.add(satz)
        db.flush()
        _sync_kontakte(
            db,
            satz,
            objekt,
            [{
                "extern_id": "pdf:sync-test:bma_alarmperson:max-muster",
                "name": "Max Muster",
                "art": "bma_alarmperson",
                "telefone": ["+43 555 123"],
            }],
            None,
        )
        kontakt_id = objekt.kontakte[0].kontakt_id
        zuordnung_id = objekt.kontakte[0].id
        db.commit()
    finally:
        db.close()

    delta = client.get(f"/api/v1/kontakte/sync?cursor={snapshot['cursor']}", headers=headers).json()
    assert any(
        item["entity"] == "kontakt" and item["operation"] == "upsert" and item["id"] == kontakt_id
        for item in delta["changes"]
    )
    assert any(
        item["entity"] == "zuordnung" and item["operation"] == "upsert" and item["id"] == zuordnung_id
        for item in delta["changes"]
    )
