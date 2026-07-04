"""Zu-/Absage-Widget im Board-Header: /einsatz/{id}/rsvp.json liefert Zaehler + Namen aus
Teilnahme.rsvp_status, und board.html rendert das Widget (rsvpWidget-Alpine-Komponente)."""
from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.teilnahme import Teilnahme
from app.models.user import Role, User, UserRole
from tests.conftest import TestingSession


def test_render_board_with_rsvp_widget(client):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        role = db.query(Role).filter(Role.code == "incident_leader").first()
        user = User(username="boardrsvp_x", password_hash=hash_password("testpass123"),
                    active=True, org_id=org.id, display_name="Board RSVP")
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))

        incident = Incident(
            primary_org_id=org.id, alarm_type_code="T4", status="active",
            reason="Verkehrsunfall", address_street="Bundesstrasse", address_no="1",
            address_city="Wolfurt", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
        )
        db.add(incident)
        db.flush()

        db.add(Teilnahme(org_id=org.id, bezug_typ="einsatz", bezug_id=incident.id,
                          freitext_name="Max Mustermann", rsvp_status="zugesagt",
                          rsvp_at=datetime.now(UTC), rsvp_source="teams"))
        db.add(Teilnahme(org_id=org.id, bezug_typ="einsatz", bezug_id=incident.id,
                          freitext_name="Erika Musterfrau", rsvp_status="abgesagt",
                          rsvp_at=datetime.now(UTC), rsvp_source="teams"))
        db.commit()
        incident_id = incident.id
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": "boardrsvp_x", "password": "testpass123", "_csrf": csrf})

    r1 = client.get(f"/einsatz/{incident_id}")
    assert r1.status_code == 200, r1.text[:2000]
    assert "rsvpWidget" in r1.text

    r2 = client.get(f"/einsatz/{incident_id}/rsvp.json")
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["zusagen"] == 1
    assert data["absagen"] == 1
    names = {n["name"] for n in data["namen"]}
    assert names == {"Max Mustermann", "Erika Musterfrau"}
