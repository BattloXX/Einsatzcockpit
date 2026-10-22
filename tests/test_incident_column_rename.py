"""ECP-02: Spalten-Umbenennen darf keine Eintraege verschwinden lassen."""
from __future__ import annotations

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, Message, Task
from app.models.user import Role, User, UserRole

ORG_ID = 1


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _setup(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "admin").first()
        if role is None:
            role = Role(code="admin", name="admin")
            db.add(role)
            db.flush()
        u = User(username=username, password_hash=hash_password("Test1234!"),
                 display_name="Admin", org_id=ORG_ID, active=True)
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        inc = Incident(primary_org_id=ORG_ID, alarm_type_code="T1")
        db.add(inc)
        db.flush()
        col = IncidentColumn(incident_id=inc.id, code="tasks", title="Auftraege", column_kind="tasks")
        db.add(col)
        db.flush()
        db.add(Task(incident_id=inc.id, column_id=col.id, title="Auftrag A", display_order=0))
        db.add(Message(incident_id=inc.id, column_id=col.id, title="Meldung A"))
        db.commit()
        return inc.id, col.id
    finally:
        db.close()


def _counts(col_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (
            db.query(Task).filter(Task.column_id == col_id).count(),
            db.query(Message).filter(Message.column_id == col_id).count(),
        )
    finally:
        db.close()


def test_mehrfaches_umbenennen_erhaelt_eintraege(client):
    inc_id, col_id = _setup("ecp02_admin")
    _login(client, "ecp02_admin")
    csrf = client.cookies.get("ec_csrf")
    for name in ("Neu 1", "Neu 2", "Neu 3"):
        r = client.post(f"/einsatz/{inc_id}/spalten/{col_id}/titel",
                        data={"title": name, "_csrf": csrf}, follow_redirects=False)
        assert r.status_code == 204
        assert _counts(col_id) == (1, 1)
        frag = client.get(f"/einsatz/{inc_id}/spalte/{col_id}")
        assert frag.status_code == 200
        assert name in frag.text
        assert "Auftrag A" in frag.text and "Meldung A" in frag.text
        kanban = client.get(f"/einsatz/{inc_id}/kanban")
        assert "Auftrag A" in kanban.text and name in kanban.text


def test_umbenennen_baut_kanban_neu_auf_und_zieht_tabs_nach():
    """Client: column_renamed laedt die ganze Kanban-Struktur und zieht Tabs/Dropdown nach."""
    from pathlib import Path

    app_js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    block = app_js.split("case 'column_renamed':", 1)[1].split("case 'column_updated':", 1)[0]
    assert "_swapKanban" in block and "syncLaneTabs" in block and "buildLaneDropdown" in block
    assert "_swapColumn" not in block
    assert "syncLaneTabs" in Path("app/static/js/incident-board.js").read_text(encoding="utf-8")
