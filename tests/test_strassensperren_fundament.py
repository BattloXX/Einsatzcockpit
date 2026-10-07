"""Grundtests für Datenmodell und Schalter der Straßensperren."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.road_closure import RoadClosure
from app.models.user import Role, User, UserRole
from app.services.road_closure_flags import strassensperren_effective_enabled


def test_migration_upgrade_and_downgrade(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0257_strassensperren.py"
    spec = importlib.util.spec_from_file_location("migration_0257", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0257.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE org_settings (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE fire_dept (id BIGINT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE user (id BIGINT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE incident (id BIGINT PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            tables = set(sa.inspect(conn).get_table_names())
            assert {
                "road_closure",
                "road_closure_share",
                "road_closure_change",
                "incident_route",
                "incident_road_closure",
            } <= tables
            migration.downgrade()
        assert "road_closure" not in sa.inspect(conn).get_table_names()


def _user(username: str, role_code: str, org_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org_id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        db.commit()
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.get("/login")
    client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")})


def test_road_closure_is_tenant_scoped_and_has_labels():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = db.query(FireDept).first()
        org_b = FireDept(name="Sperren Org B", slug="sperren-org-b")
        db.add(org_b)
        db.flush()
        closure = RoadClosure(
            org_id=org_a.id,
            title="B 1 gesperrt",
            valid_from=datetime.now(UTC).replace(tzinfo=None),
            restriction_type="closed",
        )
        db.add(closure)
        db.commit()
        closure_id, org_b_id = closure.id, org_b.id
    finally:
        db.close()

    db = SessionLocal()
    set_tenant_context(db, org_b_id)
    try:
        assert db.query(RoadClosure).filter(RoadClosure.id == closure_id).first() is None
    finally:
        db.close()

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        closure = db.get(RoadClosure, closure_id)
        assert closure.restriction_label == "Vollsperre"
        assert closure.priority_label == "Normal"
        assert closure.geometry_status_label == "Keine Geometrie"
    finally:
        db.close()


def test_effective_enabled_matrix():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        org_settings = db.query(OrgSettings).filter_by(org_id=org.id).first() or OrgSettings(org_id=org.id)
        db.add(org_settings)
        flag = db.query(SystemSettings).filter_by(key="strassensperren_module_enabled").first()
        if flag is None:
            flag = SystemSettings(key="strassensperren_module_enabled", value="false")
            db.add(flag)
        cases = ((False, False, False), (False, True, False), (True, False, False), (True, True, True))
        for system, org_enabled, expected in cases:
            flag.value = str(system).lower()
            org_settings.strassensperren_modul_aktiv = org_enabled
            db.flush()
            assert strassensperren_effective_enabled(org.id, db) is expected
        db.rollback()
    finally:
        db.close()


def test_system_toggle_requires_system_admin_and_org_form_is_gated(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        org_id = org.id
    finally:
        db.close()
    _user("sperren_org_admin", "org_admin", org_id)
    _login(client, "sperren_org_admin")
    response = client.post(
        "/admin/settings/system/strassensperren-toggle",
        data={"_csrf": client.cookies.get("ec_csrf"), "enabled_raw": "1"},
    )
    assert response.status_code == 403

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first() or OrgSettings(org_id=org_id)
        db.add(settings)
        settings.strassensperren_modul_aktiv = False
        # Andere Tests lassen das System-Flag ggf. an – hier explizit aus.
        flag = db.query(SystemSettings).filter_by(key="strassensperren_module_enabled").first()
        if flag is None:
            flag = SystemSettings(key="strassensperren_module_enabled", value="false")
            db.add(flag)
        flag.value = "false"
        db.commit()
    finally:
        db.close()
    response = client.post(
        "/admin/settings/org",
        data={
            "_csrf": client.cookies.get("ec_csrf"),
            "strassensperren_modul_aktiv_raw": "1",
            "routing_start_lat": "47.5",
            "routing_start_lng": "9.7",
            "routing_start_label": "Ausfahrt",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings = db.query(OrgSettings).filter_by(org_id=org_id).one()
        assert settings.strassensperren_modul_aktiv is False
        assert (settings.routing_start_lat, settings.routing_start_lng, settings.routing_start_label) == (
            47.5,
            9.7,
            "Ausfahrt",
        )
    finally:
        db.close()

    _user("sperren_system_admin", "system_admin", org_id)
    _login(client, "sperren_system_admin")
    response = client.post(
        "/admin/settings/system/strassensperren-toggle",
        data={"_csrf": client.cookies.get("ec_csrf"), "enabled_raw": "1", "org_id": org_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    _login(client, "sperren_org_admin")
    response = client.post(
        "/admin/settings/org",
        data={"_csrf": client.cookies.get("ec_csrf"), "strassensperren_modul_aktiv_raw": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgSettings).filter_by(org_id=org_id).one().strassensperren_modul_aktiv is True
    finally:
        db.close()
