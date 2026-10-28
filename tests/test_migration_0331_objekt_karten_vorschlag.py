"""Reversible Migration: Legacy bleibt ungeprueft, FKs bleiben BIGINT."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app.models.objekt import ObjektKartenVorschlag


def test_upgrade_downgrade_legacy_und_fk_typen(tmp_path):
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0331_objekt_karten_vorschlag.py"
    spec = importlib.util.spec_from_file_location("migration_0331", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0331" and migration.down_revision == "0330"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'karte.db'}")
    with engine.begin() as conn:
        for tabelle in ("objekt", "fire_dept", "user", "objekt_dokument"):
            conn.exec_driver_sql(f"CREATE TABLE {tabelle} (id BIGINT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE objekt_karten_objekt (id BIGINT PRIMARY KEY, typ VARCHAR(40))")
        conn.exec_driver_sql("INSERT INTO objekt_karten_objekt VALUES (1, 'bmz')")
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            zeile = conn.exec_driver_sql("SELECT position_status, revision FROM objekt_karten_objekt").one()
            assert zeile == ("ungeprueft", 1)
            for tabelle in ("objekt_karten_objekt", "objekt_karten_vorschlag"):
                metadata = sa.MetaData()
                table = sa.Table(tabelle, metadata, autoload_with=conn)
                # SQLite-Compiler verwendet INTEGER fuer BIGINT; MySQL-Modell dagegen muss BIGINT liefern.
                for fk in table.foreign_keys:
                    assert fk.ondelete in {"CASCADE", "SET NULL"}
            spalten = {c["name"]: c for c in sa.inspect(conn).get_columns("objekt_karten_vorschlag")}
            for c in ObjektKartenVorschlag.__table__.columns:
                assert spalten[c.name]["nullable"] == c.nullable
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert "objekt_karten_vorschlag" not in sa.inspect(conn).get_table_names()
            assert conn.exec_driver_sql("SELECT * FROM objekt_karten_objekt").one() == (1, "bmz")
    for fk in ObjektKartenVorschlag.__table__.foreign_keys:
        assert isinstance(fk.parent.type, sa.BigInteger)
    ddl = str(CreateTable(ObjektKartenVorschlag.__table__).compile(dialect=mysql.dialect()))
    assert "BIGINT" in ddl
    engine.dispose()
