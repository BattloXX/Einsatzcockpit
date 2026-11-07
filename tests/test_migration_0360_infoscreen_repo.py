"""0360: Metadaten nullable, SQLite-Datenerhalt und MariaDB-DDL."""
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_migration_0332_objekt_dokument_georef import migration


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_ddl(dialect):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name=dialect,
        opts={"as_sql": True, "output_buffer": output})):
        revision = migration("0360", "infoscreen_repo")
        assert revision.revision == "0360" and revision.down_revision == "0359"
        revision.upgrade()
        revision.downgrade()
    sql = output.getvalue()
    assert "MODIFY sha256 VARCHAR(64) NULL" in sql
    assert "MODIFY size BIGINT NULL" in sql
    assert "FOREIGN KEY" not in sql
    assert "DROP TABLE infoscreen_device" not in sql


def test_sqlite_preserves_artifacts_and_devices(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'repo.db'}")
    with engine.begin() as conn:
        for name in ("fire_dept", "user", "alarm_infoscreen_token"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
        conn.exec_driver_sql("CREATE TABLE org_settings (id INTEGER PRIMARY KEY, org_id BIGINT)")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        with Operations.context(MigrationContext.configure(conn)):
            for number, name in (("0355", "infoscreen_agent"), ("0356", "infoscreen_resolution_hash"),
                                 ("0357", "infoscreen_heartbeat"), ("0358", "infoscreen_releases"),
                                 ("0359", "infoscreen_release_keys")):
                # 0359 aendert keine der hier getesteten Tabellen.
                if number != "0359":
                    migration(number, name).upgrade()
            conn.exec_driver_sql("INSERT INTO fire_dept VALUES (1)")
            conn.exec_driver_sql("INSERT INTO infoscreen_device (id, org_id, name, erstellt_am) "
                                 "VALUES (1, 1, 'Halle', CURRENT_TIMESTAMP)")
            conn.exec_driver_sql("INSERT INTO infoscreen_display (id, org_id, device_id, display_key, name) "
                                 "VALUES (1, 1, 1, 'main', 'Monitor')")
            conn.exec_driver_sql("INSERT INTO infoscreen_release "
                "(id, version, channel, arch, sha256, signature, size, storage_path, created_at) "
                "VALUES (1, '1.0', 'stable', 'amd64', 'abc', 'sig', 123, 'file.deb', CURRENT_TIMESTAMP)")
            revision = migration("0360", "infoscreen_repo")
            revision.upgrade()
            conn.exec_driver_sql("INSERT INTO infoscreen_release "
                "(id, version, channel, arch, created_at) VALUES (2, '2.0', 'stable', 'amd64', CURRENT_TIMESTAMP)")
            assert conn.exec_driver_sql("SELECT sha256 FROM infoscreen_release WHERE id=2").scalar() is None
            assert conn.exec_driver_sql("SELECT device_id FROM infoscreen_display").scalar() == 1
            revision.downgrade()
            assert conn.exec_driver_sql("SELECT storage_path FROM infoscreen_release").all() == [('file.deb',)]
            assert conn.exec_driver_sql("SELECT device_id FROM infoscreen_display").scalar() == 1
    engine.dispose()
