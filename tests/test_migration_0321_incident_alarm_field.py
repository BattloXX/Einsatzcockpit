"""SQLite-FKs und MariaDB-DDL fuer den Herkunftsschutz."""
import importlib.util
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0321_incident_alarm_field.py"
    spec = importlib.util.spec_from_file_location("migration_0321", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_constraints_cascade_set_null_and_downgrade(tmp_path):
    module = migration()
    assert module.revision == "0321" and module.down_revision == "0320"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fields.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for table in ("incident", "fire_dept", "alarm_ingest"):
            conn.exec_driver_sql(f"CREATE TABLE {table} (id BIGINT PRIMARY KEY)")
            conn.exec_driver_sql(f"INSERT INTO {table} VALUES (1)")
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        conn.exec_driver_sql("INSERT INTO incident_alarm_field (incident_id, org_id, field, value, source, ingest_id, updated_at) "
                             "VALUES (1, 1, 'address_no', NULL, 'alarm', 1, '2026-10-26 00:00:00')")
        assert conn.exec_driver_sql("SELECT id, value FROM incident_alarm_field").one() == (1, None)
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql("INSERT INTO incident_alarm_field (incident_id, field, source, updated_at) "
                                 "VALUES (1, 'address_no', 'manual', '2026-10-26 00:00:00')")
        conn.exec_driver_sql("DELETE FROM alarm_ingest WHERE id=1")
        assert conn.exec_driver_sql("SELECT ingest_id FROM incident_alarm_field").scalar() is None
        conn.exec_driver_sql("DELETE FROM incident WHERE id=1")
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM incident_alarm_field").scalar() == 0
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert "incident_alarm_field" not in sa.inspect(conn).get_table_names()
    engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_mariadb_bigint_foreign_keys_and_downgrade(dialect):
    output = StringIO()
    context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    ddl = output.getvalue()
    for column in ("id", "incident_id", "org_id", "ingest_id"):
        assert f"{column} BIGINT" in ddl
    assert "REFERENCES incident (id) ON DELETE CASCADE" in ddl
    assert "REFERENCES alarm_ingest (id) ON DELETE SET NULL" in ddl
    assert "UNIQUE (incident_id, field)" in ddl
    assert "CREATE INDEX ix_incident_alarm_field_org_id" in ddl
    assert "DROP TABLE incident_alarm_field" in ddl
