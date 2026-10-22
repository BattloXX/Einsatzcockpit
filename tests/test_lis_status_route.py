"""ECP-05: Die Status-Route sendet nur tatsaechliche Aenderungen ans LIS."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.master import VehicleMaster
from app.models.user import Role, User, UserRole

ORG_ID = 1


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _setup(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "admin").first()
        if role is None:
            role = Role(code="admin", name="admin")
            db.add(role)
            db.flush()
        u = User(username=username, password_hash=hash_password("Test1234!"),
                 display_name="Admin", org_id=ORG_ID, active=True)
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        inc = Incident(primary_org_id=ORG_ID, alarm_type_code="T1")
        db.add(inc)
        db.flush()
        col = IncidentColumn(incident_id=inc.id, code="active", title="Aktiv", column_kind="vehicles")
        vm = VehicleMaster(dept_id=ORG_ID, code="ECP05", name="ECP05 Fzg", type="TLF")
        db.add_all([col, vm])
        db.flush()
        iv = IncidentVehicle(incident_id=inc.id, column_id=col.id, vehicle_master_id=vm.id,
                             unit_status="Einsatzbereit")
        db.add(iv)
        db.commit()
        return inc.id, iv.id
    finally:
        db.close()


def test_status_route_pusht_nur_bei_aenderung(client):
    inc_id, iv_id = _setup("ecp05_admin")
    _login(client, "ecp05_admin")
    csrf = client.cookies.get("ec_csrf")
    url = f"/einsatz/{inc_id}/fahrzeug/{iv_id}/status"
    with patch("app.services.lis.lis_sync.push_vehicle_status_to_lis", new_callable=AsyncMock) as push:
        r1 = client.post(url, data={"unit_status": "Am Einsatzort", "_csrf": csrf})
        r2 = client.post(url, data={"unit_status": "Am Einsatzort", "_csrf": csrf})
        r3 = client.post(url, data={"unit_status": "Einsatzbereit", "_csrf": csrf})
    assert r1.status_code < 400 and r2.status_code < 400 and r3.status_code < 400
    assert [c.args[1] for c in push.call_args_list] == ["Am Einsatzort", "Einsatzbereit"]
