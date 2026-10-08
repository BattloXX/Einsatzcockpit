"""Integrationstests fuer die UI der Straßensperren."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.road_closure import RoadClosure, RoadClosureShare
from app.models.user import Role, User, UserRole
from app.routers.ui_road_closure import require_strassensperren_enabled


def test_modul_guard_liefert_404_wenn_deaktiviert():
    request = SimpleNamespace(state=SimpleNamespace(strassensperren_enabled=False))
    with pytest.raises(HTTPException) as error:
        require_strassensperren_enabled(request)
    assert error.value.status_code == 404


def _setup_user(role: str, *, org_id: int = 1, enabled: bool = True) -> User:
    username = f"sperren_{role}_{uuid4().hex}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        system = db.get(SystemSettings, "strassensperren_module_enabled")
        if system is None:
            db.add(SystemSettings(key="strassensperren_module_enabled", value="true"))
        else:
            system.value = "true"
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first() or OrgSettings(org_id=org_id)
        db.add(settings)
        settings.strassensperren_modul_aktiv = enabled
        org = db.get(FireDept, org_id)
        if org is not None:
            org.timezone = "Europe/Vienna"
        user = User(username=username, password_hash=hash_password("Test1234!"), display_name=username, org_id=org_id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={"username": user.username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200


def _payload(client, **more):
    # Eindeutige Straße je Aufruf: sonst meldet die Verlängerungs-/Dublettenerkennung in der gemeinsamen
    # Test-DB eine mögliche bestehende Sperre (409).
    street = f"Hauptstraße {uuid4().hex[:8]}"
    values = {"_csrf": client.cookies.get("ec_csrf"), "title": "B 1 gesperrt", "street": street, "valid_from": "2026-07-01T10:00", "restriction_type": "closed"}
    values.update(more)
    return values


def _closure(org_id: int = 1, **more) -> RoadClosure:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        values = {"org_id": org_id, "title": "Test-Sperre", "street": "Testweg", "valid_from": datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1), "restriction_type": "closed"}
        values.update(more)
        closure = RoadClosure(**values)
        db.add(closure)
        db.commit()
        db.refresh(closure)
        db.expunge(closure)
        return closure
    finally:
        db.close()


def test_modul_aus_und_readonly_schreibschutz(client):
    disabled = _setup_user("readonly", enabled=False)
    _login(client, disabled)
    assert client.get("/strassensperren").status_code == 404
    reader = _setup_user("readonly")
    _login(client, reader)
    assert client.get("/strassensperren").status_code == 200
    assert client.post("/strassensperren/neu", data=_payload(client)).status_code == 403


def test_anlegen_validierung_und_utc_sommerzeit(client):
    user = _setup_user("objekt_verwalter")
    _login(client, user)
    response = client.post("/strassensperren/neu", data=_payload(client), follow_redirects=False)
    assert response.status_code == 303
    closure_id = int(response.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        assert db.get(RoadClosure, closure_id).valid_from == datetime(2026, 7, 1, 8, 0)
        before = db.query(RoadClosure).filter_by(org_id=1).count()
    finally: db.close()
    bad = client.post("/strassensperren/neu", data=_payload(client, valid_until="2026-07-01T09:00"))
    assert bad.status_code == 422
    db = SessionLocal(); set_tenant_context(db, None)
    try: assert db.query(RoadClosure).filter_by(org_id=1).count() == before
    finally: db.close()


def test_bearbeiten_deaktivieren_reaktivieren_und_loeschen(client):
    manager = _setup_user("objekt_verwalter"); _login(client, manager)
    closure = _closure()
    assert client.post(f"/strassensperren/{closure.id}/bearbeiten", data=_payload(client, version="999")).status_code == 422
    changed = client.post(f"/strassensperren/{closure.id}/bearbeiten", data=_payload(client, version=str(closure.version), title="Geänderte Sperre"), follow_redirects=False)
    assert changed.status_code == 303 and "Geänderte Sperre" in client.get(f"/strassensperren/{closure.id}").text
    assert client.post(f"/strassensperren/{closure.id}/deaktivieren", data={"_csrf": client.cookies.get("ec_csrf"), "grund": "Aufgehoben"}, follow_redirects=False).status_code == 303
    assert "Deaktiviert" in client.get(f"/strassensperren/{closure.id}").text
    assert client.post(f"/strassensperren/{closure.id}/reaktivieren", data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False).status_code == 303
    assert client.post(f"/strassensperren/{closure.id}/loeschen", data={"_csrf": client.cookies.get("ec_csrf")}).status_code == 403
    admin = _setup_user("org_admin"); _login(client, admin)
    assert client.post(f"/strassensperren/{closure.id}/loeschen", data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False).status_code == 303


def test_liste_karte_und_geometrie(client):
    user = _setup_user("objekt_verwalter"); _login(client, user)
    expired = _closure(title="Abgelaufen", valid_from=datetime(2020, 1, 1), valid_until=datetime(2020, 1, 2))
    geometry = '{"type":"LineString","coordinates":[[9.7,47.5],[9.71,47.51]]}'
    mapped = _closure(title="Mit Geometrie", geometry_geojson=geometry, geometry_status="ok")
    _closure(title="Ohne Geometrie")
    assert "Abgelaufen" not in client.get("/strassensperren").text
    assert "Abgelaufen" in client.get("/strassensperren?status=all").text
    payload = client.get("/strassensperren/karte.json").json()
    assert payload["type"] == "FeatureCollection"
    assert any(item["properties"]["id"] == mapped.id and item["properties"]["color"] == "red" for item in payload["features"])
    assert all(item["properties"]["id"] != expired.id for item in payload["features"])
    assert client.get(f"/strassensperren/{mapped.id}/geometrie.json").status_code == 200


def test_isolation_freigabe_und_navigation(client):
    owner = _setup_user("objekt_verwalter"); closure = _closure(title="Nur Eigentümer")
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        org_b = FireDept(slug=f"sperren-{uuid4().hex}", name="Sperren Org B", timezone="Europe/Vienna")
        db.add(org_b); db.flush(); org_b_id = org_b.id
        db.add(OrgSettings(org_id=org_b_id, strassensperren_modul_aktiv=True)); db.commit()
    finally: db.close()
    foreign = _setup_user("objekt_verwalter", org_id=org_b_id); _login(client, foreign)
    assert client.get(f"/strassensperren/{closure.id}").status_code == 404
    assert str(closure.id) not in client.get("/strassensperren/karte.json").text
    db = SessionLocal(); set_tenant_context(db, None)
    try: db.add(RoadClosureShare(road_closure_id=closure.id, org_id=org_b_id)); db.commit()
    finally: db.close()
    assert client.get(f"/strassensperren/{closure.id}").status_code == 200
    assert "geteilt von" in client.get("/strassensperren").text
    assert client.get(f"/strassensperren/{closure.id}/bearbeiten").status_code == 404
    assert client.post(f"/strassensperren/{closure.id}/bearbeiten", data=_payload(client, version="1")).status_code == 404
    assert client.get(f"/strassensperren/{closure.id}/geometrie.json").status_code == 404
    _login(client, owner)
    assert "/strassensperren" in client.get("/").text
