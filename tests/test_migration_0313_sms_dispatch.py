"""Additive SMS-Migration erhaelt Token, Logs und API-Empfaenger."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_downgrade_upgrade_preserves_existing_sms(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0313_sms_persistent_dispatch.py"
    spec = importlib.util.spec_from_file_location("sms_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sms.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fire_dept (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE sms_gateway_token (id INTEGER PRIMARY KEY, token_hash TEXT)")
        conn.exec_driver_sql("CREATE TABLE sms_log_recipient (id INTEGER PRIMARY KEY, success BOOLEAN)")
        conn.exec_driver_sql("CREATE TABLE api_message_recipient (id INTEGER PRIMARY KEY, status TEXT)")
        conn.exec_driver_sql("INSERT INTO sms_gateway_token VALUES (1, 'existing-hash')")
        conn.exec_driver_sql("INSERT INTO sms_log_recipient VALUES (1, 1)")
        conn.exec_driver_sql("INSERT INTO api_message_recipient VALUES (1, 'queued')")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.exec_driver_sql("SELECT sms_protocol FROM sms_gateway_token").scalar() == 1
            migration.downgrade()
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT token_hash FROM sms_gateway_token").scalar() == "existing-hash"
        assert conn.exec_driver_sql("SELECT success FROM sms_log_recipient").scalar() == 1
        assert conn.exec_driver_sql("SELECT status FROM api_message_recipient").scalar() == "queued"
