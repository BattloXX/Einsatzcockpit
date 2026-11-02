"""Backup-Monitoring: Lebenszyklus, Schwellen, Maskierung und Diagnose-Integration."""
import fcntl
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import cli
from app.admin_mcp import registry, server
from app.config import settings
from app.models.diagnose import DiagFinding
from app.services import backup_service as bs
from app.services import backup_status as status
from app.services.diagnose import backup as check_backup
from app.services.diagnose import runner
from app.services.diagnose.registry import Budget, CHECKS
from tests import test_admin_mcp_oauth as oauth
from tests import test_backup_robust as robust
from tests import test_diagnose_checks as diagnose_fixtures
from tests.test_admin_mcp_tools import call, tokens
from tests.test_diagnose_runner import queue
from tests.test_systemdiagnose_ui import cockpit  # noqa: F401

backup_env = robust.backup_env
admin_env = oauth.admin_env
diag_engine = diagnose_fixtures.diag_engine
factory = diagnose_fixtures.factory
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def speichern(out, name="backup", age=0, ok=True, **extra):
    daten = {"finished_at": (NOW - timedelta(hours=age)).isoformat().replace("+00:00", "Z"),
             "ok": ok, **extra}
    (out / f".status-{name}.json").write_text(json.dumps(daten))


def gesund(out):
    speichern(out)
    speichern(out, "restore")


@pytest.mark.parametrize("typ,grenze", [("backup", 26), ("restore", 192)])
@pytest.mark.parametrize("faktor,erwartet", [(0, "ok"), (1, "ok"), (1.001, "warn"),
                                           (2, "warn"), (2.001, "fail")])
def test_alter_grenzen(backup_env, typ, grenze, faktor, erwartet):
    gesund(backup_env)
    speichern(backup_env, typ, age=grenze * faktor)
    result = status.pruefen(now=NOW)
    assert result["status"] == erwartet
    assert result["evidence"][typ]["alter_stunden"] == pytest.approx(grenze * faktor)
    assert result["checked_at"] == "2026-11-02T12:00:00Z"


def test_schwellen_injizierbar(backup_env):
    gesund(backup_env)
    speichern(backup_env, age=4)
    assert status.pruefen(now=NOW, max_age_h=2)["status"] == "warn"
    speichern(backup_env, "restore", age=49)
    assert status.pruefen(now=NOW, restore_max_age_d=1)["evidence"]["restore"]["status"] == "fail"


@pytest.mark.parametrize("inhalt", [None, "{", "[]", "null", '"string"',
                                   '{"ok":true,"finished_at":42}',
                                   '{"ok":true,"finished_at":"kaputt"}',
                                   '{"ok":true,"finished_at":"2026-11-02"}'])
def test_fehlend_defekt_unbekannt(backup_env, inhalt):
    if inhalt is not None:
        for typ in ("backup", "restore"):
            (backup_env / f".status-{typ}.json").write_text(inhalt)
    result = status.pruefen(now=NOW)
    assert result["status"] == "unbekannt"
    assert result["evidence"]["backup"]["alter_stunden"] is None
    assert result["empfehlungen"]


def test_dump_fallback_mtime_zaehler_und_partial(backup_env):
    speichern(backup_env, "restore")
    for typ in ("einsatzleiter", "einsatzleiter_weather", "pre-update-einsatzleiter"):
        dump = backup_env / bs.dump_dateiname(typ, NOW - timedelta(days=10))
        dump.write_bytes(b"dump")
        os.utime(dump, (NOW.timestamp() - 3600,) * 2)
    (backup_env / "einsatzleiter-20990101-000000Z.sql.gz.partial").write_bytes(b"unfertig")
    result = status.pruefen(now=NOW)
    assert result["status"] == "ok"
    assert result["evidence"]["backup"]["alter_stunden"] == 1
    assert result["evidence"]["backup"]["groesse"] == 4
    assert result["evidence"]["dumps_je_typ"] == {"einsatzleiter": 1, "einsatzleiter_weather": 1,
                                                 "pre-update-einsatzleiter": 1}


@pytest.mark.parametrize("typ", ["backup", "restore"])
def test_fehlgeschlagen_ueberstimmt_frische_datei(backup_env, typ):
    gesund(backup_env)
    speichern(backup_env, typ, ok=False)
    dump = backup_env / bs.dump_dateiname("einsatzleiter", NOW)
    dump.write_bytes(b"dump")
    os.utime(dump, (NOW.timestamp(),) * 2)
    assert status.pruefen(now=NOW)["status"] == "fail"


@pytest.mark.parametrize("enabled,offsite,erwartet", [
    (False, None, "ok"), (True, None, "warn"),
    (True, {"enabled": True, "ok": None}, "warn"),
    (True, {"enabled": True, "ok": False}, "warn"),
    (True, {"enabled": True, "ok": True}, "ok"),
    (True, {"enabled": False, "ok": True}, "warn"),
])
def test_offsite(backup_env, enabled, offsite, erwartet):
    gesund(backup_env)
    speichern(backup_env, offsite=offsite)
    result = status.pruefen(now=NOW, offsite_enabled=enabled)
    assert result["status"] == erwartet
    assert ("offsite_fehlgeschlagen" in [b["key"] for b in result["befunde"]]) == (erwartet == "warn")


@pytest.mark.parametrize("frei,erwartet", [(199, "warn"), (200, "ok")])
def test_speicher_und_medien_nur_hinweis(backup_env, monkeypatch, frei, erwartet):
    gesund(backup_env)
    (backup_env / bs.dump_dateiname("einsatzleiter", NOW)).write_bytes(b"x" * 100)
    medien = backup_env / "medien-20260101-000000Z.tar.gz"
    medien.write_bytes(b"tar")
    os.utime(medien, (0, 0))
    monkeypatch.setattr(status.shutil, "disk_usage", lambda p: SimpleNamespace(free=frei))
    result = status.pruefen(now=NOW)
    assert result["status"] == erwartet
    assert result["evidence"]["freier_platz_mb"] == frei / 1024**2
    assert result["evidence"]["hinweise"]


def test_nicht_lesbares_verzeichnis(tmp_path):
    result = status.pruefen(tmp_path / "fehlt", now=NOW, offsite_enabled=False)
    assert result["status"] == "unbekannt"
    assert result["evidence"]["freier_platz_mb"] is None


@pytest.mark.parametrize("praefix", ["", "pre-update-"])
def test_backup_status_unter_lock_atomare_rechte(backup_env, monkeypatch, praefix):
    monkeypatch.setattr(cli, "_dump_db", robust.fake_dump)
    original = status.schreibe_status
    def schreiben(out, name, daten):
        with open(out / ".backup.lock") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original(out, name, daten)
    monkeypatch.setattr(status, "schreibe_status", schreiben)
    assert cli.run_backup(praefix=praefix).returncode == 0
    # Pre-Update-Laeufe haben eine eigene Statusdatei und beruehren die Tages-Bewertung nicht.
    path = backup_env / (".status-backup-pre-update.json" if praefix else ".status-backup.json")
    assert not (backup_env / (".status-backup.json" if praefix else ".status-backup-pre-update.json")).exists()
    data = json.loads(path.read_text())
    assert data["ok"] is True and data["finished_at"].endswith("Z")
    assert data["kind"] == ("pre-update" if praefix else "daily")
    assert data["files"][0]["size"] == 4
    assert data["offsite"]["ok"] is None
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(backup_env.glob(".status-*tmp*"))


def test_backup_fehler_maskierung_und_upload_status(backup_env, monkeypatch):
    monkeypatch.setattr(cli, "_dump_db", robust.fake_dump)
    monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", True)
    monkeypatch.setattr(cli, "_remote_upload", lambda *a: (0, None))
    assert cli.run_backup().returncode == 0
    path = backup_env / ".status-backup.json"
    assert json.loads(path.read_text())["offsite"]["ok"] is True
    monkeypatch.setattr(cli, "_remote_upload", lambda *a: (1, "https://u:secret@host/private"))
    assert cli.run_backup().returncode == 1
    assert json.loads(path.read_text())["offsite"]["ok"] is False
    def kaputt(*a):
        raise RuntimeError("secret mysql://u:secret@host/db /private/file password=secret")
    monkeypatch.setattr(cli, "_dump_db", kaputt)
    assert cli.run_backup().returncode == 1
    text = path.read_text()
    assert all(wert not in text for wert in ("secret", "mysql://", "/private", "https://"))
    assert json.loads(text)["failures"]


def test_restore_status_erfolg_und_fehler(backup_env, monkeypatch):
    dump = backup_env / bs.dump_dateiname("einsatzleiter", NOW)
    dump.write_bytes(b"dump")
    monkeypatch.setattr(cli, "_restore_db", lambda *a: 0)
    assert cli.restore_test() == 0
    path = backup_env / ".status-restore.json"
    data = json.loads(path.read_text())
    assert data["ok"] is True and data["dump"] == dump.name
    assert "haupt.users" in data["db_checks"]
    assert "Kein Medienarchiv vorhanden" in data["warnings"]
    assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(cli, "_restore_db", lambda *a: 1)
    assert cli.restore_test() == 1
    assert json.loads(path.read_text())["ok"] is False
    assert cli.restore_test("ungueltig/") == 1
    assert json.loads(path.read_text())["dump"] is None


def test_status_schreibfehler_beeinflusst_laeufe_nicht(backup_env, monkeypatch, caplog):
    monkeypatch.setattr(cli, "_dump_db", robust.fake_dump)
    original = status.os.replace
    def replace(src, dst):
        if Path(dst).name.startswith(".status-"):
            raise OSError("Platte voll")
        return original(src, dst)
    monkeypatch.setattr(status.os, "replace", replace)
    assert cli.run_backup().returncode == 0
    monkeypatch.setattr(cli, "_restore_db", lambda *a: 0)
    assert cli.restore_test() == 0
    assert "konnte nicht geschrieben" in caplog.text
    assert list(backup_env.glob(".status-*")) == []


def test_mcp_evidenz_erlaubte_felder(backup_env):
    gesund(backup_env)
    speichern(backup_env, dump="/private/password", failures=["GEHEIM"],
              offsite={"enabled": True, "ok": True, "ziel_beschreibung": "https://u:GEHEIM@host/private"})
    speichern(backup_env, "restore", dump="../../GEHEIM", db_checks=["GEHEIM"])
    result = registry.collect(None, "admin_backup_pruefen")
    answer = server.response("admin_backup_pruefen", "korrelation", result)
    assert answer["modul"] == "backup" and answer["status"] == "ok"
    assert result["checked_at"].endswith("Z")
    assert all(v not in json.dumps(answer) for v in ("GEHEIM", "/private", "https://", str(backup_env)))
    assert "admin_backup_pruefen" in registry.READ_TOOLS
    assert CHECKS["backup"].read_only


def test_mcp_wrapper(client, admin_env, backup_env):
    gesund(backup_env)
    token = tokens(client, admin_env)["access_token"]
    assert "admin_backup_pruefen" in oauth.admin_call(client, token, "tools/list").text
    result = call(client, token, "admin_backup_pruefen")
    assert result["modul"] == "backup" and result["status"] == "ok"
    with oauth.SessionLocal() as db:
        assert registry.collect(db, "admin_system_uebersicht")["evidence"]["teilchecks"]["admin_backup_pruefen"] == "ok"


@pytest.mark.parametrize("fall", ["alt", "fehler"])
def test_befunde_oeffnen_schliessen(factory, backup_env, monkeypatch, fall):
    monkeypatch.setattr(check_backup, "pruefen", lambda: status.pruefen(now=NOW))
    gesund(backup_env)
    speichern(backup_env, age=53)
    speichern(backup_env, "restore", age=385)
    erwartet = {"backup_zu_alt", "restore_probe_zu_alt"}
    if fall == "fehler":
        speichern(backup_env, ok=False)
        monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", True)
        (backup_env / bs.dump_dateiname("einsatzleiter", NOW)).write_bytes(b"dump")
        monkeypatch.setattr(status.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
        erwartet = {"backup_fehlgeschlagen", "restore_probe_zu_alt", "offsite_fehlgeschlagen", "speicher_knapp"}
    with factory() as db:
        ident = queue(db, checks=["backup"])
        assert runner.claim_run(db) == ident
        result = check_backup.check(db, Budget())
        assert {b.kern for b in result.findings} == erwartet
        runner.persist_result(db, ident, "backup", result, success=True)
        rows = list(db.scalars(select(DiagFinding).where(DiagFinding.check_id == "backup")))
        assert len(rows) == len(erwartet) and all(r.status == "offen" for r in rows)
        gesund(backup_env)
        monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", False)
        monkeypatch.setattr(status.shutil, "disk_usage", lambda p: SimpleNamespace(free=10000))
        runner.persist_result(db, ident, "backup", check_backup.check(db, Budget()), success=True)
        for row in rows:
            db.refresh(row)
            assert row.status == "behoben"


def test_panel_backup_karte(client, cockpit, backup_env):
    gesund(backup_env)
    response = client.get("/admin/systemdiagnose/teil/gesamt")
    assert response.status_code == 200
    for text in ("Datensicherung", "Letztes Backup", "Letzte Restore-Probe", "Off-Site"):
        assert text in response.text


def test_pre_update_lauf_verdeckt_kein_ausgefallenes_tages_backup(tmp_path):
    """Pre-Update-Dump und -Status zaehlen nicht als Tages-Backup."""
    import json
    from datetime import UTC, datetime, timedelta

    from app.services import backup_status

    jetzt = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    alt = jetzt - timedelta(hours=60)
    (tmp_path / ".status-backup.json").write_text(json.dumps(
        {"ok": True, "finished_at": alt.isoformat().replace("+00:00", "Z"), "kind": "daily"}))
    (tmp_path / ".status-backup-pre-update.json").write_text(json.dumps(
        {"ok": True, "finished_at": jetzt.isoformat().replace("+00:00", "Z"), "kind": "pre-update"}))
    dump = tmp_path / "pre-update-einsatzleiter-20260103-115900Z.sql.gz"
    dump.write_bytes(b"x")
    import os
    os.utime(dump, (jetzt.timestamp(), jetzt.timestamp()))
    ergebnis = backup_status.pruefen(tmp_path, now=jetzt, max_age_h=26, offsite_enabled=False)
    assert ergebnis["evidence"]["backup"]["status"] == "fail"
    assert any(b["key"] == "backup_zu_alt" for b in ergebnis["befunde"])
