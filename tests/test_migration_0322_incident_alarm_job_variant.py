"""SQLite-Datenbestand und MariaDB-FK-Indexreihenfolge fuer Nachalarmvarianten."""
import importlib.util
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0322_incident_alarm_job_variant.py"
    spec = importlib.util.spec_from_file_location("migration_0322", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing_index", [False, True])
def test_sqlite_upgrade_default_unique_fk_and_downgrade(tmp_path, existing_index):
    module = migration()
    assert module.revision == "0322" and module.down_revision == "0321"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'variants.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.exec_driver_sql("CREATE TABLE incident (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO incident VALUES (1)")
        metadata = sa.MetaData()
        sa.Table("incident", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        table = sa.Table(
            "incident_alarm_job", metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.UniqueConstraint("incident_id", "channel", name="uq_incident_alarm_job_incident_channel"),
        )
        table.create(conn)
        if existing_index:
            conn.exec_driver_sql("CREATE INDEX ix_incident_alarm_job_incident_id ON incident_alarm_job (incident_id)")
        for table in ("sms_log", "mail_einsatzinfo_log"):
            conn.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql(f"INSERT INTO {table} VALUES (1)")
        conn.exec_driver_sql("INSERT INTO incident_alarm_job VALUES (1, 1, 'sms')")
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert conn.exec_driver_sql("SELECT variant FROM incident_alarm_job").scalar() == ""
        for table in ("sms_log", "mail_einsatzinfo_log"):
            assert conn.exec_driver_sql(f"SELECT variant FROM {table}").scalar() == ""
        indexes = sa.inspect(conn).get_indexes("incident_alarm_job")
        assert sum(index["column_names"] == ["incident_id"] for index in indexes) == 1
        conn.exec_driver_sql("INSERT INTO incident_alarm_job (incident_id, channel, variant) "
                             "VALUES (1, 'sms', 'nach:code:r2')")
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql("INSERT INTO incident_alarm_job (incident_id, channel, variant) "
                                 "VALUES (1, 'sms', 'nach:code:r2')")
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql("INSERT INTO incident_alarm_job (incident_id, channel, variant) "
                                 "VALUES (1, 'push', NULL)")
        conn.exec_driver_sql("DELETE FROM incident WHERE id=1")
        assert conn.exec_driver_sql("SELECT count(*) FROM incident_alarm_job").scalar() == 0
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert "variant" not in {column["name"] for column in sa.inspect(conn).get_columns("incident_alarm_job")}
    engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_mariadb_ddl_index_before_drop(dialect):
    output = StringIO()
    context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    ddl = output.getvalue()
    assert ddl.index("CREATE INDEX ix_incident_alarm_job_variant_fk") < ddl.index(
        "DROP INDEX uq_incident_alarm_job_incident_channel")
    assert "UNIQUE (incident_id, channel, variant)" in ddl
    assert "variant VARCHAR(40) NOT NULL DEFAULT ''" in ddl
    assert "UNIQUE (incident_id, channel)" in ddl
