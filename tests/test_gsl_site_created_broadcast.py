"""GSL-01: Alarm-Trigger-Pfade melden neue Einsatzstellen live an offene Boards."""
from __future__ import annotations

import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import MajorIncident
from app.models.master import AlarmType, FireDept
from app.models.user import Role, User, UserRole


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _setup(username: str, *, trigger: bool, laufende_lage: bool) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"gsl01-{uuid.uuid4().hex[:8]}", name="GSL01 Org", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        role = db.query(Role).filter(Role.code == "incident_leader").first() or Role(
            code="incident_leader", name="incident_leader")
        db.add(role)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="GSL01", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.add(AlarmType(org_id=org.id, code="T9", label="Gross", triggers_major_incident=trigger))
        if laufende_lage:
            db.add(MajorIncident(org_id=org.id, name="Laufende Lage"))
        db.commit()
        return org.id
    finally:
        db.close()


def _anlegen(client, monkeypatch, username):
    calls = []

    async def fake_stelle_angelegt(db, lage, site_id):
        calls.append((lage.id, site_id))

    async def fake_notify(*a, **kw):
        return None

    monkeypatch.setattr("app.services.gsl_broadcast.stelle_angelegt", fake_stelle_angelegt)
    monkeypatch.setattr("app.services.incident_notify.notify_incident_created", fake_notify)
    monkeypatch.setattr("app.services.gsl_notify.notify_gsl_created", fake_notify)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/einsatz/neu", data={
        "_csrf": csrf, "alarm_type_code": "T9", "confirm_real_incident": "true",
        "address_street": "Teststrasse", "address_no": "1", "address_city": "Wolfurt",
        "report_text": "GSL-01",
    }, follow_redirects=False)
    assert r.status_code in (200, 303), r.text
    return calls


def test_trigger_alarmart_broadcastet_neue_stelle(client, monkeypatch):
    _setup("gsl01_trigger", trigger=True, laufende_lage=False)
    calls = _anlegen(client, monkeypatch, "gsl01_trigger")
    assert len(calls) == 1


def test_uebernahme_in_laufende_lage_broadcastet_neue_stelle(client, monkeypatch):
    _setup("gsl01_adopt", trigger=False, laufende_lage=True)
    calls = _anlegen(client, monkeypatch, "gsl01_adopt")
    assert len(calls) == 1
