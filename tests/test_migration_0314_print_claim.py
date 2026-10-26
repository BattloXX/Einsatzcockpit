"""Additive Druckmigration erhaelt Print-Jobs und Token."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_downgrade_upgrade_preserves_jobs(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0314_linux_print_claim.py"
    spec = importlib.util.spec_from_file_location("print_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'print.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE gateway (id INTEGER PRIMARY KEY, device_token_hash TEXT)")
        conn.exec_driver_sql("CREATE TABLE print_job (id INTEGER PRIMARY KEY, status TEXT)")
        conn.exec_driver_sql("INSERT INTO gateway VALUES (1, 'keep-token')")
        conn.exec_driver_sql("INSERT INTO print_job VALUES (1, 'uncertain')")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.exec_driver_sql("SELECT print_protocol FROM gateway").scalar() == 1
            migration.downgrade()
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT device_token_hash FROM gateway").scalar() == "keep-token"
        assert conn.exec_driver_sql("SELECT status FROM print_job").scalar() == "uncertain"
