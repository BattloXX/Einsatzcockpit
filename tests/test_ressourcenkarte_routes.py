"""Gezielte HTTP-Regressionen fuer die Ressourcenkarte."""

from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, MajorIncident
from app.models.master import FireDept, Member
from app.models.user import Role, User, UserRole


def _daten(role_code="recorder"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        org = FireDept(slug="karte-org-" + suffix, name="Karte", color="#f00", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username="karte-" + suffix,
            password_hash=hash_password("Test1234!"),
            display_name="Test",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        lage = MajorIncident(org_id=org.id, name="Lage")
        db.add(lage)
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="RLF", status="bereitgestellt")
        member = Member(org_id=org.id, firstname="Anna", lastname="Muster", active=True)
        db.add_all([einheit, member])
        db.commit()
        return user.username, lage.id, einheit.id, member.id
    finally:
        db.close()


def _login(client, username):
    client.get("/login")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")}
    )
    assert response.status_code == 200


def test_karte_lesbar_und_readonly_maskiert_telefon(client, setup_db):
    username, lage_id, einheit_id, _ = _daten("readonly")
    _login(client, username)
    response = client.get(f"/lage/{lage_id}/einheiten/{einheit_id}/karte")
    assert response.status_code == 200
    assert "RLF" in response.text


def test_gk_post_broadcastet_einheit_id(client, setup_db, monkeypatch):
    import app.routers.ui_ressourcenkarte as router

    username, lage_id, einheit_id, member_id = _daten()
    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append((lage, event))

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    response = client.post(
        f"/lage/{lage_id}/einheiten/{einheit_id}/gruppenkommandant",
        data={"_csrf": client.cookies.get("ec_csrf"), "member_id": member_id, "modus": "auto"},
    )
    assert response.status_code == 200
    assert events == [(lage_id, {"type": "ressource:changed", "einheit_id": einheit_id})]


def test_fremde_einheit_ist_404(client, setup_db):
    username, lage_id, _, _ = _daten()
    _login(client, username)
    response = client.get(f"/lage/{lage_id}/einheiten/999999/karte")
    assert response.status_code == 404


def test_neue_templates_enthalten_keine_smart_quotes():
    import re
    from pathlib import Path

    root = Path(__file__).parents[1]
    files = list((root / "app/templates/incident_major").glob("_ressource_karte*.html")) + [
        root / "app/static/js/ressourcen_karte.js"
    ]
    assert all(not re.search(r"[„“”‘’]", path.read_text()) for path in files)
