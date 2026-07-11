"""Kennzeichen-Pflege in den Ressourcen-Stammdaten (/admin/fahrzeuge), nicht mehr
nur im Fahrtenbuch-Admin — Nutzer-Feedback 2026-07-11. `VehicleMaster.kennzeichen`
ist dieselbe Spalte, die auch das Fahrtenbuch-Admin bearbeitet."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import VehicleMaster
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


def _admin_user(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        db.commit()
    finally:
        db.close()


def test_fahrzeug_neu_speichert_kennzeichen(client):
    _admin_user("kz_admin_neu")
    _login(client, "kz_admin_neu", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/fahrzeuge/neu", data={
        "code": "KZTEST", "name": "Test-Fahrzeug", "type": "",
        "kennzeichen": "B 1234", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = db.query(VehicleMaster).filter(VehicleMaster.code == "KZTEST").first()
        assert v is not None
        assert v.kennzeichen == "B 1234"
    finally:
        db.close()


def test_fahrzeug_edit_aktualisiert_kennzeichen(client):
    _admin_user("kz_admin_edit")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = VehicleMaster(dept_id=ORG_ID, code="KZEDIT", name="Edit-Fahrzeug", kennzeichen="ALT-1")
        db.add(v)
        db.commit()
        vehicle_id = v.id
    finally:
        db.close()

    _login(client, "kz_admin_edit", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/admin/fahrzeuge/{vehicle_id}/edit", data={
        "code": "KZEDIT", "name": "Edit-Fahrzeug", "type": "",
        "kennzeichen": "NEU-2", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = db.get(VehicleMaster, vehicle_id)
        assert v.kennzeichen == "NEU-2"
    finally:
        db.close()


def test_fremdorganisation_neu_und_edit_speichern_kennzeichen(client):
    _admin_user("kz_admin_extern")
    _login(client, "kz_admin_extern", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/fahrzeuge/neu-extern", data={
        "org_name": "FF Nachbarort", "org_short": "NAO",
        "code": "HLP-1", "name": "Hochleistungspumpe", "type": "",
        "kennzeichen": "X 999", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = db.query(VehicleMaster).filter(VehicleMaster.code == "HLP-1").first()
        assert v is not None
        assert v.kennzeichen == "X 999"
        vehicle_id = v.id
    finally:
        db.close()

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/admin/fahrzeuge/{vehicle_id}/edit-extern", data={
        "org_name": "FF Nachbarort", "org_short": "NAO",
        "code": "HLP-1", "name": "Hochleistungspumpe", "type": "",
        "kennzeichen": "X 888", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = db.get(VehicleMaster, vehicle_id)
        assert v.kennzeichen == "X 888"
    finally:
        db.close()


def test_fahrzeuge_liste_zeigt_kennzeichen(client):
    _admin_user("kz_admin_liste")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        v = VehicleMaster(dept_id=ORG_ID, code="KZLIST", name="Listen-Fahrzeug", kennzeichen="LI-777")
        db.add(v)
        db.commit()
    finally:
        db.close()

    _login(client, "kz_admin_liste", "Test1234!")
    r = client.get("/admin/fahrzeuge")
    assert r.status_code == 200
    assert "LI-777" in r.text
