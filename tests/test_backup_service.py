"""Tests fuer den Backup-/Restore-Baustein (backup_service + CLI-Orchestrierung).

Die reine Logik (URL-Parsing, Kommandozeilen, Retention, Verifikations-SQL) wird
direkt geprueft; die Orchestrierung (app.cli.run_backup/restore_test) mit
gemockten Subprozessen, damit kein echtes MariaDB noetig ist.
"""
from datetime import datetime
from pathlib import Path

import pytest

from app import cli
from app.services import backup_service as bs

# ── URL-Parsing ───────────────────────────────────────────────────────────────

def test_parse_database_url_standard():
    cfg = bs.parse_database_url("mysql+pymysql://einsatzleiter:geheim@127.0.0.1:3306/einsatzleiter")
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 3306
    assert cfg.user == "einsatzleiter"
    assert cfg.password == "geheim"
    assert cfg.database == "einsatzleiter"


def test_parse_database_url_default_port():
    cfg = bs.parse_database_url("mysql+pymysql://u:p@db.example.test/meinedb")
    assert cfg.port == 3306
    assert cfg.database == "meinedb"


def test_parse_database_url_prozentkodiertes_passwort():
    # Passwort mit Sonderzeichen (@ / :) muss dekodiert werden.
    cfg = bs.parse_database_url("mysql+pymysql://u:p%40ss%2Fwort@h:3307/d")
    assert cfg.password == "p@ss/wort"
    assert cfg.port == 3307


def test_parse_database_url_unvollstaendig():
    with pytest.raises(ValueError):
        bs.parse_database_url("mysql+pymysql://user:pw@/keine_db")
    with pytest.raises(ValueError):
        bs.parse_database_url("mysql+pymysql:///nur_db")


# ── Kommandozeilen-Aufbau ─────────────────────────────────────────────────────

def _cfg():
    return bs.DbConfig(host="h", port=3306, user="u", password="secret", database="db")


def test_build_dump_argv_passwort_nur_in_env():
    argv, env = bs.build_dump_argv(_cfg(), "mariadb-dump")
    # Passwort NIE im Argv (waere in ps sichtbar), nur in MYSQL_PWD.
    assert "secret" not in " ".join(argv)
    assert env == {"MYSQL_PWD": "secret"}
    assert argv[0] == "mariadb-dump"
    assert "--single-transaction" in argv
    assert "--routines" in argv and "--triggers" in argv and "--events" in argv
    assert "--no-tablespaces" in argv
    assert argv[-1] == "db"  # DB-Name zuletzt
    assert "--host=h" in argv and "--port=3306" in argv and "--user=u" in argv


def test_build_restore_argv_zieldb_override():
    argv, env = bs.build_restore_argv(_cfg(), "einsatzleiter_restore_test", "mariadb")
    assert argv[0] == "mariadb"
    assert argv[-1] == "einsatzleiter_restore_test"
    assert env == {"MYSQL_PWD": "secret"}


def test_build_query_argv():
    argv, _ = bs.build_query_argv(_cfg(), "scratch", "SELECT COUNT(*) FROM users", "mariadb")
    assert "--execute" in argv
    assert "SELECT COUNT(*) FROM users" in argv
    assert argv[-1] == "scratch"


def test_build_admin_argv_ohne_db():
    argv, _ = bs.build_admin_argv(_cfg(), "CREATE DATABASE x", "mariadb")
    assert "--execute" in argv and "CREATE DATABASE x" in argv
    # Kein DB-Name am Ende (administrative Anweisung ohne Ziel-DB)
    assert argv[-1] == "CREATE DATABASE x"


# ── Dateinamen / Retention ────────────────────────────────────────────────────

def test_dateinamen_sortierbar():
    dt = datetime(2026, 7, 18, 1, 30, 0)
    assert bs.dump_dateiname("einsatzleiter", dt) == "einsatzleiter-20260718-013000Z.sql.gz"
    assert bs.medien_dateiname(dt) == "medien-20260718-013000Z.tar.gz"


def test_zu_loeschende_behaelt_neueste():
    pfade = [Path(f"einsatzleiter-2026071{i}-000000Z.sql.gz") for i in range(1, 6)]  # 5 Stueck
    weg = bs.zu_loeschende(pfade, behalten=2)
    # Die 3 aeltesten weg, die 2 neuesten bleiben.
    assert [p.name for p in weg] == [
        "einsatzleiter-20260711-000000Z.sql.gz",
        "einsatzleiter-20260712-000000Z.sql.gz",
        "einsatzleiter-20260713-000000Z.sql.gz",
    ]


def test_zu_loeschende_nichts_wenn_unter_grenze():
    pfade = [Path("a-1"), Path("a-2")]
    assert bs.zu_loeschende(pfade, behalten=5) == []
    assert bs.zu_loeschende(pfade, behalten=0) == []


def test_prune_backups_loescht_alte(tmp_path):
    for i in range(1, 6):
        (tmp_path / f"einsatzleiter-2026071{i}-000000Z.sql.gz").write_text("x")
    (tmp_path / "medien-20260715-000000Z.tar.gz").write_text("y")  # anderer Praefix bleibt
    geloescht = bs.prune_backups(tmp_path, "einsatzleiter", behalten=2)
    assert len(geloescht) == 3
    verbleibend = sorted(p.name for p in tmp_path.glob("einsatzleiter-*"))
    assert verbleibend == [
        "einsatzleiter-20260714-000000Z.sql.gz",
        "einsatzleiter-20260715-000000Z.sql.gz",
    ]
    assert (tmp_path / "medien-20260715-000000Z.tar.gz").exists()


def test_verify_sql_enthaelt_kernchecks():
    checks = bs.verify_sql()
    assert any("alembic_version" in c for c in checks)
    assert any("fire_dept" in c for c in checks)
    assert all(c.upper().startswith("SELECT") for c in checks)


# ── Orchestrierung (gemockte Subprozesse) ─────────────────────────────────────

def test_run_backup_beide_dbs_und_medien(tmp_path, monkeypatch):
    from app.config import settings
    out = tmp_path / "backups"
    medien = tmp_path / "app_storage" / "incident_media"
    medien.mkdir(parents=True)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(out))
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter_weather")
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(medien))
    monkeypatch.setattr(settings, "BACKUP_INCLUDE_MEDIA", True)

    gedumpt = []

    def fake_dump(cfg, ziel, dump_bin):
        Path(ziel).write_bytes(b"dump")
        gedumpt.append(cfg.database)
        return 4

    def fake_tar(ziel, medien_root, backup_dir):
        Path(ziel).write_bytes(b"tar")
        return 3

    monkeypatch.setattr(cli, "_dump_db", fake_dump)
    monkeypatch.setattr(cli, "_tar_medien", fake_tar)

    rc = cli.run_backup()
    assert rc == 0
    assert set(gedumpt) == {"einsatzleiter", "einsatzleiter_weather"}
    assert list(out.glob("einsatzleiter-*.sql.gz"))
    assert list(out.glob("einsatzleiter_weather-*.sql.gz"))
    assert list(out.glob("medien-*.tar.gz"))


def test_run_backup_meldet_fehler(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "")
    monkeypatch.setattr(settings, "BACKUP_INCLUDE_MEDIA", False)

    def boom(cfg, ziel, dump_bin):
        raise RuntimeError("mariadb-dump nicht gefunden")

    monkeypatch.setattr(cli, "_dump_db", boom)
    assert cli.run_backup() == 1  # Exit-Code != 0 → Timer/Monitoring schlaegt an


def test_restore_test_verweigert_produktions_db(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    # Scratch == Produktions-DB → harte Verweigerung, kein Subprozess.
    assert cli.restore_test("einsatzleiter") == 1


def test_restore_test_ungueltiger_dbname(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    assert cli.restore_test("boese; DROP TABLE users") == 1


def test_restore_test_ohne_dump(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", "mysql+pymysql://u:p@h:3306/einsatzleiter")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "BACKUP_RESTORE_TEST_DB", "einsatzleiter_restore_test")
    assert cli.restore_test() == 1  # kein Dump vorhanden
