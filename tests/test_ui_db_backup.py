"""Integrationstests fuer die Systemadmin-Seite der DB-Dumps."""
from pathlib import Path

from app.cli import BackupResult
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import Role, User, UserRole
from app.routers import ui_db_backup


def _benutzer_anlegen(username: str, rolle: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == rolle).one()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="DB-Backup Test",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _login(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    token = client.cookies.get("ec_csrf")
    assert token
    return token


def test_db_backups_nur_fuer_systemadmin(client):
    _benutzer_anlegen("db_backup_org_admin", "org_admin")
    _login(client, "db_backup_org_admin")
    assert client.get("/admin/db-backups").status_code == 403

    _benutzer_anlegen("db_backup_system_admin", "system_admin")
    _login(client, "db_backup_system_admin")
    response = client.get("/admin/db-backups")
    assert response.status_code == 200
    assert "Server-Datensicherung" in response.text


def test_db_backup_erstellen_zeigt_dateiname(client, monkeypatch):
    _benutzer_anlegen("db_backup_create_admin", "system_admin")
    csrf = _login(client, "db_backup_create_admin")
    aufrufe = []

    def fake_run_backup(*, out_dir, keep, include_media):
        aufrufe.append((out_dir, keep, include_media))
        return BackupResult(0, [Path("einsatzleiter_20260903_120000.sql.gz")])

    monkeypatch.setattr(ui_db_backup, "run_backup", fake_run_backup)
    monkeypatch.setattr(ui_db_backup, "_dump_liste", lambda: [])
    response = client.post("/admin/db-backups/erstellen", data={"_csrf": csrf})

    assert response.status_code == 200
    assert "einsatzleiter_20260903_120000.sql.gz" in response.text
    assert aufrufe == [(ui_db_backup.settings.BACKUP_DIR, -1, 0)]


def test_db_backup_erstellen_zeigt_fehler(client, monkeypatch):
    _benutzer_anlegen("db_backup_failure_admin", "system_admin")
    csrf = _login(client, "db_backup_failure_admin")
    monkeypatch.setattr(
        ui_db_backup,
        "run_backup",
        lambda **kwargs: BackupResult(1, [], ["einsatzleiter: Dump fehlgeschlagen"]),
    )
    monkeypatch.setattr(ui_db_backup, "_dump_liste", lambda: [])

    response = client.post("/admin/db-backups/erstellen", data={"_csrf": csrf})

    assert response.status_code == 200
    assert "einsatzleiter: Dump fehlgeschlagen" in response.text
    assert "alert--error" in response.text


def test_db_backup_erstellen_verhindert_parallelaufruf(client, monkeypatch):
    _benutzer_anlegen("db_backup_lock_admin", "system_admin")
    csrf = _login(client, "db_backup_lock_admin")
    aufgerufen = False

    def fake_run_backup(**kwargs):
        nonlocal aufgerufen
        aufgerufen = True
        return BackupResult(0, [])

    monkeypatch.setattr(ui_db_backup, "run_backup", fake_run_backup)
    monkeypatch.setattr(ui_db_backup, "_dump_liste", lambda: [])
    assert ui_db_backup._backup_lock.acquire(blocking=False)
    try:
        response = client.post("/admin/db-backups/erstellen", data={"_csrf": csrf})
    finally:
        ui_db_backup._backup_lock.release()

    assert response.status_code == 200
    assert "wird bereits erstellt" in response.text
    assert aufgerufen is False


def test_db_backup_erstellen_ohne_csrf_abgelehnt(client, monkeypatch):
    _benutzer_anlegen("db_backup_csrf_admin", "system_admin")
    _login(client, "db_backup_csrf_admin")
    aufgerufen = False

    def fake_run_backup(**kwargs):
        nonlocal aufgerufen
        aufgerufen = True
        return BackupResult(0, [])

    monkeypatch.setattr(ui_db_backup, "run_backup", fake_run_backup)
    response = client.post("/admin/db-backups/erstellen")

    assert response.status_code == 403
    assert aufgerufen is False
