"""HTTP-Regressionstests für die über Services geführten GSL-Mutationen."""
import io

from PIL import Image

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import CommLogEntry, IncidentSite, MajorIncident, SiteLogEntry
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _lage_mit_benutzer(slug):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=slug, name=slug, color="#ff0000", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(
            username=slug, password_hash=hash_password("Test1234!"), display_name=slug,
            org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "recorder").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        lage = MajorIncident(org_id=org.id, name="Testlage")
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Teststelle")
        db.add(site)
        db.commit()
        return user.username, lage.id, site.id, org.id
    finally:
        db.close()


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), color=(200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


def test_site_media_upload_broadcastet_und_schreibt_medien_log(client, setup_db, monkeypatch, tmp_path):
    import app.routers.ui_major_incident as router
    from app.services import lage_media_service

    username, lage_id, site_id, _ = _lage_mit_benutzer("gsl-service-media")
    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append((lage, event))

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "lage_media"))
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/medien",
        data={"_csrf": csrf}, files={"file": ("foto.png", _png(), "image/png")},
    )
    assert response.status_code == 204
    assert events == [(lage_id, {"type": "site:card_changed", "site_id": site_id})]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        log = db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id == site_id).one()
        assert log.kind == "media"
        assert log.text == "Foto hochgeladen: foto.png"
    finally:
        db.close()


def test_funkjournal_add_broadcastet(client, setup_db, monkeypatch):
    import app.routers.ui_major_incident as router

    username, lage_id, site_id, _ = _lage_mit_benutzer("gsl-service-funk")
    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append((lage, event))

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        f"/lage/{lage_id}/funkjournal",
        data={"_csrf": csrf, "direction": "in", "message": "Meldung", "related_site_id": str(site_id)},
    )
    assert response.status_code == 204
    assert events == [
        (lage_id, {"type": "funkjournal:changed"}),
        (lage_id, {"type": "site:card_changed", "site_id": site_id}),
    ]


def test_funkjournal_add_lehnt_stelle_aus_fremder_lage_ab(client, setup_db):
    username, lage_id, _, org_id = _lage_mit_benutzer("gsl-service-funk-fremd")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fremde_lage = MajorIncident(org_id=org_id, name="Andere Lage")
        db.add(fremde_lage)
        db.flush()
        fremde_site = IncidentSite(major_incident_id=fremde_lage.id, org_id=org_id, bezeichnung="Andere Stelle")
        db.add(fremde_site)
        db.commit()
        fremde_site_id = fremde_site.id
    finally:
        db.close()

    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        f"/lage/{lage_id}/funkjournal",
        data={"_csrf": csrf, "direction": "in", "message": "Meldung", "related_site_id": str(fremde_site_id)},
    )
    assert response.status_code == 400


def test_funkjournal_handled_broadcastet(client, setup_db, monkeypatch):
    import app.routers.ui_major_incident as router

    username, lage_id, _, _ = _lage_mit_benutzer("gsl-service-funk-erledigt")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        entry = CommLogEntry(major_incident_id=lage_id, direction="in", message="Meldung")
        db.add(entry)
        db.commit()
        entry_id = entry.id
    finally:
        db.close()

    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append((lage, event))

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    csrf = client.cookies.get("ec_csrf")
    response = client.post(f"/lage/{lage_id}/funkjournal/{entry_id}/erledigt", data={"_csrf": csrf})
    assert response.status_code == 204
    assert events == [(lage_id, {"type": "funkjournal:changed"})]
