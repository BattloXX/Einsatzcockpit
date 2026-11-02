"""S1: Eingaben und gespeicherte Org-Ziele duerfen keine Kommandos einschleusen."""
import shlex
import socket
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import remote_backup_service as rbs
from tests.test_org_backup_push import _admin, _config, _login, _reload


@pytest.fixture
def cfg():
    return rbs.RemoteConfig(protocol="sftp", host="8.8.8.8", port=0, user="ec",
                            password="", key="", path="/backups")


PAYLOADS = [
    {"ssh_strict": "accept-new -oProxyCommand=touch /tmp/pwn"},
    {"path": "/backups\n!touch /tmp/pwn"},
    {"host": "-oProxyCommand=id"}, {"user": "-oProxyCommand=id"},
    {"rclone_remote": ":sftp,ssh=id:"},
    {"path": '-evil'}, {"path": '/bad"path'}, {"path": "/bad'path"},
    {"path": "/bad\x00path"}, {"host": "host name"}, {"host": "host\n"},
    {"port": -1}, {"port": 65536},
]
ORG_PAYLOADS = PAYLOADS + [
    {"host": host} for host in ("127.0.0.1", "10.2.3.4", "localhost", "LOCALHOST.",
                               "::1", "169.254.1.1", "0.0.0.0", "fe80::1", "fc00::1")
] + [{"protocol": p} for p in ("scp", "rsync", "rclone", "ftp")] + [{"ssh_strict": "no"}]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_server_payload_kein_runner(cfg, payload, tmp_path):
    runner = Mock()
    with pytest.raises(ValueError):
        rbs.upload(replace(cfg, **payload), [Path("a.zip")], tmp_path, runner=runner)
    runner.assert_not_called()


@pytest.mark.parametrize("payload", ORG_PAYLOADS)
def test_org_payload_gesperrt(cfg, payload):
    with pytest.raises(rbs.OrgConfigFehler, match="bitte neu speichern"):
        rbs.config_pruefen_org(replace(cfg, **payload))


@pytest.mark.parametrize("adresse", ["127.0.0.1", "10.1.1.1", "::1", "169.254.1.1"])
def test_dns_auch_gemischte_adressen_gesperrt(cfg, adresse):
    resolver = Mock(return_value=[(0, 0, 0, "", ("8.8.8.8", 22)), (0, 0, 0, "", (adresse, 22))])
    with pytest.raises(ValueError):
        rbs.config_pruefen_org(replace(cfg, host="backup.example.org"), resolver=resolver)
    resolver.assert_called_once()


def test_dns_oeffentlich_und_aufloesungsfehler(cfg):
    rbs.config_pruefen_org(replace(cfg, host="backup.example.org"),
                          resolver=lambda *a, **kw: [(0, 0, 0, "", ("8.8.8.8", 22))])
    with pytest.raises(ValueError):
        rbs.config_pruefen_org(replace(cfg, host="backup.example.org"),
                              resolver=Mock(side_effect=socket.gaierror()))


@pytest.mark.parametrize("protocol", ["sftp", "ftps", "graph"])
def test_gueltige_org_protokolle(cfg, protocol):
    rbs.config_pruefen_org(replace(cfg, protocol=protocol, graph_tenant="t", graph_client="c",
                                  graph_secret="s", graph_drive_id="d"))


@pytest.mark.parametrize("name", ['a\n!id', '-evil.zip', '!id', 'dir/-evil', 'a"b', "a'b"])
def test_batch_dateinamen_gesperrt(cfg, name, tmp_path):
    runner = Mock()
    with pytest.raises(ValueError):
        rbs.upload(cfg, [Path(name)], tmp_path, runner=runner)
    runner.assert_not_called()


def test_batch_maskierung_und_rsync_key(cfg):
    text = rbs.sftp_batch_text(replace(cfg, path="/backups/a\\"), [Path("a*.zip")])
    assert '\\\\"' in text and r'a\*.zip' in text
    assert all(not zeile.startswith(("!", "-")) for zeile in text.splitlines())
    argv = rbs.build_rsync_argv(replace(cfg, key="/key with spaces/'key"), [Path("a.zip")])
    assert shlex.split(argv[argv.index("-e") + 1]) == [
        "ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        "-i", "/key with spaces/'key", "--"]


@pytest.mark.parametrize("payload", ORG_PAYLOADS)
def test_org_admin_save_payload_http400(client, cfg, payload, monkeypatch):
    username = "hardening_" + uuid.uuid4().hex
    _admin(username, 1)
    csrf = _login(client, username, "Test1234!")
    runner = Mock()
    monkeypatch.setattr(rbs.subprocess, "run", runner)
    daten = {"_csrf": csrf, "protocol": cfg.protocol, "host": cfg.host, "username": cfg.user,
             "remote_path": cfg.path}
    felder = {"path": "remote_path", "user": "username"}
    daten.update({felder.get(k, k): str(v) for k, v in payload.items()})
    antwort = client.post("/admin/org-backup/save", data=daten, follow_redirects=False)
    assert antwort.status_code == 400
    runner.assert_not_called()


def test_bestand_rsync_error_ohne_export_oder_upload(monkeypatch):
    from app.services.org_backup_loop import run_org_backup_sync
    cid = _config("hardening-rsync", protocol="rsync")
    export, upload = Mock(), Mock()
    monkeypatch.setattr("app.services.org_export_service.export_org", export)
    monkeypatch.setattr(rbs, "upload", upload)
    assert run_org_backup_sync(cid) == "error"
    row = _reload(cid)
    assert row.last_status == "error" and row.last_error == rbs.ORG_CONFIG_FEHLER
    export.assert_not_called()
    upload.assert_not_called()


def test_bestand_test_route_markiert_error(client, monkeypatch):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_backup import OrgBackupConfig
    _admin("hardening_test_admin", 1)
    csrf = _login(client, "hardening_test_admin", "Test1234!")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(OrgBackupConfig).filter(OrgBackupConfig.org_id == 1).first()
        if row is None:
            row = OrgBackupConfig(org_id=1)
            db.add(row)
        row.protocol = "rsync"
        row.host = "8.8.8.8"
        db.commit()
        cid = row.id
    upload = Mock()
    monkeypatch.setattr(rbs, "upload", upload)
    antwort = client.post("/admin/org-backup/test", data={"_csrf": csrf}, follow_redirects=False)
    assert antwort.status_code == 303 and "test_error" in antwort.headers["location"]
    row = _reload(cid)
    assert row.last_status == "error" and row.last_error == rbs.ORG_CONFIG_FEHLER
    upload.assert_not_called()
    seite = client.get("/admin/org-backup")
    assert "nicht mehr erlaubt" in seite.text


@pytest.mark.parametrize("protocol", ["sftp", "ftps", "graph"])
def test_gueltige_org_speichern_und_testen(client, protocol, monkeypatch):
    username = "hardening_valid_" + uuid.uuid4().hex
    _admin(username, 1)
    csrf = _login(client, username, "Test1234!")
    daten = {"_csrf": csrf, "protocol": protocol, "host": "8.8.8.8", "username": "ec",
             "remote_path": "/backups", "graph_tenant_id": "t", "graph_client_id": "c",
             "graph_client_secret": "secret", "graph_drive_id": "drive"}
    antwort = client.post("/admin/org-backup/save", data=daten, follow_redirects=False)
    assert antwort.status_code == 303 and "saved" in antwort.headers["location"]
    runner = Mock(return_value=type("Ergebnis", (), {"returncode": 0})())
    ftp = Mock()
    graph_upload = Mock()
    # Der Dispatcher selbst bleibt aktiv; nur die externen Transfers sind ersetzt.
    original_upload = rbs.upload
    monkeypatch.setattr(rbs, "upload", lambda cfg, files, directory:
                        original_upload(cfg, files, directory, runner=runner, ftp_factory=lambda: ftp))
    monkeypatch.setattr("app.services.graph_backup_service.upload", graph_upload)
    antwort = client.post("/admin/org-backup/test", data={"_csrf": csrf}, follow_redirects=False)
    assert antwort.status_code == 303 and "test_ok" in antwort.headers["location"]
    if protocol == "sftp":
        assert "--" in runner.call_args.args[0]
    elif protocol == "ftps":
        ftp.storbinary.assert_called_once()
    else:
        graph_upload.assert_called_once()
