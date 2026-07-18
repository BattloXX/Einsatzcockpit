"""Tests fuer den Off-Site-Upload der Backups (remote_backup_service + CLI-Integration).

Reine Argv-/Batch-Bauer direkt geprueft; Transfer mit injiziertem Runner bzw.
FTP-Factory, damit kein Netz/keine Gegenstelle noetig ist.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import cli
from app.services import remote_backup_service as rbs


def _cfg(**kw):
    base = dict(
        protocol="sftp", host="backup.example.test", port=0, user="ec",
        password="", key="/home/ec/.ssh/id_ed25519", path="/backups/ec",
        ssh_strict="accept-new", rclone_remote="",
    )
    base.update(kw)
    return rbs.RemoteConfig(**base)


# ── config_pruefen ────────────────────────────────────────────────────────────

def test_config_pruefen_ok():
    rbs.config_pruefen(_cfg())
    rbs.config_pruefen(_cfg(protocol="rclone", rclone_remote="offsite:", host=""))


def test_config_pruefen_unbekanntes_protokoll():
    with pytest.raises(ValueError):
        rbs.config_pruefen(_cfg(protocol="carrier-pigeon"))


def test_config_pruefen_ssh_ohne_host():
    with pytest.raises(ValueError):
        rbs.config_pruefen(_cfg(host=""))


def test_config_pruefen_rclone_ohne_remote():
    with pytest.raises(ValueError):
        rbs.config_pruefen(_cfg(protocol="rclone", rclone_remote="", host=""))


def test_config_pruefen_ftp_ohne_user():
    with pytest.raises(ValueError):
        rbs.config_pruefen(_cfg(protocol="ftp", user="", password="x"))


# ── Argv-/Batch-Bauer ─────────────────────────────────────────────────────────

def test_build_scp_argv():
    argv = rbs.build_scp_argv(_cfg(port=2222), [Path("a.sql.gz"), Path("b.tar.gz")])
    assert argv[0] == "scp"
    assert "-o" in argv and "StrictHostKeyChecking=accept-new" in argv
    assert "BatchMode=yes" in argv
    assert "Port=2222" in argv
    assert "-i" in argv and "/home/ec/.ssh/id_ed25519" in argv
    assert "a.sql.gz" in argv and "b.tar.gz" in argv
    assert argv[-1] == "ec@backup.example.test:/backups/ec"


def test_build_sftp_argv_und_batch():
    batch = Path("x.sftp")
    argv = rbs.build_sftp_argv(_cfg(), batch)
    assert argv[0] == "sftp"
    assert "-b" in argv and str(batch) in argv
    assert argv[-1] == "ec@backup.example.test"
    batch = rbs.sftp_batch_text(_cfg(), [Path("a.sql.gz"), Path("b.tar.gz")])
    assert 'cd "/backups/ec"' in batch
    assert 'put "a.sql.gz"' in batch and 'put "b.tar.gz"' in batch
    assert batch.strip().endswith("bye")


def test_build_rsync_argv():
    argv = rbs.build_rsync_argv(_cfg(port=2222), [Path("a.sql.gz")])
    assert argv[0] == "rsync" and "-az" in argv
    e_index = argv.index("-e")
    ssh_cmd = argv[e_index + 1]
    assert ssh_cmd.startswith("ssh ")
    assert "Port=2222" in ssh_cmd and "-i /home/ec/.ssh/id_ed25519" in ssh_cmd
    assert argv[-1] == "ec@backup.example.test:/backups/ec"


def test_build_rclone_argv():
    cfg = _cfg(protocol="rclone", rclone_remote="offsite:", path="ec-backups", host="")
    argv = rbs.build_rclone_argv(cfg, Path("/var/backups"))
    assert argv == ["rclone", "copy", str(Path("/var/backups")), "offsite:ec-backups"]


def test_kein_passwort_in_ssh_argv():
    # SSH-Protokolle nutzen Key-Auth; ein evtl. gesetztes Passwort darf NIE ins Argv.
    cfg = _cfg(password="geheim123")
    for argv in (rbs.build_scp_argv(cfg, [Path("a")]),
                 rbs.build_rsync_argv(cfg, [Path("a")]),
                 rbs.build_sftp_argv(cfg, Path("b"))):
        assert "geheim123" not in " ".join(argv)


# ── FTP (ftplib, injizierte Factory) ──────────────────────────────────────────

def test_ftp_upload_ruft_login_und_stor(tmp_path):
    datei = tmp_path / "einsatzleiter-20260718-000000Z.sql.gz"
    datei.write_bytes(b"dump")
    fake = MagicMock()
    cfg = _cfg(protocol="ftp", host="ftp.example.test", user="ec", password="pw", key="", path="/up")
    rbs.ftp_upload(cfg, [datei], factory=lambda: fake)
    fake.connect.assert_called_once_with("ftp.example.test", 21)
    fake.login.assert_called_once_with("ec", "pw")
    fake.cwd.assert_called_once_with("/up")
    assert fake.storbinary.call_args[0][0] == f"STOR {datei.name}"
    fake.quit.assert_called_once()


# ── upload()-Dispatcher (injizierter Runner) ──────────────────────────────────

def _runner_ok(calls):
    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stderr=b"")
    return runner


def test_upload_scp_ruft_runner(tmp_path):
    calls = []
    rbs.upload(_cfg(protocol="scp"), [Path("a.sql.gz")], tmp_path, runner=_runner_ok(calls))
    assert calls and calls[0][0] == "scp"


def test_upload_sftp_schreibt_batch_und_ruft_runner(tmp_path):
    calls = []
    rbs.upload(_cfg(protocol="sftp"), [Path("a.sql.gz")], tmp_path, runner=_runner_ok(calls))
    assert calls and calls[0][0] == "sftp"
    assert "-b" in calls[0]


def test_upload_rclone_nutzt_backup_dir(tmp_path):
    calls = []
    cfg = _cfg(protocol="rclone", rclone_remote="offsite:", host="")
    rbs.upload(cfg, [], tmp_path, runner=_runner_ok(calls))
    assert calls[0][:2] == ["rclone", "copy"]
    assert str(tmp_path) in calls[0]


def test_upload_fehler_wirft(tmp_path):
    def runner(argv, **kw):
        return SimpleNamespace(returncode=1, stderr=b"Permission denied")
    with pytest.raises(RuntimeError, match="fehlgeschlagen"):
        rbs.upload(_cfg(protocol="scp"), [Path("a")], tmp_path, runner=runner)


def test_upload_ungueltige_config_wirft(tmp_path):
    with pytest.raises(ValueError):
        rbs.upload(_cfg(host=""), [Path("a")], tmp_path, runner=_runner_ok([]))


def test_neueste_je_praefix(tmp_path):
    for name in ("einsatzleiter-20260717-000000Z.sql.gz",
                 "einsatzleiter-20260718-000000Z.sql.gz",
                 "medien-20260718-000000Z.tar.gz"):
        (tmp_path / name).write_text("x")
    treffer = rbs.neueste_je_praefix(tmp_path, ("einsatzleiter", "einsatzleiter_weather", "medien"))
    namen = sorted(p.name for p in treffer)
    assert namen == ["einsatzleiter-20260718-000000Z.sql.gz", "medien-20260718-000000Z.tar.gz"]


# ── CLI-Integration ───────────────────────────────────────────────────────────

def test_run_backup_ruft_remote_upload(tmp_path, monkeypatch):
    from app.config import settings
    out = tmp_path / "backups"
    monkeypatch.setattr(settings, "BACKUP_DIR", str(out))
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "")
    monkeypatch.setattr(settings, "BACKUP_INCLUDE_MEDIA", False)
    monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", True)

    monkeypatch.setattr(cli, "_dump_db", lambda cfg, ziel, b: (Path(ziel).write_bytes(b"x"), 1)[1])

    hochgeladen = {}

    def fake_remote(dateien, backup_dir):
        hochgeladen["dateien"] = list(dateien)
        return 0

    monkeypatch.setattr(cli, "_remote_upload", fake_remote)
    assert cli.run_backup() == 0
    assert len(hochgeladen["dateien"]) == 1  # der eine DB-Dump


def test_run_backup_remote_fehler_setzt_exitcode(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "")
    monkeypatch.setattr(settings, "BACKUP_INCLUDE_MEDIA", False)
    monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", True)
    monkeypatch.setattr(cli, "_dump_db", lambda cfg, ziel, b: (Path(ziel).write_bytes(b"x"), 1)[1])
    monkeypatch.setattr(cli, "_remote_upload", lambda dateien, backup_dir: 1)
    assert cli.run_backup() == 1  # Off-Site-Fehler -> Exit != 0


def test_backup_upload_deaktiviert(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", False)
    assert cli.backup_upload() == 1
