"""Bearbeiter (recorder) duerfen am Board Personen anlegen (Vorfall 2026-10-06:
Tablet bekam 20x 403 auf POST /einsatz/{id}/person, obwohl der Dialog angezeigt wurde)."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, RescuedPerson
from app.models.user import Role, User, UserRole

ORG_ID = 1


def _setup(username: str, rolle: str) -> tuple[int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == rolle).first()
        if role is None:
            role = Role(code=rolle, name=rolle)
            db.add(role)
            db.flush()
        user = User(
            username=username, password_hash=hash_password("Test1234!"),
            display_name="Person Test", org_id=ORG_ID, active=True,
        )
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="B1", status="active")
        db.add_all([user, incident])
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        spalte = IncidentColumn(
            incident_id=incident.id, code="rescued", title="Personen", column_kind="rescued",
        )
        db.add(spalte)
        db.commit()
        return incident.id, spalte.id
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _anlegen(client, incident_id: int, spalte_id: int):
    return client.post(
        f"/einsatz/{incident_id}/person",
        data={"column_id": str(spalte_id), "name": "Max Muster"},
        headers={"X-CSRF-Token": client.cookies.get("ec_csrf"), "HX-Request": "true"},
    )


def test_recorder_darf_person_anlegen(client):
    incident_id, spalte_id = _setup("person-recorder", "recorder")
    _login(client, "person-recorder")
    response = _anlegen(client, incident_id, spalte_id)
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(RescuedPerson).filter(RescuedPerson.incident_id == incident_id).count() == 1
    finally:
        db.close()


def test_readonly_darf_keine_person_anlegen(client):
    incident_id, spalte_id = _setup("person-readonly", "readonly")
    _login(client, "person-readonly")
    assert _anlegen(client, incident_id, spalte_id).status_code == 403
