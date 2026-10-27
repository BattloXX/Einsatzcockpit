"""Opt-in bleibt fuer bestehende Organisationen aus; SQLite und MariaDB-DDL."""
import importlib.util
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0323_alarm_parser_mcp_aktiv.py"
    spec = importlib.util.spec_from_file_location("migration_0323", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_upgrade_default_not_null_and_downgrade(tmp_path):
    module = migration()
    assert module.revision == "0323" and module.down_revision == "0322"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE org_settings (id INTEGER PRIMARY KEY, org_id BIGINT NOT NULL)")
        conn.exec_driver_sql("INSERT INTO org_settings VALUES (1, 10)")
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert conn.exec_driver_sql("SELECT alarm_parser_mcp_aktiv FROM org_settings").scalar_one() == 0
        conn.exec_driver_sql("INSERT INTO org_settings (id, org_id) VALUES (2, 20)")
        column = next(c for c in sa.inspect(conn).get_columns("org_settings") if c["name"] == "alarm_parser_mcp_aktiv")
        assert not column["nullable"]
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            conn.exec_driver_sql("UPDATE org_settings SET alarm_parser_mcp_aktiv=NULL WHERE id=1")
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert [c["name"] for c in sa.inspect(conn).get_columns("org_settings")] == ["id", "org_id"]
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM org_settings").scalar_one() == 2
    engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_mariadb_ddl(dialect):
    output = StringIO()
    with Operations.context(MigrationContext.configure(
        dialect_name=dialect, opts={"as_sql": True, "output_buffer": output},
    )):
        migration().upgrade()
        migration().downgrade()
    ddl = output.getvalue()
    assert "alarm_parser_mcp_aktiv BOOL NOT NULL DEFAULT false" in ddl
    assert "DROP COLUMN alarm_parser_mcp_aktiv" in ddl
