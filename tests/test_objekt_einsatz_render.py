"""Live-Render der neuen Vollbild-Layouts:
- Einsatzinformation (/einsatz/{id}/info) mit zweispaltigem Grid
- Objekt-Anlage (/objekte/neu) mit OSM-Adresssuche
"""
from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt
from app.models.user import Role, User, UserRole


def _login(client, username, password):
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


def _setup(username, *, objekt_modul=False):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Render Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        for code in ("org_admin", "objekt_verwalter"):
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        if objekt_modul:
            sys_row = db.get(SystemSettings, "objekt_module_enabled")
            if sys_row is None:
                db.add(SystemSettings(key="objekt_module_enabled", value="true"))
            else:
                sys_row.value = "true"
            os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
            if os_row is None:
                os_row = OrgSettings(org_id=org.id)
                db.add(os_row)
            os_row.objekt_module_enabled = True

        inc = Incident(primary_org_id=org.id, alarm_type_code="F14",
                       reason="BMA 1044 Rauchmelder ausgelöst",
                       address_street="Dammstraße", address_no="64", address_city="Wolfurt",
                       lat=47.4652, lng=9.7503, status="active",
                       started_at=datetime.now(UTC))
        db.add(inc)
        db.commit()
        return org.id, inc.id
    finally:
        db.close()


def test_einsatzinfo_rendert_vollbild_grid(client):
    _, inc_id = _setup("render_info_user")
    _login(client, "render_info_user", "Test1234!")

    r = client.get(f"/einsatz/{inc_id}/info")
    assert r.status_code == 200, r.text[:500]
    # Zweispaltiges Layout + Karten-Container + Hydranten-Karte
    assert "ei-grid" in r.text
    assert "einsatz-info-karte" in r.text
    assert "Nächste Hydranten" in r.text


def test_objekt_neu_rendert_adresssuche(client):
    _setup("render_neu_user", objekt_modul=True)
    _login(client, "render_neu_user", "Test1234!")

    r = client.get("/objekte/neu")
    assert r.status_code == 200, r.text[:500]
    # OSM-Adresssuche-UI ist vorhanden
    assert "adress-suche" in r.text
    assert "objektAdresse" in r.text
    assert 'name="lat"' in r.text


def test_objekt_einsatzansicht_rendert_zweispaltig(client):
    _setup("render_oe_user", objekt_modul=True)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        obj = Objekt(org_id=org.id, nummer=999, name="Render Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN,
                     strasse="Dammstraße", hausnummer="64", ort="Wolfurt",
                     lat=47.4652, lng=9.7503)
        db.add(obj)
        db.commit()
        obj_id = obj.id
    finally:
        db.close()

    _login(client, "render_oe_user", "Test1234!")
    r = client.get(f"/objekte/{obj_id}/einsatz")
    assert r.status_code == 200, r.text[:500]
    assert "oe-grid" in r.text
    assert "karte/einbettung" in r.text
