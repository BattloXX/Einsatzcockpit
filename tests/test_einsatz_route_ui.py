"""UI-Regressionen fuer die sperrenbezogene Einsatz-Anfahrt."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import OrgSettings, SystemSettings
from app.models.road_closure import IncidentRoadClosure, IncidentRoute
from app.models.user import Role, User, UserRole


def _user(role="incident_leader"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        system = db.get(SystemSettings, "strassensperren_module_enabled")
        if system is None:
            db.add(SystemSettings(key="strassensperren_module_enabled", value="true"))
        else:
            system.value = "true"
        settings = db.query(OrgSettings).filter_by(org_id=1).first() or OrgSettings(org_id=1)
        settings.strassensperren_modul_aktiv = True
        db.add(settings)
        user = User(username="route-ui-" + uuid4().hex, password_hash=hash_password("Test1234!"),
                    display_name="Route UI", org_id=1, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        username = user.username
        db.commit()
        return username
    finally:
        db.close()


def _login(client, username):
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={"username": username, "password": "Test1234!",
                                              "_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200


def _incident_route(*, with_route=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(primary_org_id=1, status="active", lat=47.47, lng=9.75)
        db.add(incident)
        db.flush()
        if with_route:
            route = IncidentRoute(org_id=1, incident_id=incident.id, status="affected")
            db.add(route)
            db.flush()
            db.add(IncidentRoadClosure(
                org_id=1, incident_route_id=route.id, incident_id=incident.id,
                relevance="route", title_snapshot="Hauptstraße", restriction_type_snapshot="closed",
                geometry_snapshot=json.dumps({"type": "Point", "coordinates": [9.74, 47.47]}),
                geometry_status_snapshot="ok", created_at=datetime.now(UTC).replace(tzinfo=None),
            ))
        db.commit()
        return incident.id
    finally:
        db.close()


def test_route_json_liefert_org_snapshot_und_legt_nichts_an(client):
    user = _user()
    _login(client, user)
    incident_id = _incident_route()
    payload = client.get(f"/einsatz/{incident_id}/route.json").json()
    assert payload["status"] == "affected"
    assert payload["closures"][0]["title"] == "Hauptstraße"

    pending_id = _incident_route(with_route=False)
    assert client.get(f"/einsatz/{pending_id}/route.json").json() == {"status": "pending"}
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(IncidentRoute).filter_by(incident_id=pending_id, org_id=1).count() == 0
    finally:
        db.close()


def test_route_neu_markiert_route_und_info_enthaelt_container(client):
    user = _user()
    _login(client, user)
    incident_id = _incident_route()
    response = client.post(f"/einsatz/{incident_id}/route/neu", data={"_csrf": client.cookies.get("ec_csrf")})
    assert response.json() == {"ok": True}
    assert 'id="anfahrt-hinweis"' in client.get(f"/einsatz/{incident_id}/info").text
    assert "incident_route_updated" in open("app/static/js/app.js", encoding="utf-8").read()
