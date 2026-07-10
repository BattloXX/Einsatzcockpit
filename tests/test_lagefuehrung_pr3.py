"""Lageführung-Modul Phase 3: Wasserstellen-Layer, manuelles Fahrzeug-Pinning
(auch ohne Koordinaten) und Fahrzeug-Hinzufügen von der Lagekarte aus (mit
Rücksprung statt zum Board — Vehicle bleibt dasselbe Board-Modell, erscheint
also automatisch auch dort).
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.major_incident import VehiclePosition
from app.models.master import OrgSettings, SystemSettings, VehicleMaster
from app.models.user import Role, User, UserRole
from app.models.wasserstelle import Wasserstelle

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


def _setup(username, *, org_id=ORG_ID, rollen=("incident_leader",), lat=47.4652, lng=9.7503,
           mit_spalte=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft3 Test", org_id=org_id, active=True)
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
                             lat=lat, lng=lng)
        db.add(incident)
        db.flush()
        if mit_spalte:
            db.add(IncidentColumn(
                incident_id=incident.id, code="active", title="Tatsächlich im Einsatz",
                column_kind="vehicles", is_fixed=True,
            ))
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


# ── Wasserstellen-Layer ───────────────────────────────────────────────────────────

def test_wasserstellen_json_filtert_nach_radius(client):
    _, incident_id = _setup("lft3_ws_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(Wasserstelle(org_id=ORG_ID, bezeichnung="Hydrant nah", typ="ueberflur",
                            lat=47.4655, lng=9.7506, status="bereit"))
        # ~1.2 Grad entfernt -> weit außerhalb des 2-km-Radius
        db.add(Wasserstelle(org_id=ORG_ID, bezeichnung="Hydrant fern", typ="unterflur",
                            lat=48.7, lng=9.7503, status="bereit"))
        db.commit()
    finally:
        db.close()

    _login(client, "lft3_ws_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/wasserstellen.json")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["ref"] == "Hydrant nah"
    assert data[0]["icon_kat"] == "ueberflur"


def test_wasserstellen_json_leer_ohne_koordinaten(client):
    user_id, _ = _setup("lft3_ws_nokoord_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active")
        db.add(incident)
        db.commit()
        incident_id = incident.id
    finally:
        db.close()

    _login(client, "lft3_ws_nokoord_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/wasserstellen.json")
    assert r.status_code == 200
    assert r.json() == []


# ── Manuelles Fahrzeug-Pinning (auch ohne Koordinaten) ───────────────────────────

def test_vehicle_ohne_position_erscheint_in_liste_ohne_koordinate(client):
    _, incident_id = _setup("lft3_vehnopos_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="LFT3", name="Testfahrzeug")
        db.add(vm)
        db.flush()
        col = db.query(IncidentColumn).filter_by(incident_id=incident_id, code="active").first()
        db.add(IncidentVehicle(incident_id=incident_id, column_id=col.id, vehicle_master_id=vm.id))
        db.commit()
    finally:
        db.close()

    _login(client, "lft3_vehnopos_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/vehicles.json")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["lat"] is None and data[0]["lng"] is None


def test_vehicle_manuell_platzieren(client):
    _, incident_id = _setup("lft3_pin_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="LFT3P", name="Testfahrzeug Pin")
        db.add(vm)
        db.flush()
        col = db.query(IncidentColumn).filter_by(incident_id=incident_id, code="active").first()
        iv = IncidentVehicle(incident_id=incident_id, column_id=col.id, vehicle_master_id=vm.id)
        db.add(iv)
        db.commit()
        iv_id = iv.id
    finally:
        db.close()

    _login(client, "lft3_pin_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/vehicles/{iv_id}/pin",
        json={"lat": 47.46, "lng": 9.75},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text[:300]

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/vehicles.json")
    data = r.json()
    assert data[0]["lat"] == 47.46
    assert data[0]["lng"] == 9.75
    assert data[0]["position_source"] == "manual"

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pos = db.query(VehiclePosition).filter(VehiclePosition.source == "manual").first()
        assert pos is not None
        assert pos.incident_id is None, "VehiclePosition.incident_id ist nur an major_incident gebunden"
    finally:
        db.close()


def test_vehicle_pin_erfordert_editierrecht(client):
    _, incident_id = _setup("lft3_pin_viewer", rollen=("readonly",))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="LFT3V", name="Viewer-Fahrzeug")
        db.add(vm)
        db.flush()
        col = db.query(IncidentColumn).filter_by(incident_id=incident_id, code="active").first()
        iv = IncidentVehicle(incident_id=incident_id, column_id=col.id, vehicle_master_id=vm.id)
        db.add(iv)
        db.commit()
        iv_id = iv.id
    finally:
        db.close()

    _login(client, "lft3_pin_viewer", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/vehicles/{iv_id}/pin",
        json={"lat": 47.46, "lng": 9.75},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403


# ── Fahrzeug-Hinzufügen mit Rücksprung zur Lagekarte ─────────────────────────────

def test_fahrzeug_hinzufuegen_next_redirect_zur_lagekarte(client):
    _, incident_id = _setup("lft3_add_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="LFT3ADD", name="Neues Fahrzeug")
        db.add(vm)
        db.commit()
        vm_id = vm.id
    finally:
        db.close()

    _login(client, "lft3_add_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    next_url = f"/einsatz/{incident_id}/lagefuehrung"
    r = client.post(
        f"/einsatz/{incident_id}/fahrzeug-hinzufuegen",
        data={"vehicle_master_id": vm_id, "next": next_url, "_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == next_url

    # Dasselbe Board-Modell -> das Fahrzeug erscheint jetzt auch auf der Lagekarte/im Board
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/vehicles.json")
    labels = [v["label"] for v in r.json()]
    assert any("LFT3ADD" in (lbl or "") for lbl in labels)


def test_fahrzeug_hinzufuegen_ohne_next_faellt_auf_board_zurueck(client):
    _, incident_id = _setup("lft3_add_default_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="LFT3DFLT", name="Default-Fahrzeug")
        db.add(vm)
        db.commit()
        vm_id = vm.id
    finally:
        db.close()

    _login(client, "lft3_add_default_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/einsatz/{incident_id}/fahrzeug-hinzufuegen",
        data={"vehicle_master_id": vm_id, "_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/einsatz/{incident_id}"


def test_fahrzeug_hinzufuegen_open_redirect_wird_ignoriert(client):
    _, incident_id = _setup("lft3_add_evil_user")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vm = VehicleMaster(dept_id=ORG_ID, code="LFT3EVIL", name="Evil-Redirect-Fahrzeug")
        db.add(vm)
        db.commit()
        vm_id = vm.id
    finally:
        db.close()

    _login(client, "lft3_add_evil_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    for evil in ("https://evil.example.com/", "//evil.example.com/"):
        r = client.post(
            f"/einsatz/{incident_id}/fahrzeug-hinzufuegen",
            data={"vehicle_master_id": vm_id, "next": evil, "_csrf": csrf},
            follow_redirects=False,
        )
        # Fahrzeug ist ggf. schon aus der ersten Schleifen-Iteration zugewiesen ->
        # beide Zweige (Erstzuweisung 201-artig via 303, oder "already attached" 303)
        # muessen denselben sicheren Default-Redirect liefern.
        assert r.status_code == 303
        assert r.headers["location"] == f"/einsatz/{incident_id}"
