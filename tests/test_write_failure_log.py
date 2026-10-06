"""Regressionstests fuer das Protokoll fehlgeschlagener Schreibzugriffe."""
import logging

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, Message
from app.models.user import Role, User, UserRole

ORG_ID = 1


def _anmelden(client, username: str) -> None:
    client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _vorbereiten(username: str) -> tuple[int, int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"), display_name="Log Test", org_id=ORG_ID)
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="B1", status="active")
        db.add_all([user, incident])
        db.flush()
        role = db.query(Role).filter(Role.code == "incident_leader").first()
        assert role is not None
        db.add(UserRole(user_id=user.id, role_id=role.id))
        column = IncidentColumn(incident_id=incident.id, code="messages", title="Meldungen", column_kind="messages")
        db.add(column)
        db.flush()
        message = Message(incident_id=incident.id, column_id=column.id, title="Meldung", status="meldung")
        db.add(message)
        db.commit()
        return user.id, incident.id, message.id
    finally:
        db.close()


def test_fehlgeschlagener_post_wird_mit_benutzer_protokolliert(client, caplog):
    user_id, incident_id, _ = _vorbereiten("write_failure_404")
    _anmelden(client, "write_failure_404")
    caplog.set_level(logging.WARNING, logger="einsatzleiter.write_failures")
    caplog.clear()

    response = client.post(
        f"/einsatz/{incident_id}/meldung/999999",
        data={"_csrf": client.cookies.get("ec_csrf"), "title": "Test"},
    )

    assert response.status_code == 404
    assert f"status=404" in caplog.text
    assert f"user_id={user_id}" in caplog.text


def test_erfolgreicher_post_wird_nicht_protokolliert(client, caplog):
    _, incident_id, message_id = _vorbereiten("write_failure_success")
    _anmelden(client, "write_failure_success")
    caplog.set_level(logging.WARNING, logger="einsatzleiter.write_failures")
    caplog.clear()

    response = client.post(
        f"/einsatz/{incident_id}/meldung/{message_id}",
        data={"_csrf": client.cookies.get("ec_csrf"), "title": "Aktualisiert"},
    )

    assert response.status_code == 200
    assert not [record for record in caplog.records if record.name == "einsatzleiter.write_failures"]


def test_get_fehler_wird_nicht_protokolliert(client, caplog):
    _vorbereiten("write_failure_get")
    _anmelden(client, "write_failure_get")
    caplog.set_level(logging.WARNING, logger="einsatzleiter.write_failures")
    caplog.clear()

    response = client.get("/einsatz/999999/meldung/999999/detail")

    assert response.status_code == 404
    assert not [record for record in caplog.records if record.name == "einsatzleiter.write_failures"]
