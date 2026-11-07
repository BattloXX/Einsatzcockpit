"""0359: portables DDL, exakter BIGINT-User-FK und verlustfreies Downgrade."""
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.infoscreen_agent import InfoscreenReleaseKey
from app.models.user import User
from tests.test_migration_0332_objekt_dokument_georef import migration


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "mariadb"])
def test_release_keys_ddl(dialect):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name=dialect,
            opts={"as_sql": True, "output_buffer": output})):
        rev = migration("0359", "infoscreen_release_keys")
        assert rev.revision == "0359" and rev.down_revision == "0358"
        rev.upgrade()
        rev.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE infoscreen_release_key" in sql
    assert ("bestaetigt_von INTEGER" if dialect == "sqlite" else "bestaetigt_von BIGINT") in sql
    assert "FOREIGN KEY(bestaetigt_von) REFERENCES user (id) ON DELETE SET NULL" in sql
    assert "DROP TABLE infoscreen_release_key" in sql
    assert type(InfoscreenReleaseKey.__table__.c.bestaetigt_von.type) is type(User.__table__.c.id.type)


def test_release_keys_sqlite_roundtrip(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'keys.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id BIGINT PRIMARY KEY)')
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.exec_driver_sql('INSERT INTO "user" VALUES (1)')
        with Operations.context(MigrationContext.configure(conn)):
            rev = migration("0359", "infoscreen_release_keys")
            rev.upgrade()
            conn.exec_driver_sql("INSERT INTO infoscreen_release_key "
                "(id, key_id, pem, fingerprint, bestaetigt_von, bestaetigt_am, quelle, erstellt_am) "
                "VALUES (1, 'release', 'pem', 'sha256:abc', 1, CURRENT_TIMESTAMP, 'repo', CURRENT_TIMESTAMP)")
            assert conn.exec_driver_sql("SELECT aktiv FROM infoscreen_release_key").scalar() == 1
            conn.exec_driver_sql('DELETE FROM "user" WHERE id=1')
            assert conn.exec_driver_sql("SELECT bestaetigt_von FROM infoscreen_release_key").scalar() is None
            rev.downgrade()
            assert "infoscreen_release_key" not in sa.inspect(conn).get_table_names()
    engine.dispose()
