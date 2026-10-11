"""M3 regression tests for service-created messages and mail-style attachments."""
import io
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentColumn
from app.services import incident_service, media_service


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    incident = Incident(primary_org_id=1, alarm_type_code="T1", status="active", started_at=datetime.now(UTC))
    session.add(incident)
    session.flush()
    session.add(IncidentColumn(incident_id=incident.id, code="messages", column_kind="messages", title="Meldungen"))
    session.commit()
    yield session, incident
    session.close()
    Base.metadata.drop_all(engine)


@pytest.mark.asyncio
async def test_add_message_without_user_writes_card_audit_and_ws(db, monkeypatch):
    session, incident = db
    msg = incident_service.add_message(
        session, incident, title="Mail", detail="ohne Benutzer", author_name="E-Mail (a@example.test)",
    )
    assert msg.id and msg.column_id
    assert "message" in session.get(IncidentColumn, msg.column_id).card_order
    assert session.query(incident_service.IncidentChange).filter_by(action="message.created").count() == 1
    events = []

    async def fake_broadcast(incident_id, event):
        events.append((incident_id, event))

    monkeypatch.setattr("app.services.broadcast.manager.broadcast", fake_broadcast)
    await incident_service.broadcast_message_created(incident.id, msg)
    assert events == [(incident.id, {"type": "message_created", "column_id": msg.column_id, "vehicle_uid": None})]


@pytest.mark.asyncio
async def test_store_bytes_for_message_handles_image_pdf_and_download_file(db):
    session, incident = db
    msg = incident_service.add_message(session, incident, title="Anhang", detail=None, author_name=None)
    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (2, 2)).save(png, "PNG")
    image = await media_service.store_bytes_for_message(png.getvalue(), "bild.png", "image/png", msg, session, None)
    pdf = await media_service.store_bytes_for_message(b"%PDF-1.4\n", "a.pdf", "application/pdf", msg, session, None)
    file = await media_service.store_bytes_for_message(
        b"hello", "../brief.txt", "text/plain", msg, session, None, allow_datei=True,
    )
    assert (image.kind, pdf.kind, file.kind) == ("image", "pdf", "datei")
    assert file.uploaded_by_user_id is None and file.original_filename == "brief.txt"


@pytest.mark.asyncio
async def test_store_bytes_without_allow_datei_rejects_unsupported_type(db):
    session, incident = db
    msg = incident_service.add_message(session, incident, title="Anhang", detail=None, author_name=None)
    with pytest.raises(Exception, match="nicht erlaubt|nicht unterstuetzt"):
        await media_service.store_bytes_for_message(b"hello", "a.txt", "text/plain", msg, session, None)


@pytest.mark.asyncio
async def test_store_bytes_rejects_oversized_download_file(db, monkeypatch):
    session, incident = db
    msg = incident_service.add_message(session, incident, title="Anhang", detail=None, author_name=None)
    monkeypatch.setattr(media_service, "MAX_MESSAGE_FILE_BYTES", 3)
    with pytest.raises(Exception, match="Datei zu gross"):
        await media_service.store_bytes_for_message(
            b"four", "a.txt", "text/plain", msg, session, None, allow_datei=True,
        )


def test_download_only_message_file_is_attachment_nosniff_and_cross_org_forbidden(db):
    from app.routers.ui_media import _serve_file

    session, incident = db
    msg = incident_service.add_message(session, incident, title="Anhang", detail=None, author_name=None)
    media = __import__("asyncio").run(
        media_service.store_bytes_for_message(
            b"<svg/>", "evil.svg", "image/svg+xml", msg, session, None, allow_datei=True,
        ),
    )
    session.flush()
    owner = SimpleNamespace(org_id=1, roles=[])
    response = _serve_file(owner, media, session)
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.media_type == "application/octet-stream"
    assert _serve_file(SimpleNamespace(org_id=2, roles=[]), media, session).status_code == 403


def test_create_message_route_delegates_to_service(client, setup_db, monkeypatch):
    """The UI route keeps its fragment response while using the service path."""
    from app.core.security import hash_password
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import Role, User, UserRole

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username="m3-message-route", display_name="M3 Route",
            password_hash=hash_password("Test1234!"), org_id=1, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="incident_leader").one().id))
        incident = Incident(primary_org_id=1, alarm_type_code="T1", status="active", started_at=datetime.now(UTC))
        db.add(incident)
        db.flush()
        db.add(IncidentColumn(incident_id=incident.id, code="messages", column_kind="messages", title="Meldungen"))
        db.commit()
        incident_id = incident.id
    finally:
        db.close()
    client.get("/login")
    login = client.post("/login", data={
        "username": "m3-message-route", "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    })
    assert login.status_code == 200
    original = incident_service.add_message
    calls = []

    def wrapped(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(incident_service, "add_message", wrapped)
    response = client.post(
        f"/einsatz/{incident_id}/meldung",
        data={"title": "Route", "detail": "Regression", "_csrf": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 200
    assert calls and calls[0]["title"] == "Route"
