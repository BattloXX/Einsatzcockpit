"""PR 3: geplanter Push der Org-Backups (Faelligkeit, Lauf, Loop, Config-Routen)."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.core.crypto import decrypt_secret
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.org_backup import OrgBackupConfig
from app.models.user import Role, User, UserRole
from app.services import org_backup_loop as loop

ORG_A = 1


# ── Faelligkeit (reine Logik) ─────────────────────────────────────────────────

def _fake(**kw):
    base = dict(enabled=True, is_fully_configured=True, hour=3, schedule="daily",
                weekday=None, last_run_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_faellig_daily_ab_stunde():
    assert loop.ist_faellig(_fake(hour=3), datetime(2026, 7, 18, 4, 0)) is True
    assert loop.ist_faellig(_fake(hour=5), datetime(2026, 7, 18, 4, 0)) is False


def test_nicht_faellig_wenn_heute_schon_gelaufen():
    heute = datetime(2026, 7, 18, 6, 0)
    assert loop.ist_faellig(_fake(last_run_at=datetime(2026, 7, 18, 3, 5)), heute) is False
    assert loop.ist_faellig(_fake(last_run_at=datetime(2026, 7, 17, 3, 5)), heute) is True


def test_weekly_nur_am_wochentag():
    # 2026-07-18 ist ein Samstag (weekday 5)
    sa = datetime(2026, 7, 18, 4, 0)
    assert loop.ist_faellig(_fake(schedule="weekly", weekday=5), sa) is True
    assert loop.ist_faellig(_fake(schedule="weekly", weekday=2), sa) is False


def test_nicht_faellig_wenn_deaktiviert_oder_unvollstaendig():
    now = datetime(2026, 7, 18, 4, 0)
    assert loop.ist_faellig(_fake(enabled=False), now) is False
    assert loop.ist_faellig(_fake(is_fully_configured=False), now) is False


# ── Lauf (Export + Upload gemockt) ────────────────────────────────────────────

def _config(slug: str, **kw) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        o = db.query(FireDept).filter(FireDept.slug == slug).first()
        if o is None:
            o = FireDept(slug=slug, name=slug)
            db.add(o)
            db.flush()
        base = dict(org_id=o.id, enabled=True, protocol="rclone", rclone_remote="offsite:",
                    remote_path="ec")
        base.update(kw)
        cfg = OrgBackupConfig(**base)
        db.add(cfg)
        db.commit()
        return cfg.id
    finally:
        db.close()


def _reload(cfg_id: int) -> OrgBackupConfig:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (db.query(OrgBackupConfig).filter(OrgBackupConfig.id == cfg_id)
                .execution_options(include_all_tenants=True).first())
    finally:
        db.close()


def test_run_sync_ok(monkeypatch):
    cid = _config("push-ok")

    def fake_export(db, org_id, out_dir, include_media=True):
        p = Path(out_dir) / "org-backup.zip"
        p.write_bytes(b"zip")
        return p

    hochgeladen = []
    monkeypatch.setattr("app.services.org_export_service.export_org", fake_export)
    monkeypatch.setattr("app.services.remote_backup_service.upload",
                        lambda remote, dateien, backup_dir, **kw: hochgeladen.append(dateien))

    assert loop.run_org_backup_sync(cid) == "ok"
    c = _reload(cid)
    assert c.last_status == "ok" and c.last_run_at is not None and c.last_error is None
    assert hochgeladen and hochgeladen[0]


def test_run_sync_error_wird_vermerkt(monkeypatch):
    cid = _config("push-err")

    def fake_export(db, org_id, out_dir, include_media=True):
        p = Path(out_dir) / "org-backup.zip"
        p.write_bytes(b"zip")
        return p

    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.services.org_export_service.export_org", fake_export)
    monkeypatch.setattr("app.services.remote_backup_service.upload", boom)

    assert loop.run_org_backup_sync(cid) == "error"
    c = _reload(cid)
    assert c.last_status == "error" and "connection refused" in (c.last_error or "")


def test_lade_faellige_waehlt_faellige(monkeypatch):
    cid = _config("push-due", hour=0, last_run_at=None)  # sofort faellig
    faellig = loop._lade_faellige_ids()
    assert cid in faellig


# ── Config-Routen ─────────────────────────────────────────────────────────────

def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                follow_redirects=False)
    return csrf


def _admin(username: str, org_id: int, rolle: str = "org_admin"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        u = User(username=username, password_hash=hash_password("Test1234!"),
                 display_name="Push Admin", org_id=org_id, active=True)
        db.add(u)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).first() or Role(code=rolle, name=rolle)
        if role.id is None:
            db.add(role)
            db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def test_save_verschluesselt_passwort(client):
    _admin("push_save_user", ORG_A)
    csrf = _login(client, "push_save_user", "Test1234!")
    r = client.post("/admin/org-backup/save", data={
        "_csrf": csrf, "enabled": "1", "protocol": "ftps", "host": "ftp.example.org",
        "username": "ec", "password": "geheim123", "remote_path": "/up",
        "schedule": "daily", "hour": "2", "keep_count": "5", "include_media": "1",
    }, follow_redirects=False)
    assert r.status_code == 303 and "flash=saved" in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = (db.query(OrgBackupConfig).filter(OrgBackupConfig.org_id == ORG_A)
               .execution_options(include_all_tenants=True).first())
        assert cfg is not None and cfg.protocol == "ftps"
        assert cfg.password_enc and decrypt_secret(cfg.password_enc) == "geheim123"
    finally:
        db.close()


def test_run_now_route(client, monkeypatch):
    _admin("push_run_user", ORG_A)
    csrf = _login(client, "push_run_user", "Test1234!")
    # Ziel konfigurieren
    client.post("/admin/org-backup/save", data={
        "_csrf": csrf, "enabled": "1", "protocol": "rclone", "rclone_remote": "offsite:",
        "remote_path": "ec", "schedule": "daily", "hour": "3", "keep_count": "5",
    }, follow_redirects=False)

    def fake_export(db, org_id, out_dir, include_media=True):
        p = Path(out_dir) / "org-backup.zip"
        p.write_bytes(b"zip")
        return p

    monkeypatch.setattr("app.services.org_export_service.export_org", fake_export)
    monkeypatch.setattr("app.services.remote_backup_service.upload",
                        lambda remote, dateien, backup_dir, **kw: None)

    r = client.post("/admin/org-backup/run-now", data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303 and "flash=run_ok" in r.headers["location"]
