"""Admin-UI: GSL-Profil von Geräten (Einheitenmodus, Plan 9.2 / 11.2)."""
from __future__ import annotations

import secrets

from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, VehicleMaster
from app.models.user import AuditLog, DeviceToken, Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password="Test1234!"):
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


def _make_admin(db, username, org_id=ORG_ID):
    admin = User(username=username, password_hash=hash_password("Test1234!"),
                 display_name="Admin", org_id=org_id, active=True)
    db.add(admin)
    db.flush()
    db.add(UserRole(user_id=admin.id, role_id=_rolle(db, "admin").id))
    return admin


def _make_geraet(db, org_id, label, vehicle_id=None, gsl_profil=None):
    geraet_user = User(username=f"geraet_{secrets.token_hex(4)}", password_hash=hash_password("x"),
                       display_name=label, org_id=org_id, active=True, is_device=True)
    db.add(geraet_user)
    db.flush()
    dt = DeviceToken(label=label, token_hash=hash_api_key(secrets.token_urlsafe(16)),
                     user_id=geraet_user.id, vehicle_master_id=vehicle_id, gsl_profil=gsl_profil)
    db.add(dt)
    db.flush()
    return dt


def _session():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def test_anlage_mit_fahrzeug_setzt_profil_einheit_ohne_fahrzeug_fuehrung(client):
    db = _session()
    try:
        _make_admin(db, "gsl_profil_admin_anlage")
        vehicle = VehicleMaster(dept_id=ORG_ID, code="TLF-GSL", name="TLF GSL-Test", type="TLF")
        db.add(vehicle)
        db.commit()
        vehicle_id = vehicle.id
    finally:
        db.close()

    _login(client, "gsl_profil_admin_anlage")
    csrf = client.cookies.get("ec_csrf")
    r1 = client.post("/admin/geraete-login/neu", data={
        "label": "Tablet mit Fahrzeug", "device_type": "unit",
        "vehicle_master_id": str(vehicle_id), "_csrf": csrf,
    }, follow_redirects=False)
    assert r1.status_code == 200
    r2 = client.post("/admin/geraete-login/neu", data={
        "label": "Tablet ohne Fahrzeug", "device_type": "unit", "_csrf": csrf,
    }, follow_redirects=False)
    assert r2.status_code == 200

    db = _session()
    try:
        mit = db.query(DeviceToken).filter(DeviceToken.label == "Tablet mit Fahrzeug").one()
        ohne = db.query(DeviceToken).filter(DeviceToken.label == "Tablet ohne Fahrzeug").one()
        assert mit.vehicle_master_id == vehicle_id
        assert mit.gsl_profil == "einheit"
        assert ohne.gsl_profil == "fuehrung"
    finally:
        db.close()


def test_profil_umstellen_mit_audit(client):
    db = _session()
    try:
        _make_admin(db, "gsl_profil_admin_umstellen")
        dt = _make_geraet(db, ORG_ID, "Tablet Umstellen")
        db.commit()
        dt_id = dt.id
    finally:
        db.close()

    _login(client, "gsl_profil_admin_umstellen")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/admin/geraete-login/{dt_id}/gsl-profil",
                    data={"gsl_profil": "einheit", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303

    db = _session()
    try:
        assert db.get(DeviceToken, dt_id).gsl_profil == "einheit"
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.action == "admin.device_token.gsl_profil", AuditLog.entity_id == dt_id)
            .one()
        )
        assert '"einheit"' in (audit.payload_json or "")
    finally:
        db.close()

    # Ungültiger/leerer Wert setzt auf Altverhalten (NULL) zurück.
    r = client.post(f"/admin/geraete-login/{dt_id}/gsl-profil",
                    data={"gsl_profil": "irgendwas", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    db = _session()
    try:
        assert db.get(DeviceToken, dt_id).gsl_profil is None
    finally:
        db.close()


def test_profil_fremder_org_nicht_aenderbar(client):
    db = _session()
    try:
        _make_admin(db, "gsl_profil_admin_fremd")
        fremd = FireDept(slug="gsl-profil-fremd", name="Fremd", color="#00ff00", bos="Feuerwehr")
        db.add(fremd)
        db.flush()
        dt = _make_geraet(db, fremd.id, "Tablet Fremd", gsl_profil="fuehrung")
        db.commit()
        dt_id = dt.id
    finally:
        db.close()

    _login(client, "gsl_profil_admin_fremd")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/admin/geraete-login/{dt_id}/gsl-profil",
                    data={"gsl_profil": "einheit", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 404

    db = _session()
    try:
        assert db.get(DeviceToken, dt_id).gsl_profil == "fuehrung"
    finally:
        db.close()


def test_geraeteliste_zeigt_profilauswahl_und_hinweis(client):
    db = _session()
    try:
        _make_admin(db, "gsl_profil_admin_liste")
        vehicle = VehicleMaster(dept_id=ORG_ID, code="RLF-GSL", name="RLF GSL-Test", type="RLF")
        db.add(vehicle)
        db.flush()
        _make_geraet(db, ORG_ID, "Tablet Altbestand", vehicle_id=vehicle.id)
        db.commit()
    finally:
        db.close()

    _login(client, "gsl_profil_admin_liste")
    r = client.get("/admin/geraete-login")
    assert r.status_code == 200
    assert "/gsl-profil" in r.text
    assert "Für den GSL-Einheitenmodus" in r.text
