"""Presence-Migration erhaelt bestehende Gateway-Daten und Tokens."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_downgrade_upgrade_preserves_gateway(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0306_print_agent_presence.py"
    spec = importlib.util.spec_from_file_location("migration_0306", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'presence.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE gateway (id INTEGER PRIMARY KEY, device_token_hash VARCHAR(64))")
        connection.exec_driver_sql("INSERT INTO gateway VALUES (2, 'test-token-hash')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            columns = {column["name"] for column in sa.inspect(connection).get_columns("gateway")}
            expected = {"agent_connection_id", "agent_connected_at", "agent_heartbeat_at", "agent_presence_expires_at"}
            assert expected <= columns
            assert connection.exec_driver_sql("SELECT agent_connection_id FROM gateway WHERE id=2").scalar() is None
            migration.downgrade()
            migration.upgrade()
        saved_hash = connection.exec_driver_sql("SELECT device_token_hash FROM gateway WHERE id=2").scalar()
        assert saved_hash == "test-token-hash"
