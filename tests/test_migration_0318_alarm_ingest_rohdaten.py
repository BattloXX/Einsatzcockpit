"""Migration des Rohdatenspeichers mit bestehenden SQLite-Daten."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize("unique_kind", ["named", "unnamed", "index"])
def test_upgrade_preserves_data_and_replaces_unique_hash(tmp_path, unique_kind):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0318_alarm_ingest_rohdaten.py"
    spec = importlib.util.spec_from_file_location("migration_0318", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0318" and migration.down_revision == "0317"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'raw.db'}")
    unique = {"named": ", CONSTRAINT uq_alarm_ingest_raw_hash UNIQUE (raw_hash)",
              "unnamed": ", UNIQUE (raw_hash)", "index": ""}[unique_kind]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alarm_ingest (id INTEGER PRIMARY KEY, org_id BIGINT, gateway_id BIGINT NOT NULL, "
            "raw_hash VARCHAR(64) NOT NULL, raw_text TEXT NOT NULL, received_at DATETIME" + unique + ")"
        )
        if unique_kind == "index":
            conn.exec_driver_sql("CREATE UNIQUE INDEX ix_alarm_ingest_raw_hash ON alarm_ingest(raw_hash)")
        conn.exec_driver_sql("CREATE INDEX ix_alarm_ingest_org_received ON alarm_ingest(org_id, received_at)")
        conn.exec_driver_sql("INSERT INTO alarm_ingest VALUES (1, 10, 20, 'hash', 'Original', NULL)")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        row = conn.exec_driver_sql("SELECT * FROM alarm_ingest WHERE id=1").mappings().one()
        assert row["raw_text"] == "Original" and row["raw_hash"] == "hash"
        assert row["process_status"] == "applied" and row["content_hash"] == "hash"
        assert row["source"] == "wut" and row["process_attempts"] == 0
        assert row["raw_bytes"] is None and row["sequence"] is None
        columns = {column["name"]: column for column in sa.inspect(conn).get_columns("alarm_ingest")}
        assert not columns["raw_hash"]["nullable"]
        assert {column.name for column in migration._columns()} <= columns.keys()
        indexes = {index["name"]: index["column_names"] for index in sa.inspect(conn).get_indexes("alarm_ingest")}
        assert indexes["ix_alarm_ingest_org_nummer_hash"] == ["org_id", "leitstellennummer", "content_hash"]
        assert indexes["ix_alarm_ingest_leitstellennummer"] == ["leitstellennummer"]
        assert "ix_alarm_ingest_org_received" in indexes
        conn.exec_driver_sql(
            "INSERT INTO alarm_ingest (id, org_id, gateway_id, raw_hash, raw_text, sequence) "
            "VALUES (2, 10, 20, 'hash', 'Original', 7), (3, 10, 21, 'hash', 'Original', 7), "
            "(4, 10, 20, 'hash', 'Original', NULL)"
        )
        with pytest.raises(IntegrityError), conn.begin_nested():
            conn.exec_driver_sql(
                "INSERT INTO alarm_ingest (id, org_id, gateway_id, raw_hash, raw_text, sequence) "
                "VALUES (5, 10, 20, 'anderer-hash', 'Andere Daten', 7)"
            )
        conn.exec_driver_sql("DELETE FROM alarm_ingest WHERE id != 1")
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
            assert "raw_bytes" not in {column["name"] for column in sa.inspect(conn).get_columns("alarm_ingest")}
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT raw_text FROM alarm_ingest WHERE id=1").scalar_one() == "Original"
    engine.dispose()


def test_mariadb_unique_reflection_is_not_dropped_twice(tmp_path, monkeypatch):
    from sqlalchemy.dialects.mysql import dialect

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0318_alarm_ingest_rohdaten.py"
    spec = importlib.util.spec_from_file_location("migration_0318_mariadb", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    raw_bytes = next(column for column in migration._columns() if column.name == "raw_bytes")
    assert raw_bytes.type.compile(dialect=dialect()) == "MEDIUMBLOB"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reflection.db'}")
    real_inspect = sa.inspect

    class MariaDBInspector:
        def get_unique_constraints(self, table):
            return [{"name": "uq_alarm_ingest_raw_hash", "column_names": ["raw_hash"]}]

        def get_indexes(self, table):
            return [{"name": "uq_alarm_ingest_raw_hash", "column_names": ["raw_hash"],
                     "unique": True, "duplicates_constraint": "uq_alarm_ingest_raw_hash"}]

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alarm_ingest (id INTEGER PRIMARY KEY, org_id BIGINT, gateway_id BIGINT, "
            "raw_hash VARCHAR(64) NOT NULL, CONSTRAINT uq_alarm_ingest_raw_hash UNIQUE (raw_hash))"
        )
        # Nur die explizite Inspektion emulieren, Alembics SQLite-Reflexion bleibt echt.
        monkeypatch.setattr(migration.sa, "inspect", lambda bind: MariaDBInspector())
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert [constraint["column_names"] for constraint in real_inspect(conn).get_unique_constraints(
            "alarm_ingest"
        )] == [["gateway_id", "sequence"]]
    engine.dispose()
