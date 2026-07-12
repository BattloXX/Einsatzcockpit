"""Integrationstest der neuen Medienverwaltung (Admin/Verwaltung).

Rendert das echte Jinja-Template end-to-end, prueft Groessen-/Alter-Sortierung,
Typ-Filter, Org-Isolation und (Bulk-)Loeschen fuer Task-Medien.
"""
from datetime import UTC, datetime, timedelta

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, Task, TaskMedia
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


def _make_org_admin(username: str, org_slug: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#112233", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "admin").first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id
    finally:
        db.close()


def _make_task_media(org_id: int, *, filename: str, kind: str, size: int, age_days: int = 0) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        inc = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                       started_at=datetime.now(UTC).replace(tzinfo=None))
        db.add(inc)
        db.flush()
        task = Task(incident_id=inc.id, title="Testauftrag")
        db.add(task)
        db.flush()
        media = TaskMedia(
            task_id=task.id, incident_id=inc.id, kind=kind,
            original_filename=filename, storage_path=f"x/{filename}",
            mime_type="image/jpeg" if kind == "image" else "application/pdf",
            bytes=size,
            created_at=(datetime.now(UTC) - timedelta(days=age_days)).replace(tzinfo=None),
        )
        db.add(media)
        db.commit()
        return media.id
    finally:
        db.close()


def test_medienverwaltung_erfordert_admin_rolle(client, setup_db):
    org_id = _make_org_admin("mv_forbidden_org_admin", "mv-forb")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username="mv_readonly", password_hash=hash_password("Test1234!"),
                    display_name="RO", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "readonly").first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    _login(client, "mv_readonly", "Test1234!")
    r = client.get("/admin/medien", follow_redirects=False)
    assert r.status_code in (401, 403)


def test_medienverwaltung_zeigt_groesse_und_sortiert(client, setup_db):
    org_id = _make_org_admin("mv_admin_sort", "mv-sort")
    _make_task_media(org_id, filename="klein.jpg", kind="image", size=1_000)
    _make_task_media(org_id, filename="gross.jpg", kind="image", size=9_000_000)

    _login(client, "mv_admin_sort", "Test1234!")
    r = client.get("/admin/medien")
    assert r.status_code == 200
    assert "Medienverwaltung" in r.text
    assert "gross.jpg" in r.text and "klein.jpg" in r.text
    # Default-Sortierung: Groesse absteigend -> gross.jpg vor klein.jpg
    assert r.text.index("gross.jpg") < r.text.index("klein.jpg")

    # Aufsteigend sortieren dreht die Reihenfolge um
    r_asc = client.get("/admin/medien?sort=size&dir=asc")
    assert r_asc.text.index("klein.jpg") < r_asc.text.index("gross.jpg")


def test_medienverwaltung_filtert_nach_typ(client, setup_db):
    org_id = _make_org_admin("mv_admin_filter", "mv-filter")
    _make_task_media(org_id, filename="foto.jpg", kind="image", size=1_000)
    _make_task_media(org_id, filename="bericht.pdf", kind="pdf", size=2_000)

    _login(client, "mv_admin_filter", "Test1234!")
    r = client.get("/admin/medien?typ=pdf")
    assert "bericht.pdf" in r.text
    assert "foto.jpg" not in r.text


def test_medienverwaltung_org_isolation(client, setup_db):
    org_a = _make_org_admin("mv_admin_a", "mv-a")
    org_b = _make_org_admin("mv_admin_b", "mv-b")
    _make_task_media(org_a, filename="nur-a.jpg", kind="image", size=1_000)
    _make_task_media(org_b, filename="nur-b.jpg", kind="image", size=1_000)

    _login(client, "mv_admin_a", "Test1234!")
    r = client.get("/admin/medien")
    assert "nur-a.jpg" in r.text
    assert "nur-b.jpg" not in r.text


def test_medienverwaltung_loescht_einzelne_datei(client, setup_db):
    org_id = _make_org_admin("mv_admin_del", "mv-del")
    media_id = _make_task_media(org_id, filename="weg.jpg", kind="image", size=1_000)

    _login(client, "mv_admin_del", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/admin/medien/task/{media_id}/loeschen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(TaskMedia, media_id) is None
    finally:
        db.close()


def test_medienverwaltung_bulk_loeschen(client, setup_db):
    org_id = _make_org_admin("mv_admin_bulk", "mv-bulk")
    id1 = _make_task_media(org_id, filename="a.jpg", kind="image", size=1_000)
    id2 = _make_task_media(org_id, filename="b.jpg", kind="image", size=1_000)

    _login(client, "mv_admin_bulk", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        "/admin/medien/bulk-loeschen",
        data={"_csrf": csrf, "keys": [f"task:{id1}", f"task:{id2}"]},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(TaskMedia, id1) is None
        assert db.get(TaskMedia, id2) is None
    finally:
        db.close()
