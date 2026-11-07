"""0358 ohne neue FKs und ohne SQLite-CASCADE-Datenverlust."""
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_migration_0332_objekt_dokument_georef import migration


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "mariadb"])
def test_release_migration_ddl(dialect):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name=dialect,
        opts={"as_sql": True, "output_buffer": output})):
        revision = migration("0358", "infoscreen_releases")
        assert revision.revision == "0358" and revision.down_revision == "0357"
        revision.upgrade()
        revision.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE infoscreen_release_upload_token" in sql and "token_hash" in sql
    assert "FOREIGN KEY" not in sql
    if dialect != "sqlite":
        assert "VARCHAR(100)" in sql and "VARCHAR(50)" in sql
    else:
        assert "DROP TABLE infoscreen_device" not in sql


def test_sqlite_upgrade_erhaelt_geraet_und_display(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'release.db'}")
    with engine.begin() as conn:
        for name in ("fire_dept", "user", "alarm_infoscreen_token"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
        conn.exec_driver_sql("CREATE TABLE org_settings (id INTEGER PRIMARY KEY, org_id BIGINT)")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        with Operations.context(MigrationContext.configure(conn)):
            migration("0355", "infoscreen_agent").upgrade()
            migration("0356", "infoscreen_resolution_hash").upgrade()
            migration("0357", "infoscreen_heartbeat").upgrade()
            conn.exec_driver_sql("INSERT INTO fire_dept VALUES (1)")
            conn.exec_driver_sql("INSERT INTO infoscreen_device (id, org_id, name, erstellt_am) "
                                 "VALUES (1, 1, 'Halle', CURRENT_TIMESTAMP)")
            conn.exec_driver_sql("INSERT INTO infoscreen_display (id, org_id, device_id, display_key, name) "
                                 "VALUES (1, 1, 1, 'main', 'Monitor')")
            migration("0358", "infoscreen_releases").upgrade()
            assert conn.exec_driver_sql("SELECT device_id FROM infoscreen_display").scalar() == 1
            migration("0358", "infoscreen_releases").downgrade()
            assert conn.exec_driver_sql("SELECT name FROM infoscreen_device").scalar() == "Halle"
            assert conn.exec_driver_sql("SELECT device_id FROM infoscreen_display").scalar() == 1
    engine.dispose()
