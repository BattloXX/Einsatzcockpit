"""Tests für den GeoJSON-Endpoint /api/lagekarte/…"""
import hashlib

import pytest

from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.lagekarte import LagekarteToken
from app.models.master import FireDept, VehicleMaster

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def org_id(setup_db):
    db = SessionLocal()
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        assert org is not None, "Keine Home-Org in Seed-Daten"
        return org.id
    finally:
        db.close()


@pytest.fixture
def other_org_id(setup_db):
    db = SessionLocal()
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == False).first()  # noqa: E712
        assert org is not None
        return org.id
    finally:
        db.close()


@pytest.fixture
def incident_with_vehicles(setup_db, org_id):
    """Erstellt einen Einsatz mit Koordinaten + 2 Fahrzeugen."""
    db = SessionLocal()
    try:
        incident = Incident(
            alarm_type_code="T1",
            status="active",
            primary_org_id=org_id,
            address_street="Teststraße",
            address_no="1",
            address_city="Wolfurt",
            lat=47.4664,
            lng=9.7416,
        )
        db.add(incident)
        db.flush()

        col = IncidentColumn(
            incident_id=incident.id, code="dispatched",
            title="Disponierte Fahrzeuge", is_fixed=True, display_order=0,
        )
        db.add(col)
        db.flush()

        vm = db.query(VehicleMaster).filter(VehicleMaster.dept_id == org_id).first()
        if vm is None:
            vm = VehicleMaster(dept_id=org_id, code="RLF", name="RLF",
                               type="Rüstlöschfahrzeug", is_first_train=True, display_order=0, active=True)
            db.add(vm)
            db.flush()

        iv1 = IncidentVehicle(incident_id=incident.id, column_id=col.id,
                               vehicle_master_id=vm.id, unit_status="Am Einsatzort")
        iv2 = IncidentVehicle(incident_id=incident.id, column_id=col.id,
                               vehicle_master_id=vm.id, unit_status="Einsatz übernommen")
        db.add_all([iv1, iv2])
        db.commit()
        return incident.id
    finally:
        db.close()


@pytest.fixture
def incident_no_coords(setup_db, org_id):
    """Einsatz ohne lat/lng."""
    db = SessionLocal()
    try:
        incident = Incident(alarm_type_code="F1", status="active", primary_org_id=org_id)
        db.add(incident)
        db.commit()
        return incident.id
    finally:
        db.close()


@pytest.fixture
def incident_empty(setup_db, org_id):
    """Einsatz mit Koordinaten aber ohne Fahrzeuge."""
    db = SessionLocal()
    try:
        incident = Incident(alarm_type_code="F2", status="active",
                            primary_org_id=org_id, lat=47.0, lng=9.5)
        db.add(incident)
        db.commit()
        return incident.id
    finally:
        db.close()


def _make_token(org_id, einsatz_id=None, revoked=False, expired=False) -> str:
    import secrets
    from datetime import UTC, datetime, timedelta
    raw = "lkw_" + secrets.token_urlsafe(16)
    tok_hash = hashlib.sha256(raw.encode()).hexdigest()
    db = SessionLocal()
    try:
        expires_at = datetime.now(UTC) - timedelta(hours=1) if expired else None
        revoked_at = datetime.now(UTC) if revoked else None
        tok = LagekarteToken(
            token_hash=tok_hash,
            label="Test",
            org_id=org_id,
            einsatz_id=einsatz_id,
            expires_at=expires_at,
            revoked_at=revoked_at,
        )
        db.add(tok)
        db.commit()
    finally:
        db.close()
    return raw


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_geojson_valid_org_token(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2


def test_geojson_feature_schema(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    feat = r.json()["features"][0]
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] == "Point"
    coords = feat["geometry"]["coordinates"]
    assert len(coords) == 2
    # GeoJSON: [lng, lat] → lng ist der erste Wert (~9.7)
    assert 9.0 < coords[0] < 10.5, f"Erster Koordinatenwert sollte Längengrad sein, war {coords[0]}"
    assert 47.0 < coords[1] < 48.0, f"Zweiter Koordinatenwert sollte Breitengrad sein, war {coords[1]}"
    props = feat["properties"]
    assert "name" in props
    assert "typ" in props
    assert "status" in props
    assert "einsatz_id" in props
    assert "fahrzeug_id" in props


def test_geojson_coordinates_order(client, incident_with_vehicles, org_id):
    """Stellt sicher, dass Koordinaten in [lng, lat] Reihenfolge sind (GeoJSON-Standard)."""
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    for feat in r.json()["features"]:
        lng, lat = feat["geometry"]["coordinates"]
        assert abs(lng) <= 180
        assert abs(lat) <= 90
        # Wolfurt-Region: lat ~47, lng ~9
        assert 47.0 < lat < 48.0
        assert 9.0 < lng < 10.5


def test_geojson_cache_control_header(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    assert r.headers.get("cache-control") == "no-store"


def test_geojson_invalid_token(client, incident_with_vehicles):
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token=lkw_invalid_token_xyz")
    assert r.status_code == 401


def test_geojson_revoked_token(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id, revoked=True)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 401


def test_geojson_expired_token(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id, expired=True)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 401


def test_geojson_einsatz_scoped_token_wrong_incident(client, incident_with_vehicles, incident_no_coords, org_id):
    """Token, das auf incident_with_vehicles beschränkt ist, darf nicht für anderen Einsatz gelten."""
    raw = _make_token(org_id, einsatz_id=incident_with_vehicles)
    r = client.get(f"/api/lagekarte/einsatz/{incident_no_coords}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 404


def test_geojson_einsatz_scoped_token_correct_incident(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id, einsatz_id=incident_with_vehicles)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 200


def test_geojson_org_mismatch(client, incident_with_vehicles, other_org_id):
    """Token einer anderen Org darf nicht auf den Einsatz zugreifen."""
    raw = _make_token(other_org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 404


def test_geojson_empty_incident(client, incident_empty, org_id):
    """Einsatz mit Koordinaten aber ohne Fahrzeuge → leere FeatureCollection."""
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_empty}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 200
    assert r.json()["features"] == []


def test_geojson_no_coords(client, incident_no_coords, org_id):
    """Einsatz ohne lat/lng → leere FeatureCollection (kein 404)."""
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_no_coords}/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 200
    assert r.json()["features"] == []


def test_geojson_nonexistent_incident(client, org_id):
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/999999/fahrzeuge.geojson?token={raw}")
    assert r.status_code == 404


def test_kml_valid(client, incident_with_vehicles, org_id):
    raw = _make_token(org_id)
    r = client.get(f"/api/lagekarte/einsatz/{incident_with_vehicles}/fahrzeuge.kml?token={raw}")
    assert r.status_code == 200
    assert "kml" in r.headers.get("content-type", "").lower()
    assert b"<kml" in r.content


def test_cors_preflight(client):
    """OPTIONS mit lagekarte.info Origin → CORS-Header vorhanden."""
    r = client.options(
        "/api/lagekarte/einsatz/1/fahrzeuge.geojson",
        headers={"Origin": "https://www.lagekarte.info",
                 "Access-Control-Request-Method": "GET"},
    )
    # FastAPI CORSMiddleware antwortet mit 200 auf OPTIONS
    assert r.status_code in (200, 204)
    origin_header = r.headers.get("access-control-allow-origin", "")
    assert "lagekarte.info" in origin_header or origin_header == "*", (
        f"Erwartet lagekarte.info in CORS-Header, bekam: {origin_header}"
    )
