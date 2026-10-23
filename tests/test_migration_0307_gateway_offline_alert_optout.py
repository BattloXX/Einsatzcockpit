"""Opt-out-Migration erhaelt Bestand und aktiviert Ausfallalarme standardmaessig."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_downgrade_upgrade_preserves_gateways(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0307_gateway_offline_alert_optout.py"
    spec = importlib.util.spec_from_file_location("migration_0307", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'optout.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE gateway (id INTEGER PRIMARY KEY, device_token_hash VARCHAR(64))")
        connection.exec_driver_sql("INSERT INTO gateway VALUES (1, 'test-token')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT offline_alert_enabled FROM gateway WHERE id=1").scalar() == 1
            connection.exec_driver_sql("INSERT INTO gateway (id) VALUES (2)")
            assert connection.exec_driver_sql("SELECT offline_alert_enabled FROM gateway WHERE id=2").scalar() == 1
            connection.exec_driver_sql("UPDATE gateway SET offline_alert_enabled=0 WHERE id=1")
            assert connection.exec_driver_sql("SELECT offline_alert_enabled FROM gateway WHERE id=1").scalar() == 0
            migration.downgrade()
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT device_token_hash FROM gateway WHERE id=1").scalar() == 'test-token'
