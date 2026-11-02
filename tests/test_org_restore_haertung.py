"""Regressionen fuer Pflicht-Sicherung, sichere Archive und lokale Org-Zeitplaene."""
import io
import json
import stat
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, Member
from app.models.objekt import ObjektSymbol
from app.routers import ui_org_backup as ui
from app.services import org_export_media as media
from app.services import org_import_service as restore
from app.services.org_backup_loop import ist_faellig
from app.services.org_export_service import export_org


def _zip(path, rows=None, revision=None, extra=None):
    rows = rows or {}
    manifest = dict(format_version=1, org_id=123, tables={k: len(v) for k, v in rows.items()},
                    alembic_revision=revision, media_count=0, app_version="Test")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, zeilen in rows.items():
            zf.writestr(f"data/{name}.jsonl", "\n".join(json.dumps(z) for z in zeilen))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def orgs():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        tag = uuid.uuid4().hex
        a = FireDept(slug=f"hard-a-{tag}", name="Quelle")
        b = FireDept(slug=f"hard-b-{tag}", name="Ziel")
        db.add_all([a, b])
        db.flush()
        db.add_all([Member(org_id=a.id, lastname="Quelle", firstname="A"),
                    Member(org_id=b.id, lastname="Bestand", firstname="B")])
        db.commit()
        yield db, a.id, b.id


@pytest.mark.parametrize("backup", [None, "unlesbar"])
def test_replace_ohne_sicherung_loescht_nichts(orgs, tmp_path, monkeypatch, backup):
    db, a, b = orgs
    archiv = export_org(db, a, tmp_path)
    monkeypatch.setattr(restore, "_safety_backup", lambda *_: backup)
    delete = Mock(side_effect=AssertionError("Darf nichts loeschen"))
    monkeypatch.setattr(restore, "_delete_org_data", delete)
    with pytest.raises(ValueError, match="Sicherheits-Backup"):
        restore.import_org(db, archiv, b, replace=True)
    delete.assert_not_called()
    assert db.query(Member).filter(Member.org_id == b).one().lastname == "Bestand"
    assert db.query(Member).filter(Member.org_id == a).one().lastname == "Quelle"


def test_safety_retention_org_filter_und_rechte(tmp_path, monkeypatch):
    from app.services import org_export_service as exports
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ORG_SAFETY_KEEP", 5)
    counter = iter(range(7))

    def export(_db, oid, directory, **_kw):
        return _zip(directory / f"org-backup-{oid}-20261102-{next(counter):06d}.zip")

    # Die Probe muss genau die gesicherte Org im Manifest enthalten.
    def export_valid(db, oid, directory, **kw):
        path = export(db, oid, directory, **kw)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(dict(format_version=1, org_id=oid)))
        return path

    monkeypatch.setattr(exports, "export_org", export_valid)
    directory = tmp_path / "org-safety"
    directory.mkdir()
    fremd = directory / "org-backup-10-20261102-000000.zip"
    fremd.write_bytes(b"Fremde Org")
    for _ in range(7):
        assert restore._safety_backup(None, 1)
    archive = sorted(directory.glob("org-backup-1-*.zip"))
    assert len(archive) == 5
    assert archive[0].name.endswith("000002.zip")
    assert fremd.read_bytes() == b"Fremde Org"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in archive)


def test_beschaedigtes_safety_archiv_wird_abgelehnt(tmp_path, monkeypatch):
    from app.services import org_export_service as exports
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"kein ZIP")
    monkeypatch.setattr(exports, "export_org", lambda *_a, **_kw: bad)
    assert restore._safety_backup(None, 1) is None


def _symbol(path):
    return dict(id=55, org_id=123, code="X", name="Bild", stil="bild", sort=0,
                aktiv=True, system=False, bild_pfad=path)


@pytest.mark.parametrize("payload", ["../../app/main.py", "/tmp/absolute.png", "a/../../b", "link/bild.png"])
def test_traversal_export_und_import_ueberspringen(orgs, tmp_path, monkeypatch, payload):
    db, a, b = orgs
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "aussen"
    outside.mkdir()
    sentinel = outside / "bild.png"
    sentinel.write_bytes(b"unveraendert")
    (root / "link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(root))
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(root))
    row = _symbol(payload)
    assert media.medien_referenzen("objekt_symbol", [row]) == []
    assert media.medien_referenzen("task_media", [dict(id=55, storage_path=payload)]) == []
    archiv = _zip(tmp_path / "angriff.zip", {"objekt_symbol": [row]}, extra={
        f"media/objekt_symbol/55/bild_pfad/{Path(payload).name}": b"ANGRIFF",
        "../../app/main.py": b"ANGRIFF", "data/../../evil.jsonl": b"ANGRIFF"})
    summary = restore.import_org(db, archiv, b)
    assert summary["media_restored"] == 0
    assert summary["media_skipped"] == 1
    assert sentinel.read_bytes() == b"unveraendert"
    assert list(root.iterdir()) == [root / "link"]
    assert db.query(Member).filter(Member.org_id == a).one().lastname == "Quelle"


@pytest.mark.parametrize("filename", ["../../app/main.py", "/tmp/bild.png", "a/../../b", "../bild.png"])
def test_zusammengesetzte_pfade_nur_basename(tmp_path, monkeypatch, filename):
    resolver = media._zusammengesetzt(str(tmp_path), "incident_site_id", ["stored_filename"])
    monkeypatch.setitem(media._RESOLVERS, "site_media", resolver)
    skipped = []
    row = dict(id=1, org_id=2, incident_site_id=3, stored_filename=filename)
    assert media.medien_referenzen("site_media", [row], skipped) == []
    assert skipped == ["stored_filename"]
    (tmp_path / "2").symlink_to(tmp_path.parent, target_is_directory=True)
    row["stored_filename"] = "bild.png"
    assert media.medien_referenzen("site_media", [row]) == []


def test_medien_restore_schreibt_nur_ziel_org(orgs, tmp_path, monkeypatch):
    db, a, b = orgs
    root = tmp_path / "media"
    root.mkdir()
    source = root / "quelle.png"
    source.write_bytes(b"Quelle")
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(root))
    row = _symbol("quelle.png")
    row["org_id"] = a
    db.add(ObjektSymbol(**{k: v for k, v in row.items() if k != "id"}))
    db.commit()
    archiv = export_org(db, a, tmp_path)
    # Ein geaendertes Archiv darf die Quelldatei beim Restore nicht ueberschreiben.
    summary = restore.import_org(db, archiv, b)
    ziel = db.query(ObjektSymbol).filter(ObjektSymbol.org_id == b).one()
    assert ziel.bild_pfad.startswith(f"{b}/org-restore/")
    assert (root / ziel.bild_pfad).read_bytes() == b"Quelle"
    assert source.read_bytes() == b"Quelle"
    assert summary["media_restored"] == 1
    manifest = restore.read_manifest(archiv)
    assert "alembic_revision" in manifest and "schema_hint" in manifest
    assert stat.S_IMODE(archiv.stat().st_mode) == 0o600


def test_zipbomb_vor_sicherung_und_schreiben(orgs, tmp_path, monkeypatch):
    db, _, b = orgs
    archiv = _zip(tmp_path / "bomb.zip", extra={"unbenutzt": b"0" * 10000})
    monkeypatch.setattr(settings, "ORG_RESTORE_MAX_UNCOMPRESSED_BYTES", 2000)
    safety = Mock()
    monkeypatch.setattr(restore, "_safety_backup", safety)
    with pytest.raises(ValueError, match="Gesamtgröße"):
        restore.read_manifest(archiv)
    with pytest.raises(ValueError, match="Gesamtgröße"):
        restore.import_org(db, archiv, b, replace=True)
    safety.assert_not_called()
    assert db.query(Member).filter(Member.org_id == b).one().lastname == "Bestand"


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("fail,reset_fail", [(False, False), (True, False), (False, True)])
def test_fk_checks_eine_connection_vor_commit_rollback(orgs, tmp_path, monkeypatch, fail, reset_fail, replace):
    db, a, b = orgs
    safety = export_org(db, b, tmp_path / "safety")
    monkeypatch.setattr(restore, "_safety_backup", lambda *_: str(safety))
    archiv = _zip(tmp_path / "leer.zip")
    actual = db.connection()
    events = []

    class Recorder:
        dialect = SimpleNamespace(name="mysql")

        def execute(self, stmt):
            sql = str(stmt)
            events.append((self, sql))
            if sql == "SET FOREIGN_KEY_CHECKS=1" and reset_fail:
                raise RuntimeError("Reset fehlgeschlagen")
            if not sql.startswith("SET "):
                return actual.execute(stmt)

        def invalidate(self):
            events.append((self, "invalidate"))

    connection = Recorder()
    monkeypatch.setattr(db, "connection", lambda: connection)
    commit, rollback = db.commit, db.rollback
    monkeypatch.setattr(db, "commit", lambda: (events.append((connection, "commit")), commit()))
    monkeypatch.setattr(db, "rollback", lambda: (events.append((connection, "rollback")), rollback()))

    # Ein echtes INSERT muss ebenfalls ueber die festgehaltene Connection laufen.
    archiv = _zip(archiv, {"objekt_symbol": [_symbol(None)]})

    def media_pass(*_args):
        events.append((connection, "Import-Statement"))
        if fail:
            raise RuntimeError("Importfehler")
        return 0

    monkeypatch.setattr(restore, "_restore_media", media_pass)
    if fail or reset_fail:
        with pytest.raises((RuntimeError, ValueError)):
            restore.import_org(db, archiv, b, replace=replace)
    else:
        restore.import_org(db, archiv, b, replace=replace)
    expected = ["SET FOREIGN_KEY_CHECKS=0", "Import-Statement", "SET FOREIGN_KEY_CHECKS=1"]
    if reset_fail:
        expected.append("invalidate")
    expected.append("rollback" if fail or reset_fail else "commit")
    statements = [sql for _, sql in events]
    assert any(sql.startswith("INSERT INTO objekt_symbol") for sql in statements)
    assert any(sql.startswith("DELETE FROM member") for sql in statements) is replace
    assert [sql for sql in statements if not sql.startswith(("INSERT INTO", "DELETE FROM"))] == expected
    assert db.query(Member).filter(Member.org_id == a).one().lastname == "Quelle"
    assert all(c is connection for c, _ in events)


def test_schema_import_braucht_bestaetigung(orgs, tmp_path, monkeypatch):
    from app.services import backup_service
    db, _, b = orgs
    monkeypatch.setattr(backup_service, "aktueller_alembic_head", lambda: "neu")
    archiv = _zip(tmp_path / "alt.zip", revision="alt")
    with pytest.raises(ValueError, match="Schema"):
        restore.import_org(db, archiv, b)
    assert restore.import_org(db, archiv, b, confirm_schema=True)["rows_total"] == 0
    assert restore.schema_status({}) == "legacy"
    assert restore.schema_status({"alembic_revision": "neu"}) == "match"


def test_schema_vorschau_und_router_bestaetigung(client, tmp_path, monkeypatch):
    from app.services import backup_service
    from tests.test_org_backup_ui import _admin, _login
    monkeypatch.setattr(backup_service, "aktueller_alembic_head", lambda: "neu")
    tag = uuid.uuid4().hex
    _admin(f"schema-{tag}", 1, "system_admin")
    csrf = _login(client, f"schema-{tag}", "Test1234!")
    archiv = _zip(tmp_path / "alt.zip", revision="alt").read_bytes()
    apply = Mock(return_value=dict(org_id=2, org_name="Test", slug="test", source_org_id=123,
                                  rows_total=0, media_restored=0, media_skipped=2))
    monkeypatch.setattr(ui, "_restore_in_thread", apply)
    for confirm in ("", "1"):
        response = client.post("/admin/org-backup/restore", data={"_csrf": csrf, "confirm": confirm},
                               files={"archiv": ("alt.zip", archiv, "application/zip")})
        assert "Warnung: Abweichender Schema-Stand" in response.text
        assert 'name="confirm_schema"' in response.text
    apply.assert_not_called()
    response = client.post("/admin/org-backup/restore", data={
        "_csrf": csrf, "confirm": "1", "confirm_schema": "1"},
        files={"archiv": ("alt.zip", archiv, "application/zip")})
    assert "abgeschlossen" in response.text and "übersprungen" in response.text
    assert apply.call_args.args[-1] is True


def test_upload_stream_limit_413_ohne_vollread(client, monkeypatch):
    from starlette.datastructures import UploadFile as StarletteUploadFile
    from tests.test_org_backup_ui import _admin, _login
    username = f"upload-{uuid.uuid4().hex}"
    _admin(username, 1, "system_admin")
    csrf = _login(client, username, "Test1234!")
    monkeypatch.setattr(settings, "ORG_BACKUP_MAX_BYTES", 10)

    async def forbidden(*_args, **_kw):
        raise AssertionError("UploadFile.read darf nicht benutzt werden")

    monkeypatch.setattr(StarletteUploadFile, "read", forbidden)
    response = client.post("/admin/org-backup/restore", data={"_csrf": csrf},
                           files={"archiv": ("gross.zip", b"x" * 100, "application/zip")})
    assert response.status_code == 413


def test_stream_kopiert_begrenzt_und_temp_cleanup(tmp_path, monkeypatch):
    class Stream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    monkeypatch.setattr(settings, "ORG_BACKUP_MAX_BYTES", 10)
    monkeypatch.setattr(ui.tempfile, "tempdir", str(tmp_path))
    upload = UploadFile(file=Stream(b"x" * 100))
    with pytest.raises(HTTPException) as exc:
        ui.org_restore_apply(SimpleNamespace(), db=None, archiv=upload)
    assert exc.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("now,last,hour,weekday,expected", [
    ("2026-03-29T00:30", None, 2, None, False),  # noch 01:30 CET
    ("2026-03-29T01:00", None, 2, None, True),   # 03:00 CEST, Stunde 2 entfaellt
    ("2026-10-25T00:30", None, 2, None, True),
    ("2026-10-25T01:30", "2026-10-25T00:30", 2, None, False),  # doppelte Stunde
    ("2026-07-18T22:30", "2026-07-18T21:00", 0, 6, True),    # lokal Sonntag
    ("2026-07-19T00:30", "2026-07-18T22:30", 0, 6, False),   # gleicher Ortstag
])
def test_zeitplan_lokal_dst_und_tageswechsel(now, last, hour, weekday, expected):
    cfg = SimpleNamespace(enabled=True, is_fully_configured=True, hour=hour, weekday=weekday,
                          schedule="weekly" if weekday is not None else "daily",
                          last_run_at=datetime.fromisoformat(last) if last else None)
    assert ist_faellig(cfg, datetime.fromisoformat(now), SimpleNamespace(timezone="Europe/Vienna")) is expected


def test_zeitplan_org_zone_und_fallback(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Europe/Vienna")
    cfg = SimpleNamespace(enabled=True, is_fully_configured=True, hour=5, last_run_at=None)
    now = datetime(2026, 7, 18, 4)
    assert ist_faellig(cfg, now)
    assert not ist_faellig(cfg, now, SimpleNamespace(timezone="UTC"))
    assert ist_faellig(cfg, now, SimpleNamespace(timezone="ungueltig"))


def test_router_safety_fehler_ohne_serverpfade(client, tmp_path, monkeypatch):
    from tests.test_org_backup_ui import _admin, _login
    username = f"safety-ui-{uuid.uuid4().hex}"
    _admin(username, 1, "system_admin")
    csrf = _login(client, username, "Test1234!")
    archiv = _zip(tmp_path / "leer.zip").read_bytes()
    monkeypatch.setattr(restore, "_safety_backup", lambda *_: None)
    response = client.post("/admin/org-backup/restore", data={
        "_csrf": csrf, "modus": "replace", "ziel_org_id": "1",
        "confirm": "1", "confirm_replace": "1"},
        files={"archiv": ("leer.zip", archiv, "application/zip")})
    assert "erforderliche Sicherheits-Backup ist fehlgeschlagen" in response.text
    assert str(tmp_path) not in response.text
    response = client.post("/admin/org-backup/restore", data={"_csrf": csrf},
                           files={"archiv": ("leer.zip", archiv, "application/zip")})
    assert "Altarchiv ohne Alembic-Revision" in response.text
