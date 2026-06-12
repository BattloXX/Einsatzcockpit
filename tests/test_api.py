"""Tests für die REST-API (Einsatz anlegen)."""
import pytest
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.core.security import generate_api_key, hash_api_key
from app.models.master import FireDept
from app.models.user import ApiKey


@pytest.fixture
def api_key(setup_db):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        key = ApiKey(key_hash=hash_api_key(raw), label="Test", org_id=org.id)
        db.add(key)
        db.commit()
    finally:
        db.close()
    return raw


PAYLOAD = {
    "Key": "test-key-001",
    "Nummer": 1,
    "AlarmDatumZeit": "2026-01-01T10:00:00",
    "Stufe": "t1",
    "Art": "T",
    "Meldung": "Testmeldung",
    "Einsatzgrund": "Test",
    "Ort": "Wolfurt",
    "Strasse": "Teststraße",
    "HausNr": "1",
    "Uebung": True,
}


def test_create_incident_no_key(client):
    r = client.post("/api/v1/einsatz", json=PAYLOAD)
    assert r.status_code == 422  # missing header


def test_create_incident_invalid_key(client):
    r = client.post("/api/v1/einsatz", json=PAYLOAD, headers={"X-API-Key": "invalid"})
    assert r.status_code == 401


def test_create_incident_success(client, api_key):
    r = client.post("/api/v1/einsatz", json=PAYLOAD, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert data["created"] is True
    assert data["id"] > 0
    incident_id = data["id"]

    # Idempotency: same Key again → created=False
    r2 = client.post("/api/v1/einsatz", json=PAYLOAD, headers={"X-API-Key": api_key})
    assert r2.status_code == 200
    assert r2.json()["created"] is False
    assert r2.json()["id"] == incident_id


def test_list_active(client, api_key):
    r = client.get("/api/v1/einsatz/active", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
