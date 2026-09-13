"""Contract tests for the offline contact feed."""

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import ApiKey
from app.services import kontakt_service


def test_snapshot_delta_and_tombstone(client):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org.is_(True)).first()
        assert org is not None
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Kontakt-Sync", org_id=org.id))
        kontakt = kontakt_service.create_kontakt(
            db, {"typ": "person", "anzeigename": "Offline Kontakt"}, [], [], org_id=org.id, user_id=None
        )
        kontakt_id = kontakt.id
    finally:
        db.close()
    headers = {"X-API-Key": raw}
    snapshot = client.get("/api/v1/kontakte/sync", headers=headers).json()
    assert snapshot["schema_version"] == 1
    assert any(item["id"] == kontakt_id for item in snapshot["contacts"])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kontakt_service.archive_kontakt(db, kontakt_id, user_id=None)
    finally:
        db.close()
    delta = client.get(f"/api/v1/kontakte/sync?cursor={snapshot['cursor']}", headers=headers).json()
    assert any(item["operation"] == "tombstone" and item["id"] == kontakt_id for item in delta["changes"])
