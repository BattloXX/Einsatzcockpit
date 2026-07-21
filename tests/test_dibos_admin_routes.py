"""End-to-End-Smoke-Test für die /admin/dibos-Routen (Config-Formular + Diagnose-
Sektion), über den echten FastAPI-Router mit TestClient. Netzwerk wird an keiner
Stelle wirklich angesprochen: dibos_capture.start_trace_for_org wird für den
Start-Endpoint gefaked, damit kein echter Poll-Task gegen dibos.lwz-vorarlberg.at
losläuft."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
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


def _setup_system_admin(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="DIBOS Test Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "system_admin").id))
        db.commit()
        return user.id
    finally:
        db.close()


def test_settings_page_loads_and_shows_diagnose_section():
    _setup_system_admin("dibos_admin_page_user")

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_admin_page_user", "Test1234!")

    r = client.get("/admin/dibos", params={"org_id": ORG_ID})
    assert r.status_code == 200, r.text[:300]
    assert "DIBOS" in r.text
    assert "Diagnose: Live mitlesen" in r.text
    assert 'id="dibos-traces"' in r.text


def test_save_then_test_connection_roundtrip(monkeypatch):
    _setup_system_admin("dibos_admin_save_user")

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_admin_save_user", "Test1234!")

    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/dibos/save", data={
        "_csrf": csrf,
        "target_org_id": ORG_ID,
        "enabled": "1",
        "base_url": "https://dibos.example.at/Z_EventHub",
        "host": "testhost",
        "ag": "FW",
        "poll_interval_seconds": "20",
        "auto_trace_on_event": "1",
        "auto_trace_duration_minutes": "90",
        "gateway_user": "gw_user",
        "gateway_password": "gw-secret",
        "gateway_secret_changed": "1",
        "service_user": "service.test.all",
        "service_password": "svc-secret",
        "service_secret_changed": "1",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "flash=saved" in r.headers["location"]

    # Config gespeichert + verschlüsselt -> Seite zeigt "Passwort gesetzt" statt Klartext
    r = client.get("/admin/dibos", params={"org_id": ORG_ID})
    assert r.status_code == 200
    assert "gw-secret" not in r.text
    assert "svc-secret" not in r.text
    assert r.text.count("Passwort gesetzt") == 2  # Gateway + Servicekonto

    # Verbindungstest: DibosClient.test_connection wird gefaked (kein echtes Netzwerk)
    import app.services.dibos.dibos_client as dibos_client_mod

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def test_connection(self):
            return True, "Verbindung erfolgreich (0 eigene Einsätze aktuell)"

        async def aclose(self):
            pass

    monkeypatch.setattr(dibos_client_mod, "DibosClient", _FakeClient)

    r = client.post("/admin/dibos/test", data={"_csrf": csrf, "target_org_id": ORG_ID})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "Verbindung erfolgreich" in body["message"]


def test_trace_start_status_and_live_view(monkeypatch):
    _setup_system_admin("dibos_admin_trace_user")

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_admin_trace_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    # Status ohne vorherige Aufzeichnungen
    r = client.get("/admin/dibos/trace/status", params={"org_id": ORG_ID})
    assert r.status_code == 200
    assert "Noch keine Aufzeichnungen" in r.text

    # Start faken (kein echter Hintergrund-Task gegen einen echten DIBOS-Server)
    async def fake_start_trace_for_org(org_id, duration_minutes=120):
        assert org_id == ORG_ID
        return "20260101T000000Z"

    monkeypatch.setattr(
        "app.services.dibos.dibos_capture.start_trace_for_org", fake_start_trace_for_org,
    )

    r = client.post("/admin/dibos/trace/start", data={
        "_csrf": csrf, "target_org_id": ORG_ID, "duration_minutes": "30",
    })
    assert r.status_code == 200

    # Live-Ansicht ohne vorhandenen Snapshot (kein latest.json auf Platte) -> Hinweistext
    r = client.get(
        "/admin/dibos/trace/20260101T000000Z/live", params={"target_org_id": ORG_ID},
    )
    assert r.status_code == 200
    assert "Noch kein Live-Snapshot" in r.text


# ── /admin/dibos/einsaetze: Einsatz-Infos-Seite ─────────────────────────────

def test_einsaetze_page_loads_empty_when_no_dibos_data():
    """Eigene, frisch angelegte Org (statt der gemeinsam genutzten ORG_ID=1):
    andere Testdateien (test_dibos_enrich.py) legen unter ORG_ID=1 Einsätze mit
    dibos_*-Feldern an, was die "leer"-Erwartung hier sonst je nach Testreihenfolge
    brechen würde (dieselbe Klasse Cross-File-Kollision wie bei den Objekt-BMA-
    Tests, siehe test_dibos_enrich.py-Kommentar dort)."""
    system_admin_id = _setup_system_admin("dibos_einsaetze_empty_user")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.master import FireDept
        org = FireDept(slug="dibos-einsaetze-empty", name="DIBOS-Einsaetze-Test (leer)",
                       color="#336699", bos="Feuerwehr")
        db.add(org)
        db.flush()
        org_id = org.id
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_einsaetze_empty_user", "Test1234!")

    r = client.get("/admin/dibos/einsaetze", params={"org_id": org_id})
    assert r.status_code == 200
    assert "Einsatz-Infos" in r.text
    assert "Noch kein Einsatz mit DIBOS-Zusatzinfos gefunden." in r.text
    assert "Noch kein Einsatz mit DIBOS-Zusatzinfos gefunden." in r.text


def test_einsaetze_page_lists_incident_with_dibos_fields():
    _setup_system_admin("dibos_einsaetze_list_user")

    from datetime import UTC, datetime

    from app.core.tenant import set_tenant_context as _stc
    from app.models.incident import Incident
    from app.models.lis import LisSyncedObject
    from app.services.incident_service import create_incident

    db = SessionLocal()
    _stc(db, ORG_ID)
    try:
        incident = create_incident(
            db, alarm_type_code="T2", primary_org_id=ORG_ID,
            address_street="Unterlinden", address_no="23", address_city="Wolfurt",
            started_at=datetime(2026, 7, 21, 17, 27, tzinfo=UTC),
        )
        incident.lis_operation_number = "f26006436-admininfo"
        incident.dibos_tycod = "t2"
        incident.dibos_diagnose = "Testdiagnose"
        incident.dibos_bma_no = "009401"
        incident.dibos_event_comment = "[Türöffnung] med. Notfall hinter verschlossener Türe"
        db.add(LisSyncedObject(org_id=ORG_ID, obj_type="dibos_comment", lis_id="1", incident_id=incident.id))
        db.add(LisSyncedObject(org_id=ORG_ID, obj_type="dibos_comment", lis_id="2", incident_id=incident.id))
        db.commit()
        incident_id = incident.id
    finally:
        db.close()

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_einsaetze_list_user", "Test1234!")

    r = client.get("/admin/dibos/einsaetze", params={"org_id": ORG_ID})
    assert r.status_code == 200
    assert "f26006436-admininfo" in r.text
    assert "Testdiagnose" in r.text
    assert "009401" in r.text
    assert "Unterlinden" in r.text
    assert f"/einsatz/{incident_id}/info" in r.text
    assert 'badge-pill--gray">2<' in r.text  # Meldungen-Zähler (2 LisSyncedObject-Einträge)


def test_einsaetze_page_reachable_by_org_admin_not_only_system_admin():
    """Gleiche Berechtigungsstufe wie die Einstellungsseite selbst (org_admin) —
    zeigt nur ohnehin schon zugängliche Einsatzdaten der eigenen Org, keine
    zusätzliche PII wie die system_admin-only Diagnose-Sektion."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username="dibos_einsaetze_org_admin", password_hash=hash_password("Test1234!"),
                    display_name="Nur Org-Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_einsaetze_org_admin", "Test1234!")

    r = client.get("/admin/dibos/einsaetze")
    assert r.status_code == 200


def test_org_admin_without_system_admin_cannot_reach_trace_routes():
    """Die Diagnose-/Trace-Routen sind bewusst strenger gegated (require_system_admin)
    als das Config-Formular (require_role org_admin/admin) - Personenbezug in den
    Rohdaten (siehe dibos_capture.py-Modul-Docstring)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username="dibos_org_admin_only", password_hash=hash_password("Test1234!"),
                    display_name="Nur Org-Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "dibos_org_admin_only", "Test1234!")

    # Config-Seite bleibt erreichbar ...
    r = client.get("/admin/dibos", params={"org_id": ORG_ID})
    assert r.status_code == 200

    # ... aber die Diagnose-Route ist ihr verwehrt
    r = client.get("/admin/dibos/trace/status", params={"org_id": ORG_ID})
    assert r.status_code == 403
