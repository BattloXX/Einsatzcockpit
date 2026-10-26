"""Alarmversion: Bestand, nicht eindeutiger Index und beide SQL-Dialekte."""
import importlib.util
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0320_alarm_ingest_version.py"
    spec = importlib.util.spec_from_file_location("migration_0320", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_upgrade_preserves_rows_nullable_non_unique_and_downgrade(tmp_path):
    module = migration()
    assert module.revision == "0320" and module.down_revision == "0319"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'version.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE alarm_ingest (id INTEGER PRIMARY KEY, einsatz_id BIGINT, raw_text TEXT)")
        conn.exec_driver_sql("INSERT INTO alarm_ingest VALUES (1, 7, 'Original')")
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        columns = {c["name"]: c for c in sa.inspect(conn).get_columns("alarm_ingest")}
        assert columns["version_no"]["nullable"] and isinstance(columns["version_no"]["type"], sa.Integer)
        assert conn.exec_driver_sql("SELECT raw_text, version_no FROM alarm_ingest").one() == ("Original", None)
        index = next(i for i in sa.inspect(conn).get_indexes("alarm_ingest")
                     if i["name"] == "ix_alarm_ingest_einsatz_version")
        assert index["column_names"] == ["einsatz_id", "version_no"] and not index["unique"]
        conn.exec_driver_sql("INSERT INTO alarm_ingest VALUES (2, 7, 'A', 1), (3, 7, 'B', 1)")
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert "version_no" not in {c["name"] for c in sa.inspect(conn).get_columns("alarm_ingest")}
        assert conn.exec_driver_sql("SELECT raw_text FROM alarm_ingest ORDER BY id").scalars().all() == ["Original", "A", "B"]
    engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_mariadb_upgrade_and_downgrade_ddl(dialect):
    output = StringIO()
    context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    ddl = output.getvalue()
    assert "ADD COLUMN version_no INTEGER" in ddl
    assert "CREATE INDEX ix_alarm_ingest_einsatz_version ON alarm_ingest (einsatz_id, version_no)" in ddl
    assert "DROP COLUMN version_no" in ddl
