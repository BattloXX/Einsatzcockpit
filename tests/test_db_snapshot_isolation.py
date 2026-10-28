"""Versionsgrenze und fehlertolerante Session-Einstellung fuer MariaDB."""

from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.db import _set_db_timezone_utc, apply_snapshot_isolation_off, supports_mariadb_snapshot_isolation


@pytest.mark.parametrize(("version", "expected"), [
    ("12.3.1-MariaDB", True),
    ("10.11.6-MariaDB", False),
    ("11.6.1-MariaDB", False),
    ("11.6.2-MariaDB-ubi", True),
    ("5.5.5-12.3.1-MariaDB", True),
    ("8.0.36", False),
    ("8.0.36-MySQL", False),
    ("", False),
    ("MariaDB", False),
])
def test_version_boundary(version, expected):
    assert supports_mariadb_snapshot_isolation(version) is expected


def test_setting_default_and_env(monkeypatch):
    monkeypatch.delenv("DB_SNAPSHOT_ISOLATION_OFF", raising=False)
    assert Settings(_env_file=None).DB_SNAPSHOT_ISOLATION_OFF is True
    monkeypatch.setenv("DB_SNAPSHOT_ISOLATION_OFF", "false")
    assert Settings(_env_file=None).DB_SNAPSHOT_ISOLATION_OFF is False


@pytest.mark.parametrize("version", ["12.3.1-MariaDB", "10.11.6-MariaDB", "8.0.36"])
@pytest.mark.parametrize("enabled", [True, False])
def test_apply_only_for_supported_server(version, enabled):
    connection = MagicMock()
    connection.get_server_info.return_value = version
    apply_snapshot_isolation_off(connection, enabled)
    if enabled and version == "12.3.1-MariaDB":
        connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "SET SESSION innodb_snapshot_isolation=OFF")
        connection.cursor.return_value.__exit__.assert_called_once()
    else:
        connection.cursor.assert_not_called()


def test_sqlite_skipped():
    connection = MagicMock(spec=["cursor"])
    apply_snapshot_isolation_off(connection, True)
    connection.cursor.assert_not_called()


@pytest.mark.parametrize("code", [1193, 1227])
def test_connect_error_logged_and_ignored(code, caplog):
    connection = MagicMock()
    connection.get_server_info.return_value = "12.3.1-MariaDB"
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError(code, "Geheimer Treibertext")
    apply_snapshot_isolation_off(connection, True)
    assert "einsatzleiter.db" in caplog.text
    assert "Geheimer Treibertext" not in caplog.text
    if code == 1193:
        assert "1193" in caplog.text
    connection.cursor.return_value.__exit__.assert_called_once()


def test_listener_applies_snapshot_setting_after_timezone(monkeypatch):
    monkeypatch.setattr("app.db.settings.DB_SNAPSHOT_ISOLATION_OFF", True)
    connection = MagicMock()
    connection.get_server_info.return_value = "12.3.1-MariaDB"
    _set_db_timezone_utc(connection, None)
    cursor = connection.cursor.return_value.__enter__.return_value
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SET time_zone = '+00:00'", "SET SESSION innodb_snapshot_isolation=OFF",
    ]
