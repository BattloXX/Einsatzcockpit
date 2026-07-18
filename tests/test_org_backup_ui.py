"""PR 2: Org-Admin-Download des Org-Datenarchivs (/admin/org-backup)."""
import io
import json
import zipfile

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, Member
from app.models.user import Role, User, UserRole

ORG_A = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                follow_redirects=False)
    return client.cookies.get("ec_csrf")


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _admin(username: str, org_id: int, rolle: str = "org_admin") -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        u = User(username=username, password_hash=hash_password("Test1234!"),
                 display_name="Backup Test", org_id=org_id, active=True)
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=_rolle(db, rolle).id))
        db.commit()
        return u.id
    finally:
        db.close()


def _org_mit_mitglied(slug: str, name: str, nachname: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        o = db.query(FireDept).filter(FireDept.slug == slug).first()
        if o is None:
            o = FireDept(slug=slug, name=name)
            db.add(o)
            db.flush()
            db.add(Member(org_id=o.id, lastname=nachname, firstname="Test"))
            db.commit()
        return o.id
    finally:
        db.close()


def _zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def test_seite_laedt_fuer_org_admin(client):
    _admin("orgbk_page_user", ORG_A)
    _login(client, "orgbk_page_user", "Test1234!")
    r = client.get("/admin/org-backup")
    assert r.status_code == 200
    assert "Datensicherung" in r.text


def test_download_eigene_org(client):
    _admin("orgbk_dl_user", ORG_A)
    _login(client, "orgbk_dl_user", "Test1234!")
    r = client.get("/admin/org-backup/export.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with _zip(r.content) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["org_id"] == ORG_A


def test_org_admin_kann_keine_fremde_org_ziehen(client):
    """Cross-Org: org_admin A mit ?org_id=B erhaelt seine EIGENE Org, nie B."""
    org_b = _org_mit_mitglied("orgbk-iso-b", "OrgBackup Iso B", "Bgeheimnis")
    _admin("orgbk_iso_user", ORG_A)
    _login(client, "orgbk_iso_user", "Test1234!")

    r = client.get("/admin/org-backup/export.zip", params={"org_id": org_b})
    assert r.status_code == 200
    with _zip(r.content) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        member_roh = zf.read("data/member.jsonl").decode("utf-8") if "data/member.jsonl" in zf.namelist() else ""
    assert manifest["org_id"] == ORG_A                 # nicht B
    assert "Bgeheimnis" not in member_roh               # keine Fremd-Org-Daten


def test_sysadmin_kann_org_waehlen(client):
    org_b = _org_mit_mitglied("orgbk-sys-b", "OrgBackup Sys B", "Sysadmin")
    _admin("orgbk_sys_user", ORG_A, rolle="system_admin")
    _login(client, "orgbk_sys_user", "Test1234!")
    r = client.get("/admin/org-backup/export.zip", params={"org_id": org_b})
    assert r.status_code == 200
    with _zip(r.content) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["org_id"] == org_b


def test_kill_switch_deaktiviert_download(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ORG_BACKUP_ENABLED", False)
    _admin("orgbk_off_user", ORG_A)
    _login(client, "orgbk_off_user", "Test1234!")
    r = client.get("/admin/org-backup/export.zip")
    assert r.status_code == 404


def test_restore_seite_nur_sysadmin(client):
    _admin("restore_orgadm", ORG_A)  # org_admin
    _login(client, "restore_orgadm", "Test1234!")
    assert client.get("/admin/org-backup/restore").status_code == 403
    _admin("restore_sysadm", ORG_A, rolle="system_admin")
    _login(client, "restore_sysadm", "Test1234!")
    assert client.get("/admin/org-backup/restore").status_code == 200


def test_restore_upload_roundtrip(client, tmp_path):
    import uuid

    from app.services.org_export_service import export_org
    tag = uuid.uuid4().hex[:6]

    db = SessionLocal()
    set_tenant_context(db, None)
    a = FireDept(slug=f"ui-rt-{tag}", name=f"UI RT {tag}")
    db.add(a)
    db.flush()
    db.add(Member(org_id=a.id, lastname=f"Rt{tag}", firstname="X"))
    db.commit()
    aid = a.id
    db.close()

    db = SessionLocal()
    set_tenant_context(db, None)
    z = export_org(db, aid, tmp_path)
    db.close()
    archiv = z.read_bytes()

    _admin(f"restore_up_{tag}", ORG_A, rolle="system_admin")
    csrf = _login(client, f"restore_up_{tag}", "Test1234!")
    r = client.post(
        "/admin/org-backup/restore",
        data={"_csrf": csrf, "neue_org_name": f"Restored {tag}", "confirm": "1"},
        files={"archiv": ("a.zip", archiv, "application/zip")},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "abgeschlossen" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        neu = db.query(FireDept).filter(FireDept.name == f"Restored {tag}").first()
        assert neu is not None
        set_tenant_context(db, neu.id)
        assert db.query(Member).filter(Member.lastname == f"Rt{tag}").count() == 1
    finally:
        db.close()
