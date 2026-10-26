"""Additive Presence-Erweiterung: Token, vorhandene Windows-Presence und Jobs bleiben erhalten."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_downgrade_upgrade_preserves_existing_tokens_and_presence(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0312_gateway_presence_routing.py"
    spec = importlib.util.spec_from_file_location("migration_0312", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'routing.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fire_dept (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE gateway (id INTEGER PRIMARY KEY, device_token_hash TEXT, agent_connection_id TEXT)"
        )
        conn.exec_driver_sql("CREATE TABLE sms_gateway_token (id INTEGER PRIMARY KEY, token_hash TEXT)")
        conn.exec_driver_sql("INSERT INTO gateway VALUES (1, 'gateway-hash', 'existing-cid')")
        conn.exec_driver_sql("INSERT INTO sms_gateway_token VALUES (2, 'sms-hash')")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert {"agent_worker_id", "agent_connection_token_hash"} <= {
                column["name"] for column in sa.inspect(conn).get_columns("gateway")
            }
            assert "gateway_message" in sa.inspect(conn).get_table_names()
            assert conn.exec_driver_sql("SELECT worker_id FROM sms_gateway_token WHERE id=2").scalar() is None
            migration.downgrade()
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT device_token_hash FROM gateway WHERE id=1").scalar() == "gateway-hash"
        assert conn.exec_driver_sql("SELECT agent_connection_id FROM gateway WHERE id=1").scalar() == "existing-cid"
        assert conn.exec_driver_sql("SELECT token_hash FROM sms_gateway_token WHERE id=2").scalar() == "sms-hash"
