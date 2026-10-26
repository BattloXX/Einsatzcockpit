"""Regeltabellen: SQLite-Upgrade/Downgrade und MariaDB-FK-Typen."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.exc import IntegrityError


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0319_alarm_parser_regelsatz.py"
    spec = importlib.util.spec_from_file_location("migration_0319", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_defaults_constraints_foreign_keys_and_downgrade(tmp_path):
    module = migration()
    assert module.revision == "0319" and module.down_revision == "0318"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'parser.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for name in ("fire_dept", "user", "alarm_ingest"):
            conn.exec_driver_sql(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql(f"INSERT INTO {name} VALUES (1)")
        conn.exec_driver_sql("INSERT INTO fire_dept VALUES (2)")
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        inspector = sa.inspect(conn)
        for table in ("alarm_parser_regelsatz", "alarm_parser_testfall"):
            columns = {c["name"]: c for c in inspector.get_columns(table)}
            assert isinstance(columns["org_id"]["type"], sa.Integer)
            assert isinstance(columns["erstellt_von_user_id"]["type"], sa.Integer)
            assert inspector.get_indexes(table)[0]["column_names"] == ["org_id"]
        conn.exec_driver_sql("INSERT INTO alarm_parser_regelsatz (org_id,version,regeln) VALUES (1,1,'{}'),(2,1,'{}')")
        row = conn.exec_driver_sql("SELECT * FROM alarm_parser_regelsatz WHERE org_id=1").mappings().one()
        assert row["status"] == "entwurf" and row["quelle"] == "ui" and row["erstellt_am"]
        assert row["aktiviert_am"] is None
        with pytest.raises(IntegrityError), conn.begin_nested():
            conn.exec_driver_sql("INSERT INTO alarm_parser_regelsatz (org_id,version,regeln) VALUES (1,1,'{}')")
        conn.exec_driver_sql(
            "INSERT INTO alarm_parser_testfall (org_id,titel,rohtext,erwartet,ingest_id,erstellt_von_user_id) "
            "VALUES (1,'Probe','Original','{}',1,1)"
        )
        assert conn.exec_driver_sql("SELECT aktiv FROM alarm_parser_testfall").scalar_one() == 1
        conn.exec_driver_sql("DELETE FROM alarm_ingest WHERE id=1")
        assert conn.exec_driver_sql("SELECT ingest_id FROM alarm_parser_testfall").scalar_one() is None
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
            assert "alarm_parser_regelsatz" not in sa.inspect(conn).get_table_names()
            module.upgrade()
        assert conn.exec_driver_sql("SELECT id FROM fire_dept WHERE id=1").scalar_one() == 1
    engine.dispose()


def test_mariadb_ddl_matches_existing_foreign_key_types():
    from app.models.gateway import AlarmParserRegelsatz, AlarmParserTestfall

    for model in (AlarmParserRegelsatz, AlarmParserTestfall):
        for column in model.__table__.columns:
            for fk in column.foreign_keys:
                assert column.type.compile(dialect=dialect()) == fk.column.type.compile(dialect=dialect())
    columns = {column.name: column for column in migration()._gemeinsame_spalten()}
    assert columns["org_id"].type.compile(dialect=dialect()) == "BIGINT"
    assert columns["erstellt_von_user_id"].type.compile(dialect=dialect()) == "BIGINT"
