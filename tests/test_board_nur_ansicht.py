"""Geschlossene Einsaetze bleiben am Board sichtbar, aber unveraenderbar."""
import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, Task
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from tests.test_lagefuehrung_pr4 import _login as _lage_login
from tests.test_lagefuehrung_pr4 import _setup as _lage_setup


def _setup(role_code: str, status: str = "closed") -> tuple[str, int]:
    suffix = uuid.uuid4().hex[:10]
    username = f"board_ro_{suffix}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"board-ro-{suffix}", name="Board Nur-Ansicht")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Board Nur-Ansicht", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status=status)
        db.add(incident)
        db.flush()
        db.add(IncidentColumn(incident_id=incident.id, code="tasks", title="Aufträge", column_kind="tasks"))
        db.commit()
        return username, incident.id
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code == 302


def test_closed_board_is_readonly_and_active_board_is_editable(client):
    username, closed_id = _setup("incident_leader")
    active_username, active_id = _setup("incident_leader", "active")
    _login(client, username)

    closed = client.get(f"/einsatz/{closed_id}")
    _login(client, active_username)
    active = client.get(f"/einsatz/{active_id}")

    assert closed.status_code == 200
    assert "Nur-Ansicht" in closed.text
    assert "Wieder eröffnen" not in closed.text
    assert f'hx-post="/einsatz/{closed_id}/aufgabe"' not in closed.text
    assert f'hx-post="/einsatz/{active_id}/aufgabe"' in active.text


def test_closed_board_blocks_mutations_and_allows_reopen(client):
    username, incident_id = _setup("admin")
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")

    board = client.get(f"/einsatz/{incident_id}")
    assert "Wieder eröffnen" in board.text
    blocked = client.post(f"/einsatz/{incident_id}/aufgabe", data={
        "title": "Darf nicht entstehen", "_csrf": csrf,
    })
    assert blocked.status_code == 409
    assert client.post(f"/einsatz/{incident_id}/meldung", data={"_csrf": csrf}).status_code == 409
    assert client.post(
        f"/einsatz/{incident_id}/fahrzeug/999/status", data={"_csrf": csrf},
    ).status_code == 409
    db = SessionLocal()
    try:
        set_tenant_context(db, None)
        assert db.query(Task).filter(Task.incident_id == incident_id).count() == 0
    finally:
        db.close()
    reopened = client.post(f"/einsatz/{incident_id}/wiedereroeffnen", data={"_csrf": csrf})
    assert reopened.status_code in {200, 302, 303}
    db = SessionLocal()
    try:
        set_tenant_context(db, None)
        assert db.get(Incident, incident_id).status == "active"
    finally:
        db.close()
    created = client.post(f"/einsatz/{incident_id}/aufgabe", data={
        "title": "Aktiver Auftrag", "_csrf": csrf,
    })
    assert created.status_code == 200


def test_org_admin_sees_reopen_button(client):
    username, incident_id = _setup("org_admin")
    _login(client, username)
    assert "Wieder eröffnen" in client.get(f"/einsatz/{incident_id}").text


def test_closed_incident_blocks_lagefuehrung_edits(client):
    username = f"lage_ro_{uuid.uuid4().hex[:8]}"
    _, incident_id = _lage_setup(username)
    db = SessionLocal()
    try:
        set_tenant_context(db, None)
        db.get(Incident, incident_id).status = "closed"
        db.commit()
    finally:
        db.close()
    _lage_login(client, username, "Test1234!")
    response = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "geometry": {"type": "Point", "coordinates": [9.75, 47.46]}},
        headers={"X-CSRF-Token": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 409


def test_closed_board_is_linked_from_archive():
    source = open("app/templates/archive/list.html", encoding="utf-8").read()
    assert 'class="btn btn--xs btn--ghost">Board ansehen' in source
