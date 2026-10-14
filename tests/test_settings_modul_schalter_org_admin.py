"""Org-Admins sehen die Org-Schalter (MCP, Kontakte) freigegeben, wenn das System-Flag an ist."""
import re

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import SystemSettings
from app.models.user import Role, User, UserRole


def _set_flag(db, key, value):
    row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    if row is None:
        db.add(SystemSettings(key=key, value=value))
    else:
        row.value = value


def _checkbox(html, name):
    match = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
    assert match, name
    return match.group(0)


def test_org_admin_kann_mcp_und_kontakte_bei_systemfreigabe_schalten(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        alte_werte = {
            key: (row.value if row else None)
            for key in ("mcp_module_enabled", "kontakte_module_enabled")
            for row in [db.query(SystemSettings).filter(SystemSettings.key == key).first()]
        }
        _set_flag(db, "mcp_module_enabled", "true")
        _set_flag(db, "kontakte_module_enabled", "true")
        role = db.query(Role).filter_by(code="org_admin").one()
        user = User(
            username="modul-schalter-org-admin",
            password_hash=hash_password("Test1234!"),
            display_name="Modul Schalter",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    try:
        client.get("/login")
        login = client.post("/login", data={
            "username": "modul-schalter-org-admin",
            "password": "Test1234!",
            "_csrf": client.cookies.get("ec_csrf"),
        })
        assert login.status_code == 200

        page = client.get("/admin/settings")
        assert page.status_code == 200
        assert "disabled" not in _checkbox(page.text, "mcp_modul_aktiv_raw")
        assert "disabled" not in _checkbox(page.text, "kontakte_module_enabled_raw")
    finally:
        # Session-weite Test-DB: System-Flags auf den Ausgangszustand zuruecksetzen.
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            for key, value in alte_werte.items():
                row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
                if value is None and row is not None:
                    db.delete(row)
                elif row is not None:
                    row.value = value
            db.commit()
        finally:
            db.close()
