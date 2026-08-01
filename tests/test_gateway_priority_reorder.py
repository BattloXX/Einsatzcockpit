"""Tests fuer die Reihenfolge der SMS-Gateway-Tokens in der Admin-Ansicht."""
from __future__ import annotations

import uuid

import pytest

from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, SmsGatewayToken, User, UserRole


def _login_admin(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    return client.cookies.get("ec_csrf")


def _setup_gateways() -> tuple[str, list[int]]:
    kennung = uuid.uuid4().hex
    username = f"gateway_priority_{kennung}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"gateway-priority-{kennung}",
            name="Gateway-Prioritaet Testorganisation",
            color="#123456",
            bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        admin = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Gateway-Prioritaet Test",
            org_id=org.id,
            active=True,
        )
        db.add(admin)
        db.flush()
        role = db.query(Role).filter(Role.code == "admin").one()
        db.add(UserRole(user_id=admin.id, role_id=role.id))
        gateways = [
            SmsGatewayToken(
                label=f"Gateway {priority}",
                token_hash=hash_api_key(f"{kennung}-{priority}"),
                org_id=org.id,
                priority=priority,
            )
            for priority in (10, 20, 30)
        ]
        db.add_all(gateways)
        db.commit()
        return username, [gateway.id for gateway in gateways]
    finally:
        db.close()


def _priorities(ids: list[int]) -> list[int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return [db.get(SmsGatewayToken, token_id).priority for token_id in ids]
    finally:
        db.close()


@pytest.mark.parametrize(
    ("richtung", "erwartet"),
    [("hoch", [20, 10, 30]), ("runter", [10, 30, 20])],
)
def test_reorder_tauscht_mittleres_gateway_mit_nachbarn(client, richtung, erwartet):
    username, ids = _setup_gateways()
    csrf = _login_admin(client, username)

    response = client.post(
        f"/admin/geraete-login/gateway/{ids[1]}/prioritaet",
        data={"richtung": richtung, "_csrf": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/geraete-login?saved=1"
    assert _priorities(ids) == erwartet


@pytest.mark.parametrize(("index", "richtung"), [(0, "hoch"), (2, "runter")])
def test_reorder_am_rand_aendert_nichts(client, index, richtung):
    username, ids = _setup_gateways()
    csrf = _login_admin(client, username)

    response = client.post(
        f"/admin/geraete-login/gateway/{ids[index]}/prioritaet",
        data={"richtung": richtung, "_csrf": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/geraete-login?saved=1"
    assert _priorities(ids) == [10, 20, 30]
