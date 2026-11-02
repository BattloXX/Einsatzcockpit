"""Regressionen fuer atomare, getrennte und zeitlich begrenzte DB-Sicherungen."""
import fcntl
import gzip
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import cli
from app.config import settings
from app.services import backup_service as bs
from app.services import remote_backup_service as rbs


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:secret@h/main")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "")
    monkeypatch.setattr(settings, "BACKUP_INCLUDE_MEDIA", False)
    monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", False)
    monkeypatch.setattr(bs, "lade_dump_policy", lambda db: (30, 2))
    return tmp_path


def binary(tmp_path, code):
    path = tmp_path / "fake-dump"
    path.write_text(f"#!{sys.executable}\n{code}\n")
    path.chmod(0o700)
    return str(path)


def fake_dump(cfg, ziel, dump_bin):
    ziel.write_bytes(b"dump")
    return 4


@pytest.mark.parametrize("code", ["import sys; print('SQL'); sys.exit(1)",
                                  "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
def test_dump_fehler_kein_final(tmp_path, code):
    ziel = tmp_path / "einsatzleiter-20261102-000000Z.sql.gz"
    with pytest.raises(RuntimeError):
        cli._dump_db(bs.parse_database_url("mysql://u:p@h/main"), ziel, binary(tmp_path, code))
    assert not ziel.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_dump_timeout_entfernt_partial(backup_env, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DUMP_TIMEOUT_S", 0.05)
    ziel = backup_env / "timeout.sql.gz"
    with pytest.raises(RuntimeError, match="Zeitlimit"):
        cli._dump_db(bs.parse_database_url(settings.DATABASE_URL), ziel,
                     binary(backup_env, "import time; print('SQL', flush=True); time.sleep(10)"))
    assert not ziel.exists()
    assert not list(backup_env.glob("*.partial"))


def test_atomare_datei_und_rechte(backup_env):
    ziel = backup_env / "ok.sql.gz"
    cli._dump_db(bs.parse_database_url(settings.DATABASE_URL), ziel, binary(backup_env, "print('SQL')"))
    assert gzip.decompress(ziel.read_bytes()) == b"SQL\n"
    assert ziel.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("timeout", [False, True])
def test_tar_fehler_kein_final(backup_env, monkeypatch, timeout):
    def run(*args, **kwargs):
        kwargs["stdout"].write(b"halb")
        assert kwargs["timeout"] == settings.BACKUP_DUMP_TIMEOUT_S
        if timeout:
            raise subprocess.TimeoutExpired("tar", 1)
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(cli.subprocess, "run", run)
    ziel = backup_env / "medien.tar.gz"
    with pytest.raises(RuntimeError):
        cli._tar_medien(ziel, backup_env, backup_env / "backups")
    assert not ziel.exists()
    assert not list(backup_env.glob("*.partial"))


def test_tar_erfolg_rechte_und_ausschluss(tmp_path):
    storage = tmp_path / "storage"
    out = storage / "backups"
    out.mkdir(parents=True)
    (storage / "foto").write_bytes(b"bild")
    ziel = out / "medien.tar.gz"
    cli._tar_medien(ziel, storage, out)
    assert ziel.stat().st_mode & 0o777 == 0o600
    with cli.tarfile.open(ziel) as tar:
        assert "storage/foto" in tar.getnames()
        assert not any("backups" in name for name in tar.getnames())


def test_partial_ignoriert_von_liste_retention_upload_restore(backup_env, monkeypatch):
    from app.routers import ui_db_backup
    partial = backup_env / "einsatzleiter-20990101-000000Z.sql.gz.partial"
    partial.write_bytes(b"halb")
    normal = backup_env / "einsatzleiter-20261102-000000Z.sql.gz"
    normal.write_bytes(b"gut")
    assert bs.parse_dump_dateiname(partial.name) is None
    assert bs.zu_loeschende_dump_backups([normal, partial], 30, 1, datetime(2026, 11, 2, tzinfo=UTC)) == []
    assert [d["name"] for d in ui_db_backup._dump_liste()] == [normal.name]
    assert rbs.neueste_je_praefix(backup_env, ["einsatzleiter"]) == [normal]
    monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", True)
    uploads = []
    monkeypatch.setattr(cli, "_remote_upload", lambda files, out: (uploads.extend(files) or 0, None))
    assert cli.backup_upload(alle=True) == 0
    assert uploads == [normal]
    restored = []
    monkeypatch.setattr(cli, "_restore_db", lambda cfg, scratch, path: restored.append(path) or 0)
    assert cli.restore_test() == 0
    assert restored == [normal]
    bs.prune_backups(backup_env, "einsatzleiter", 1)
    assert partial.exists()


def test_alte_partial_aufraeumen(backup_env, monkeypatch):
    alt = backup_env / "alt.sql.gz.partial"
    neu = backup_env / "neu.tar.gz.partial"
    alt.write_bytes(b"x")
    neu.write_bytes(b"x")
    os.utime(alt, (0, 0))
    monkeypatch.setattr(cli, "_dump_db", fake_dump)
    assert cli.run_backup().returncode == 0
    assert not alt.exists()
    assert neu.exists()
    assert backup_env.stat().st_mode & 0o777 == 0o700


def test_retention_getrennt_und_kollision(backup_env, monkeypatch):
    monkeypatch.setattr(cli, "_dump_db", fake_dump)
    monkeypatch.setattr(settings, "UPDATE_BACKUP_KEEP", 3)
    class FesteZeit(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 2, tzinfo=UTC)
    monkeypatch.setattr(cli, "datetime", FesteZeit)
    for _ in range(4):
        assert cli.run_backup().returncode == 0
        assert cli.run_backup(praefix="pre-update-").returncode == 0
    assert len(list(backup_env.glob("einsatzleiter-*.sql.gz"))) == 2
    assert len(list(backup_env.glob("pre-update-einsatzleiter-*.sql.gz"))) == 3
    assert all(bs.parse_dump_dateiname(p.name) for p in backup_env.glob("*.sql.gz"))
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in backup_env.glob("*.sql.gz"))


def test_flock_anderer_prozess(backup_env):
    # Ein eigener Prozess haelt die Sperre: unabhaengig vom UI-Thread-Lock.
    code = ("import fcntl,sys; f=open(sys.argv[1],'w'); "
            "fcntl.flock(f,fcntl.LOCK_EX); print('bereit',flush=True); sys.stdin.read()")
    with subprocess.Popen([sys.executable, "-c", code, str(backup_env / ".backup.lock")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as proc:
        assert proc.stdout.readline().strip() == b"bereit"
        result = cli.run_backup()
        assert result.returncode == 1
        assert result.failures == ["Backup laeuft bereits"]
        proc.stdin.close()
        proc.wait(timeout=5)


def test_lock_nach_fehler_frei(backup_env, monkeypatch):
    monkeypatch.setattr(cli, "_dump_db", lambda *a: (_ for _ in ()).throw(RuntimeError("Fehler")))
    assert cli.run_backup().returncode == 1
    with open(backup_env / ".backup.lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("praefix", ["", "pre-update-"])
def test_platzpruefung(backup_env, monkeypatch, praefix):
    (backup_env / "einsatzleiter-20261101-000000Z.sql.gz").write_bytes(b"x" * 100)
    monkeypatch.setattr(cli.shutil, "disk_usage", lambda p: SimpleNamespace(free=149))
    dump = []
    monkeypatch.setattr(cli, "_dump_db", lambda *a: dump.append(a))
    result = cli.run_backup(praefix=praefix)
    assert result.returncode == 1
    assert "Zu wenig freier Speicherplatz" in result.failures[0]
    assert dump == []


@pytest.mark.parametrize("sql,wert", [("SELECT COUNT(*) FROM users", "0"),
                                      ("SELECT COUNT(*) FROM fire_dept", "0"),
                                      ("SELECT COUNT(*) FROM alembic_version", "2"),
                                      ("SELECT COUNT(*) FROM users", "ungueltig")])
def test_plausibilitaet_verweigert(sql, wert):
    with pytest.raises(RuntimeError):
        bs.pruefe_zeilenzahl(sql, wert)


def test_code_head():
    assert bs.aktueller_alembic_head()


def test_restore_wetter_und_medien_integritaet(backup_env, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "mysql://u:p@h/weather")
    for label in ("einsatzleiter", "einsatzleiter_weather"):
        (backup_env / bs.dump_dateiname(label)).write_bytes(b"dump")
    calls = []
    monkeypatch.setattr(cli, "_restore_db", lambda cfg, scratch, path, **kw: calls.append((scratch, kw)) or 0)
    assert cli.restore_test("scratch") == 0
    assert calls == [("scratch", {}), ("scratch_weather", {"wetter": True})]
    (backup_env / "medien-20261102-000000Z.tar.gz").write_bytes(b"kaputt")
    assert cli.restore_test("scratch") == 1


@pytest.mark.parametrize("protocol", ["scp", "sftp", "rsync", "rclone"])
def test_remote_timeout(protocol, backup_env):
    cfg = rbs.RemoteConfig(protocol, "example.org", 22, "u", "secret", "", "/backup", rclone_remote="offsite:")
    def runner(argv, **kwargs):
        assert kwargs["timeout"] == settings.BACKUP_REMOTE_TIMEOUT_S
        raise subprocess.TimeoutExpired(["secret"], kwargs["timeout"])
    with pytest.raises(RuntimeError, match="Zeitlimit") as exc:
        rbs.upload(cfg, [Path("dump.sql.gz")], backup_env, runner=runner)
    assert "secret" not in str(exc.value)
    assert "ConnectTimeout=30" in rbs._ssh_optionen(cfg)


def test_ftp_timeout():
    ftp = SimpleNamespace()
    assert rbs._ftp_client(lambda: ftp).timeout == settings.BACKUP_REMOTE_TIMEOUT_S


@pytest.mark.parametrize("wert,revision,erwartet", [("1", "head", 0), ("0", "head", 1),
                                                  ("1", "alt", 1), ("2", "head", 1)])
def test_restore_echte_cli_plausibilitaet(backup_env, monkeypatch, wert, revision, erwartet):
    dump = backup_env / bs.dump_dateiname("einsatzleiter")
    dump.write_bytes(gzip.compress(b"CREATE TABLE users (id INT);\n"))
    code = ("import sys\n"
            "if '--execute' not in sys.argv:\n    sys.stdin.buffer.read()\n"
            "else:\n"
            "    sql = sys.argv[sys.argv.index('--execute')+1]\n"
            f"    if 'COUNT(*)' in sql: print({wert!r})\n"
            f"    elif 'version_num' in sql: print({revision!r})\n")
    monkeypatch.setattr(settings, "BACKUP_CLIENT_BIN", binary(backup_env, code))
    monkeypatch.setattr(bs, "aktueller_alembic_head", lambda: "head")
    assert cli.restore_test("scratch") == erwartet


def test_pre_update_revision_nur_warnung(backup_env, monkeypatch, capsys):
    dump = backup_env / bs.dump_dateiname("pre-update-einsatzleiter")
    dump.write_bytes(gzip.compress(b"SELECT 1;\n"))
    code = ("import sys\n"
            "if '--execute' not in sys.argv:\n    sys.stdin.buffer.read()\n"
            "else:\n"
            "    sql=sys.argv[sys.argv.index('--execute')+1]\n"
            "    if 'COUNT(*)' in sql: print('1')\n"
            "    elif 'version_num' in sql: print('alt')\n")
    monkeypatch.setattr(settings, "BACKUP_CLIENT_BIN", binary(backup_env, code))
    monkeypatch.setattr(bs, "aktueller_alembic_head", lambda: "head")
    assert cli._restore_db(bs.parse_database_url(settings.DATABASE_URL), "scratch", dump) == 0
    assert "Warnung" in capsys.readouterr().err


def test_update_nutzt_eigenen_praefix(backup_env, monkeypatch):
    from app.services import update_service
    calls = []
    monkeypatch.setattr(settings, "UPDATE_BACKUP_BEFORE_MIGRATE", True)
    monkeypatch.setattr(cli, "run_backup", lambda **kw: calls.append(kw) or cli.BackupResult(0, []))
    assert update_service._run_pre_migration_backup()["success"]
    assert calls[0]["praefix"] == "pre-update-"


def test_ui_flock_status(client, monkeypatch):
    from app.routers import ui_db_backup
    from tests.test_ui_db_backup import _benutzer_anlegen, _login
    _benutzer_anlegen("robust_flock_admin", "system_admin")
    csrf = _login(client, "robust_flock_admin")
    monkeypatch.setattr(ui_db_backup, "run_backup", lambda **kw: cli.BackupResult(1, [], ["Backup laeuft bereits"]))
    response = client.post("/admin/db-backups/erstellen", data={"_csrf": csrf})
    assert response.status_code == 200
    assert "wird bereits erstellt" in response.text


def test_pre_update_in_ui_und_partial_download_verboten(client, backup_env):
    from tests.test_ui_db_backup import _benutzer_anlegen, _login
    _benutzer_anlegen("robust_typ_admin", "system_admin")
    _login(client, "robust_typ_admin")
    pre = bs.dump_dateiname("pre-update-einsatzleiter")
    (backup_env / pre).write_bytes(b"dump")
    (backup_env / (pre + ".partial")).write_bytes(b"halb")
    response = client.get("/admin/db-backups")
    assert "Vor Update" in response.text
    assert client.get("/admin/db-backups/" + pre).status_code == 200
    assert client.get("/admin/db-backups/" + pre + ".partial").status_code == 404


def test_medien_crc_wird_geprueft(backup_env, monkeypatch):
    (backup_env / bs.dump_dateiname("einsatzleiter")).write_bytes(b"dump")
    monkeypatch.setattr(cli, "_restore_db", lambda *a: 0)
    medien = backup_env / "medien-20261102-000000Z.tar.gz"
    # Gültiges TAR, aber defekte gzip-Pruefsumme hinter dem TAR-Endmarker.
    data = bytearray(gzip.compress(b"\0" * 10240))
    data[-8] ^= 1
    medien.write_bytes(data)
    assert cli.restore_test() == 1


def test_kein_vorgaenger_keine_platzpruefung(backup_env, monkeypatch):
    monkeypatch.setattr(cli, "_dump_db", fake_dump)
    def unerwartet(p):
        pytest.fail("Ohne Vorgaenger darf kein Platzcheck erfolgen")
    monkeypatch.setattr(cli.shutil, "disk_usage", unerwartet)
    assert cli.run_backup().returncode == 0


def test_tar_exit_1_ist_nur_warnung(backup_env, monkeypatch):
    def run(*args, **kwargs):
        kwargs["stdout"].write(b"ok")
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(cli.subprocess, "run", run)
    ziel = backup_env / "medien.tar.gz"
    cli._tar_medien(ziel, backup_env, backup_env / "backups")
    assert ziel.exists()
