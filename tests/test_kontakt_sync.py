"""Contract tests for the offline contact feed."""

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.bma_import import BmaImportSatz
from app.models.master import FireDept
from app.models.objekt import Objekt
from app.models.user import ApiKey
from app.services import kontakt_service
from app.services.bma_import.bma_sync import _sync_kontakte


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
