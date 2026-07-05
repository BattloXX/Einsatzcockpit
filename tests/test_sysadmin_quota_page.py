"""Integrationstest der Quota-Verwaltungsseite (system_admin).

Rendert das echte Jinja-Template end-to-end (GET) und prueft den Speicher-Pfad
(POST → DB → erneutes Rendern zeigt den Wert).
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole

_GIB = 1024 ** 3


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_sysadmin(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Sys Admin", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "system_admin").first()
        if role is None:
            role = Role(code="system_admin", name="System-Admin")
            db.add(role)
            db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id
    finally:
        db.close()


def test_quota_page_renders_and_saves(client, setup_db):
    org_id = _make_sysadmin("quota_sysadmin")

    r = _login(client, "quota_sysadmin", "Test1234!")
    assert r.status_code == 302

    # GET: Template rendert vollstaendig
    r = client.get("/admin/system/quotas")
    assert r.status_code == 200
    assert "Quota-Verwaltung" in r.text
    assert "Speicher" in r.text
    assert "KI-Token" in r.text

    # POST: Speicher-Quota = 3 GB, KI-Token = 500000
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/admin/system/quotas/{org_id}",
        data={"_csrf": csrf, "storage_quota_gb": "3", "ai_monthly_token_quota": "500000"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # DB tatsaechlich aktualisiert
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, org_id)
        assert org.storage_quota_bytes == 3 * _GIB
        from app.models.master import OrgSettings
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        assert os_row is not None
        assert os_row.ai_monthly_token_quota == 500000
    finally:
        db.close()

    # Erneutes Rendern zeigt gespeicherten Wert (3.0 GB im Input)
    r = client.get("/admin/system/quotas?saved=1")
    assert r.status_code == 200
    assert "Quota gespeichert" in r.text
    assert 'value="3.0"' in r.text


def test_quota_page_forbidden_for_non_sysadmin(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username="quota_readonly", password_hash=hash_password("Test1234!"),
                    display_name="RO", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "readonly").first()
        if role:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    _login(client, "quota_readonly", "Test1234!")
    r = client.get("/admin/system/quotas", follow_redirects=False)
    assert r.status_code in (401, 403)
