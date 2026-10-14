"""Tests fuer die Normalisierung der Alarm-Ausschlussbegriffe."""

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.user import Role, User, UserRole
from app.routers.ui_settings import normalisiere_alarm_ausschluss_begriffe


def test_normalisierung_entfernt_leere_zeilen_duplikate_und_begrenzt_laengen():
    raw = "  Bereits erledigt  \n\nBEREITS ERLEDIGT\n" + "x" * 101

    assert normalisiere_alarm_ausschluss_begriffe(raw) == f"Bereits erledigt\n{'x' * 100}"


def test_normalisierung_leerer_eingabe_ergibt_none():
    assert normalisiere_alarm_ausschluss_begriffe(" \n\t ") is None


def test_settings_post_speichert_liste_und_teilformular_erhaelt_sie(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_settings = db.query(OrgSettings).filter_by(org_id=1).first()
        if org_settings is None:
            org_settings = OrgSettings(org_id=1)
            db.add(org_settings)
        org_settings.alarm_ausschluss_begriffe = "alter Begriff"
        role = db.query(Role).filter_by(code="org_admin").one()
        user = User(
            username="alarm-ausschluss-settings-admin",
            password_hash=hash_password("Test1234!"),
            display_name="Alarm Einstellungen",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    login = client.post("/login", data={
        "username": "alarm-ausschluss-settings-admin",
        "password": "Test1234!",
        "_csrf": csrf,
    })
    assert login.status_code == 200

    csrf = client.cookies.get("ec_csrf")
    saved = client.post("/admin/settings/org", data={
        "_csrf": csrf,
        "alarm_ausschluss_begriffe": " Bereits erledigt \nBEREITS ERLEDIGT\nanderer Begriff ",
    }, follow_redirects=False)
    assert saved.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_settings = db.query(OrgSettings).filter_by(org_id=1).one()
        assert org_settings.alarm_ausschluss_begriffe == "Bereits erledigt\nanderer Begriff"
    finally:
        db.close()

    csrf = client.cookies.get("ec_csrf")
    retained = client.post("/admin/settings/org", data={"_csrf": csrf}, follow_redirects=False)
    assert retained.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_settings = db.query(OrgSettings).filter_by(org_id=1).one()
        assert org_settings.alarm_ausschluss_begriffe == "Bereits erledigt\nanderer Begriff"
    finally:
        # Session-weite Test-DB: Begriffe nicht in andere Alarm-Tests durchsickern lassen.
        db.rollback()
        db.query(OrgSettings).filter_by(org_id=1).one().alarm_ausschluss_begriffe = None
        db.commit()
        db.close()
