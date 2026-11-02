"""Migration 0342: SQLite-Roundtrip und MariaDB-DDL ohne Server."""
import importlib.util
from io import StringIO

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration():
    spec = importlib.util.spec_from_file_location("fcm_ack_migration", "alembic/versions/0342_fcm_ack_wake_limit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_upgrade_downgrade_sqlite(monkeypatch):
    migration = _migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table("incident", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("fcm_token", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("push_log", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        inspector = sa.inspect(connection)
        ack = next(column for column in inspector.get_columns("fcm_token") if column["name"] == "last_ack_at")
        assert ack["nullable"] is True
        assert isinstance(ack["type"], sa.DateTime)
        fk = inspector.get_foreign_keys("push_log")[0]
        assert fk["referred_table"] == "incident"
        assert fk["options"]["ondelete"] == "SET NULL"
        migration.downgrade()
        inspector = sa.inspect(connection)
        assert [column["name"] for column in inspector.get_columns("fcm_token")] == ["id"]
        assert [column["name"] for column in inspector.get_columns("push_log")] == ["id"]
    engine.dispose()


def test_migration_mariadb_offline_sql(monkeypatch):
    migration = _migration()
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mariadb", opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(migration, "op", Operations(context))
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "ADD COLUMN last_ack_at DATETIME" in sql
    assert "FOREIGN KEY(incident_id) REFERENCES incident (id) ON DELETE SET NULL" in sql
    assert migration.revision == "0342"
    assert migration.down_revision == "0341"
