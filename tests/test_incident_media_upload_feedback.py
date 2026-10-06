"""Regressionstests fuer Upload-Fehlermeldungen bei Einsatz-Medien."""
import io
import json
from urllib.parse import unquote

from PIL import Image

from app.config import settings
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, Message, RescuedPerson
from app.models.user import Role, User, UserRole
from app.routers import ui_incident

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username: str) -> None:
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _rolle(db, code: str):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_upload_ziele(username: str) -> tuple[int, int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Upload Test",
            org_id=ORG_ID,
            active=True,
        )
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="B1", status="active")
        db.add_all([user, incident])
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))
        message_column = IncidentColumn(
            incident_id=incident.id, code="messages", title="Meldungen", column_kind="messages",
        )
        person_column = IncidentColumn(
            incident_id=incident.id, code="persons", title="Personen", column_kind="persons",
        )
        db.add_all([message_column, person_column])
        db.flush()
        message = Message(
            incident_id=incident.id, column_id=message_column.id, title="Upload-Meldung", status="meldung",
        )
        person = RescuedPerson(incident_id=incident.id, column_id=person_column.id, gender="Unbekannt")
        db.add_all([message, person])
        db.commit()
        return incident.id, message.id, person.id
    finally:
        db.close()


def _csrf(client) -> str:
    return client.cookies.get("ec_csrf")


def _png() -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, "PNG")
    return data.getvalue()


def test_meldungs_upload_meldet_abgelehnten_dateityp(client, caplog, tmp_path, monkeypatch):
    incident_id, message_id, _ = _setup_upload_ziele("media_feedback_message_invalid")
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    _login(client, "media_feedback_message_invalid")

    response = client.post(
        f"/einsatz/{incident_id}/meldung/{message_id}/medien",
        data={"_csrf": _csrf(client)},
        files={"files": ("hinweis.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    assert "Dateityp konnte nicht erkannt werden" in response.text
    errors = json.loads(unquote(response.headers["X-Upload-Errors"]))
    assert errors == ["Dateityp konnte nicht erkannt werden und ist nicht erlaubt."]
    assert "Upload abgelehnt" in caplog.text


def test_meldungs_upload_speichert_bild_und_broadcastet(client, tmp_path, monkeypatch):
    incident_id, message_id, _ = _setup_upload_ziele("media_feedback_message_png")
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    broadcasts = []

    async def broadcast(incident_id, payload):
        broadcasts.append((incident_id, payload))

    monkeypatch.setattr(ui_incident.manager, "broadcast", broadcast)
    _login(client, "media_feedback_message_png")

    response = client.post(
        f"/einsatz/{incident_id}/meldung/{message_id}/medien",
        data={"_csrf": _csrf(client)},
        files={"files": ("foto.png", _png(), "image/png")},
    )

    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        message = db.get(Message, message_id)
        assert message is not None
        assert len(message.media) == 1
    finally:
        db.close()
    assert len(broadcasts) == 1
    broadcast_incident_id, payload = broadcasts[0]
    assert broadcast_incident_id == incident_id
    assert payload["type"] == "message_updated"
    assert payload["kind"] == "message"
    assert payload["uid"] == message_id


def test_personen_upload_meldet_abgelehnten_dateityp(client, tmp_path, monkeypatch):
    incident_id, _, person_id = _setup_upload_ziele("media_feedback_person_invalid")
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    _login(client, "media_feedback_person_invalid")

    response = client.post(
        f"/einsatz/{incident_id}/person/{person_id}/medien",
        data={"_csrf": _csrf(client)},
        files={"files": ("hinweis.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    assert "Dateityp konnte nicht erkannt werden" in response.text
    errors = json.loads(unquote(response.headers["X-Upload-Errors"]))
    assert errors == ["Dateityp konnte nicht erkannt werden und ist nicht erlaubt."]
