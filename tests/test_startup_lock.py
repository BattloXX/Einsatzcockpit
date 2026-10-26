"""Startup-Lock und die begrenzte Behandlung konkurrierender Bootstrap-Fehler."""

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from app import main, seed_data
from app.services.startup_lock import startup_lock


def test_sqlite_does_not_connect_or_execute_lock_sql():
    engine = create_engine("sqlite://")
    connected = Mock()
    event.listen(engine, "connect", connected)
    try:
        with startup_lock(engine):
            pass
        connected.assert_not_called()
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
@pytest.mark.parametrize("fail", [False, True])
def test_lock_uses_same_dedicated_connection_and_releases_on_error(dialect, fail):
    engine = MagicMock()
    engine.dialect.name = dialect
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = 1

    def work():
        with startup_lock(engine):
            assert connection.execute.call_count == 1
            if fail:
                raise ValueError("startup failed")

    if fail:
        with pytest.raises(ValueError, match="startup failed"):
            work()
    else:
        work()
    engine.connect.assert_called_once_with()
    assert [str(call.args[0]) for call in connection.execute.call_args_list] == [
        "SELECT GET_LOCK('ecp_bootstrap', 60)",
        "SELECT RELEASE_LOCK('ecp_bootstrap')",
    ]
    engine.connect.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("result", [0, None])
def test_lock_timeout_warns_and_runs_block(result, caplog):
    engine = MagicMock()
    engine.dialect.name = "mysql"
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = result
    work = Mock()
    with startup_lock(engine):
        work()
    work.assert_called_once_with()
    assert "Startup-Lock ecp_bootstrap nicht erhalten" in caplog.text
    assert "Start wird fortgesetzt" in caplog.text
    assert connection.execute.call_count == 1


@pytest.mark.parametrize("existing_user", [None, object()])
@pytest.mark.parametrize("stage", ["roles", "seed", "admin"])
def test_bootstrap_integrity_error_requires_user_after_rollback(monkeypatch, caplog, existing_user, stage):
    from app import cli

    db = Mock()
    db.info = {}
    error = IntegrityError("INSERT", {}, Exception("duplicate"))
    monkeypatch.setattr(main, "SessionLocal", Mock(return_value=db))
    roles = Mock()
    seed = Mock()
    admin = Mock()
    monkeypatch.setattr(seed_data, "_upsert_roles", roles)
    monkeypatch.setattr(seed_data, "seed", seed)
    monkeypatch.setattr(cli, "create_admin", admin)
    {"roles": roles, "seed": seed, "admin": admin}[stage].side_effect = error
    values = iter([existing_user] if stage == "roles" else [None, existing_user])

    def first():
        if stage == "roles" or db.query.return_value.first.call_count == 2:
            db.rollback.assert_called_once_with()
        return next(values)

    db.query.return_value.first.side_effect = first
    if existing_user is None:
        with pytest.raises(IntegrityError) as raised:
            main._bootstrap_admin()
        assert raised.value is error
    else:
        main._bootstrap_admin()
        assert "paralleler Worker war schneller" in caplog.text
    db.rollback.assert_called_once_with()
    db.close.assert_called_once_with()


def test_lifespan_bootstrap_and_weather_share_main_database_lock(monkeypatch):
    from app import db_weather
    from app.services import startup_lock as module

    calls = []
    locked = False

    @contextmanager
    def lock(engine):
        nonlocal locked
        assert engine is main.engine
        locked = True
        try:
            yield
        finally:
            locked = False

    def work(label):
        assert locked
        calls.append(label)

    monkeypatch.setattr(module, "startup_lock", lock)
    monkeypatch.setattr(main, "_bootstrap_admin", lambda: work("bootstrap"))
    monkeypatch.setattr(db_weather, "init_weather_db", lambda: work("weather"))
    with TestClient(main.app):
        assert calls == ["bootstrap", "weather"]
        assert not locked
