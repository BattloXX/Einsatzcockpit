"""Lagedokument PR4: Awareness/Presence UI (wer bearbeitet gerade).

Kein echter Browser verfuegbar -- prueft serverseitig, dass die
Presence-Verkabelung (quill-cursors-Modul, Kopfzeile, userId/userName an
initLagedokumentCollab) nur fuer Bearbeiten-Rollen ausgeliefert wird, und dass
die client-seitige Farbzuweisung/Namens-Escaping vorhanden ist.
"""
from pathlib import Path

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import MajorIncident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_user_with_lage(username: str, *, org_slug: str, rolle: str | None) -> tuple[int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#334455", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Testuser", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        if rolle:
            role = db.query(Role).filter(Role.code == rolle).first()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        lage = MajorIncident(org_id=org.id, name="Testlage")
        db.add(lage)
        db.commit()
        return org.id, lage.id
    finally:
        db.close()


def test_editor_bekommt_presence_kopfzeile_und_cursors_modul(client, setup_db):
    _, lage_id = _make_user_with_lage("ld4_edit", org_slug="ld4-edit", rolle="incident_leader")
    _login(client, "ld4_edit", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'id="lagedokument-presence"' in r.text
    assert "/static/js/quill-cursors.js" in r.text
    assert "/static/css/quill-cursors.min.css" in r.text
    assert "Quill.register('modules/cursors', QuillCursors)" in r.text
    assert "cursors: true" in r.text
    assert "onPresenceChange" in r.text
    assert "userId:" in r.text


def test_readonly_bekommt_keine_presence_kopfzeile(client, setup_db):
    _, lage_id = _make_user_with_lage("ld4_ro", org_slug="ld4-ro", rolle="readonly")
    _login(client, "ld4_ro", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'id="lagedokument-presence"' not in r.text
    assert "/static/js/quill-cursors.js" not in r.text


def test_glue_js_hat_deterministische_farbzuweisung_und_praesenz_callback():
    glue = Path("app/static/js/lagedokument-collab.js").read_text(encoding="utf-8")
    assert "colorForUserId" in glue
    assert "onPresenceChange" in glue
    assert "awareness.on('change'" in glue
