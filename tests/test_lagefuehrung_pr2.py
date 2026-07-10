"""Lageführung-Modul Phase 2: taktische Zeichen, Fahrzeug-Mapping, Meldungsmarker,
Distanzwerkzeuge, Rechtevergabe, PDF-Lagebericht, Presence/Lock-Registries (ws.py).
"""
import time

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.lagefuehrung import LagefuehrungBerechtigung, LagefuehrungFeature
from app.models.master import FireDept, OrgSettings, SystemSettings, VehicleMaster
from app.models.user import Role, User, UserRole
from app.services.tz_service import list_tz_symbole, load_tz_manifest, tz_symbol_name

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    # Cookies vorher leeren: die Sliding-Window-Session-Middleware (app/main.py
    # session_middleware) verlängert bei jedem Request eine bereits vorhandene
    # gültige Session und setzt dabei ein zweites Set-Cookie: session=<alter User>
    # zusätzlich zum neuen Login-Cookie — ohne Clear "gewinnt" im Cookie-Jar teils
    # der alte User, wenn Tests innerhalb einer Funktion den Nutzer wechseln.
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


def _setup(username, *, org_id=ORG_ID, rollen=("incident_leader",)):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft2 Test", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org_id)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = True

        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                             lat=47.4652, lng=9.7503)
        db.add(incident)
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


# ── tz_service: Manifest-Katalog ─────────────────────────────────────────────────

def test_load_tz_manifest_hat_symbole():
    m = load_tz_manifest()
    assert len(m.get("symbole", [])) > 10


def test_list_tz_symbole_filtert_kategorie():
    alle = list_tz_symbole()
    fahrzeuge = list_tz_symbole(kat={"Fahrzeuge"})
    assert len(fahrzeuge) < len(alle)
    assert all(s["kat"] == "Fahrzeuge" for s in fahrzeuge)


def test_tz_symbol_name_bekannt_und_unbekannt():
    assert tz_symbol_name("feuerwehrfahrzeug") == "Feuerwehrfahrzeug"
    assert tz_symbol_name("nicht_vorhanden") is None
    assert tz_symbol_name(None) is None


# ── P2-B: Fahrzeugtyp-Zeichen-Mapping ────────────────────────────────────────────

def test_vehicle_admin_neu_speichert_taktisches_zeichen(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username="lft2_admin_neu", password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        db.commit()
    finally:
        db.close()

    _login(client, "lft2_admin_neu", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/fahrzeuge/neu", data={
        "code": "TZTEST", "name": "Test-Fahrzeug", "type": "",
        "taktisches_zeichen": "feuerwehrfahrzeug", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = db.query(VehicleMaster).filter(VehicleMaster.code == "TZTEST").first()
        assert v is not None
        assert v.taktisches_zeichen == "feuerwehrfahrzeug"
    finally:
        db.close()


def test_vehicles_json_liefert_zeichen_key(client):
    user_id, incident_id = _setup("lft2_vehjson_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="TZ2", name="Test", taktisches_zeichen="fahrzeug_grund")
        db.add(vm)
        db.flush()
        col = IncidentColumn(incident_id=incident_id, code="active", title="Aktiv",
                              column_kind="vehicles", is_fixed=True)
        db.add(col)
        db.flush()
        db.add(IncidentVehicle(incident_id=incident_id, column_id=col.id, vehicle_master_id=vm.id))
        db.commit()
    finally:
        db.close()

    _login(client, "lft2_vehjson_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/vehicles.json")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["zeichen_key"] == "fahrzeug_grund"


# ── P2-A/C/D: Feature-Typen taktisches_zeichen / meldung / distanz ──────────────

def test_feature_taktisches_zeichen_anlegen(client):
    _, incident_id = _setup("lft2_tz_user")
    _login(client, "lft2_tz_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "taktisches_zeichen", "zeichen_key": "feuerwehrfahrzeug",
        "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
        "rotation": 45, "scale": 1.5,
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text[:300]
    body = r.json()
    assert body["zeichen_key"] == "feuerwehrfahrzeug"
    assert body["rotation"] == 45
    assert body["scale"] == 1.5


def test_feature_meldung_anlegen_fliesst_in_chronologie(client):
    _, incident_id = _setup("lft2_meldung_user")
    _login(client, "lft2_meldung_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "meldung", "label": "Rauch aus Obergeschoss gemeldet",
        "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    assert r.json()["label"] == "Rauch aus Obergeschoss gemeldet"

    events = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json").json()
    assert any(e["event_typ"] == "feature.created" for e in events)


def test_feature_distanz_linie_und_kreis(client):
    _, incident_id = _setup("lft2_distanz_user")
    _login(client, "lft2_distanz_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "distanz",
        "geometry": {"type": "LineString", "coordinates": [[9.75, 47.46], [9.76, 47.47]]},
        "props": {"kind": "linie", "distanz_m": 1234},
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    assert r.json()["props"]["distanz_m"] == 1234

    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "distanz",
        "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
        "props": {"kind": "kreis", "distanz_m": 500},
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    assert r.json()["props"]["kind"] == "kreis"


# ── P2-F: Rechtevergabe durch den Lageführer ─────────────────────────────────────

def test_rechtevergabe_lifecycle(client):
    fuehrer_id, incident_id = _setup("lft2_fuehrer_user")
    viewer_id, _ = _setup("lft2_viewer_user", rollen=("readonly",))

    _login(client, "lft2_fuehrer_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/uebernehmen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303

    # Viewer kann (noch) nicht zeichnen
    _login(client, "lft2_viewer_user", "Test1234!")
    csrf_viewer = client.cookies.get("ec_csrf")
    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "marker", "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
    }, headers={"X-CSRF-Token": csrf_viewer})
    assert r.status_code == 403

    # Fuehrer erteilt Recht
    _login(client, "lft2_fuehrer_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/berechtigung/{viewer_id}",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["granted"] is True

    # Viewer kann jetzt zeichnen
    _login(client, "lft2_viewer_user", "Test1234!")
    csrf_viewer = client.cookies.get("ec_csrf")
    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "marker", "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
    }, headers={"X-CSRF-Token": csrf_viewer})
    assert r.status_code == 201

    # Fuehrer entzieht Recht wieder
    _login(client, "lft2_fuehrer_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.delete(f"/einsatz/{incident_id}/lagefuehrung/berechtigung/{viewer_id}",
                      headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204

    _login(client, "lft2_viewer_user", "Test1234!")
    csrf_viewer = client.cookies.get("ec_csrf")
    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/features", json={
        "typ": "marker", "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
    }, headers={"X-CSRF-Token": csrf_viewer})
    assert r.status_code == 403


def test_nur_fuehrer_darf_berechtigung_erteilen(client):
    _, incident_id = _setup("lft2_notfuehrer_user")
    viewer_id, _ = _setup("lft2_viewer2_user", rollen=("readonly",))

    _login(client, "lft2_notfuehrer_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/einsatz/{incident_id}/lagefuehrung/berechtigung/{viewer_id}",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


def test_berechtigung_tenant_isolation(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = FireDept(slug="lft2-tenant-a", name="lft2-tenant-a", color="#ff0000", bos="Feuerwehr")
        org_b = FireDept(slug="lft2-tenant-b", name="lft2-tenant-b", color="#ff0000", bos="Feuerwehr")
        db.add_all([org_a, org_b])
        db.flush()
        incident_b = Incident(primary_org_id=org_b.id, alarm_type_code="T1", status="active")
        db.add(incident_b)
        db.flush()
        db.add(LagefuehrungBerechtigung(org_id=org_b.id, incident_id=incident_b.id, user_id=1))
        db.commit()
        incident_b_id = incident_b.id
        org_a_id = org_a.id
    finally:
        db.close()

    db = SessionLocal()
    set_tenant_context(db, org_a_id)
    try:
        result = (
            db.query(LagefuehrungBerechtigung)
            .filter(LagefuehrungBerechtigung.incident_id == incident_b_id)
            .first()
        )
        assert result is None, "Auto-Scope-Backstop greift nicht — Berechtigung einer fremden Org sichtbar"
    finally:
        db.close()


# ── P2-G: PDF-Lagebericht ─────────────────────────────────────────────────────────

def test_pdf_lagebericht_liefert_pdf(client):
    _, incident_id = _setup("lft2_pdf_user")
    _login(client, "lft2_pdf_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert len(r.content) > 100


# ── P2-E: Presence-/Lock-Registries (ws.py, ohne echte WS-Verbindung) ────────────

def test_presence_und_lock_registry_funktionen():
    from app.routers.ws import _lft_locks, _lft_presence, _lft_presence_list, _lft_purge_expired_locks

    incident_id = 999001  # isolierte Test-ID, kollidiert nicht mit echten Einsätzen
    _lft_presence[incident_id].clear()
    _lft_locks[incident_id].clear()

    class _FakeWs:
        pass
    ws1, ws2 = _FakeWs(), _FakeWs()

    _lft_presence[incident_id][ws1] = {"user_id": 1, "name": "Alice"}
    _lft_presence[incident_id][ws2] = {"user_id": 2, "name": "Bob"}
    users = _lft_presence_list(incident_id)
    assert {u["name"] for u in users} == {"Alice", "Bob"}

    _lft_locks[incident_id][42] = {"user_id": 1, "name": "Alice", "expires_at": time.monotonic() - 1}
    _lft_purge_expired_locks(incident_id)
    assert 42 not in _lft_locks[incident_id]

    _lft_locks[incident_id][43] = {"user_id": 1, "name": "Alice", "expires_at": time.monotonic() + 30}
    _lft_purge_expired_locks(incident_id)
    assert 43 in _lft_locks[incident_id]

    _lft_presence[incident_id].clear()
    _lft_locks[incident_id].clear()
