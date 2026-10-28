"""Zentrale App-Verbindung verhindert 1020 nach konkurrierendem Commit."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError

from app.db import _set_db_timezone_utc

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


@pytest.fixture(params=[True, False], ids=["snapshot_off", "server_default"])
def database(request, monkeypatch):
    monkeypatch.setattr("app.db.settings.DB_SNAPSHOT_ISOLATION_OFF", request.param)
    engine = create_engine(os.environ["MARIADB_TEST_URL"], isolation_level="REPEATABLE READ")
    # Derselbe Connect-Listener wie bei der App-Engine, keine manuelle Session-Anpassung.
    event.listen(engine, "connect", _set_db_timezone_utc)
    table = "snapshot_phase1_" + uuid4().hex
    try:
        with engine.begin() as db:
            assert db.scalar(text("SELECT @@GLOBAL.innodb_snapshot_isolation")) == 1
            db.execute(text(f"CREATE TABLE {table} (id INT PRIMARY KEY, version INT NOT NULL) ENGINE=InnoDB"))
            db.execute(text(f"INSERT INTO {table} VALUES (1, 1)"))
        yield engine, table, request.param
    finally:
        with engine.begin() as db:
            db.execute(text(f"DROP TABLE IF EXISTS {table}"))
        engine.dispose()


@pytest.mark.parametrize("operation", ["locking_read", "conditional_update"])
def test_competing_commit_uses_latest_version(database, operation):
    engine, table, snapshot_off = database
    with engine.connect() as a:
        assert a.scalar(text("SELECT @@SESSION.innodb_snapshot_isolation")) == int(not snapshot_off)
        assert a.scalar(text("SELECT @@SESSION.time_zone")) == "+00:00"
        assert a.scalar(text("SELECT @@SESSION.tx_isolation")) == "REPEATABLE-READ"
        assert a.scalar(text(f"SELECT version FROM {table} WHERE id=1")) == 1
        with engine.begin() as b:
            assert a.scalar(text("SELECT CONNECTION_ID()")) != b.scalar(text("SELECT CONNECTION_ID()"))
            b.execute(text(f"UPDATE {table} SET version=2 WHERE id=1"))
        # Der normale Read bleibt im alten Snapshot, der Locking-Read muss Version 2 sehen.
        assert a.scalar(text(f"SELECT version FROM {table} WHERE id=1")) == 1

        def apply():
            if operation == "locking_read":
                assert a.scalar(text(f"SELECT version FROM {table} WHERE id=1 FOR UPDATE")) == 2
            else:
                assert a.execute(text(f"UPDATE {table} SET version=3 WHERE id=1 AND version=2")).rowcount == 1
                assert a.scalar(text(f"SELECT version FROM {table} WHERE id=1 FOR UPDATE")) == 3

        if snapshot_off:
            apply()
            a.commit()
        else:
            with pytest.raises(OperationalError) as error:
                apply()
            assert error.value.orig.args[0] == 1020
            a.rollback()
