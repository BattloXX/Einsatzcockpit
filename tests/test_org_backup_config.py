"""PR 0: OrgBackupConfig-Modell + RemoteConfig-Builder aus einer Org-Zeile."""
from pathlib import Path

from app.core.crypto import encrypt_secret
from app.models.org_backup import OrgBackupConfig
from app.services import remote_backup_service as rbs


def _cfg(**kw) -> OrgBackupConfig:
    base = dict(org_id=1, protocol="sftp", host="backup.example.org", port=0,
                username="ec", remote_path="/srv/ec")
    base.update(kw)
    return OrgBackupConfig(**base)


# ── is_fully_configured ───────────────────────────────────────────────────────

def test_is_fully_configured_sftp():
    assert _cfg().is_fully_configured is True


def test_is_fully_configured_ftp_braucht_user():
    assert _cfg(protocol="ftp", username=None).is_fully_configured is False
    assert _cfg(protocol="ftp", username="ec").is_fully_configured is True


def test_is_fully_configured_rclone_braucht_remote():
    assert _cfg(protocol="rclone", host=None, rclone_remote=None).is_fully_configured is False
    assert _cfg(protocol="rclone", host=None, rclone_remote="offsite:").is_fully_configured is True


def test_is_fully_configured_ohne_host():
    assert _cfg(host=None).is_fully_configured is False


# ── config_aus_org (Mapping + Passwort-Entschluesselung) ──────────────────────

def test_config_aus_org_mappt_und_entschluesselt():
    row = _cfg(protocol="ftps", port=2121, password_enc=encrypt_secret("geheim"),
              ssh_strict="yes", remote_path="/up")
    cfg = rbs.config_aus_org(row, key_path="/tmp/k")
    assert cfg.protocol == "ftps"
    assert cfg.host == "backup.example.org"
    assert cfg.port == 2121
    assert cfg.user == "ec"
    assert cfg.password == "geheim"        # entschluesselt
    assert cfg.key == "/tmp/k"             # durchgereicht
    assert cfg.path == "/up"
    assert cfg.ssh_strict == "yes"


def test_config_aus_org_ohne_passwort():
    cfg = rbs.config_aus_org(_cfg())
    assert cfg.password == ""
    assert cfg.key == ""


# ── org_remote_config (SSH-Key materialisieren + aufraeumen) ──────────────────

def test_org_remote_config_materialisiert_key():
    key_text = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    row = _cfg(ssh_key_enc=encrypt_secret(key_text))
    erzeugte_datei = None
    with rbs.org_remote_config(row) as cfg:
        assert cfg.key, "key_path muss gesetzt sein"
        erzeugte_datei = Path(cfg.key)
        assert erzeugte_datei.exists()
        assert key_text in erzeugte_datei.read_text(encoding="utf-8")
    # Nach dem Block ist die temporaere Key-Datei entfernt.
    assert erzeugte_datei is not None and not erzeugte_datei.exists()


def test_org_remote_config_ohne_key():
    with rbs.org_remote_config(_cfg()) as cfg:
        assert cfg.key == ""
