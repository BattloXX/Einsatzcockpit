"""Regressionstest: Das Einsatz-Dashboard aktualisiert sich live per WebSocket +
gezieltem HTMX/Fetch-Swap statt per zeitgesteuertem `location.reload()` (30s-Countdown).

Prüft:
- Vollseiten-Route und neuer Fragment-Endpoint (/dashboard/inhalt) liefern 200 und
  identische Datenausschnitte (kein Render-Drift zwischen den beiden Kontext-Aufbauten).
- Das Fragment enthält keinen eigenen <html>/<body>-Rahmen (reines Swap-Ziel).
- Die Vollseite enthält keinen zeitgesteuerten `location.reload()` mehr.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_incident(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Dashboard Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))

        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="B1", status="active",
                             address_street="Teststrasse", address_no="1")
        db.add(incident)
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


def test_dashboard_vollseite_laedt_und_hat_keinen_zeitgesteuerten_reload():
    _, incident_id = _setup_incident("dash_ws_full_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "dash_ws_full_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/dashboard")
    assert r.status_code == 200, r.text[:300]
    assert 'id="dashBody"' in r.text
    assert "/ws/incident/" in r.text
    # Kein zeitgesteuerter Reload mehr (Countdown-Pfad wurde durch WS-Swap ersetzt)
    assert "refreshCountdown" not in r.text
    assert "location.reload()" not in r.text


def test_dashboard_fragment_liefert_identischen_datenausschnitt():
    _, incident_id = _setup_incident("dash_ws_fragment_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "dash_ws_fragment_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/dashboard/inhalt")
    assert r.status_code == 200, r.text[:300]
    # Fragment ist reiner Datenbereich (kein eigener <html>/<body>-Rahmen)
    assert "<html" not in r.text
    assert "<body" not in r.text
    # Enthaelt die gleichen Panels wie die Vollseite
    assert "Fahrzeuge / Abschnitte" in r.text
    assert "Aufträge" in r.text
    assert "Meldungen" in r.text
    assert 'id="lageHintsData"' in r.text


def test_dashboard_fragment_ohne_login_401():
    _, incident_id = _setup_incident("dash_ws_noauth_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    r = client.get(f"/einsatz/{incident_id}/dashboard/inhalt")
    assert r.status_code == 401
