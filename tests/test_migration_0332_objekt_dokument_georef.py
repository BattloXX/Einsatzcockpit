"""Upgrade/Downgrade und MariaDB-BIGINT-FKs fuer Georeferenzen."""

import importlib.util
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.objekt import ObjektDokumentGeoref


def migration(nummer, name):
    pfad = Path(__file__).resolve().parents[1] / f"alembic/versions/{nummer}_{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{nummer}", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_upgrade_downgrade(tmp_path):
    vorher = migration("0331", "objekt_karten_vorschlag")
    neu = migration("0332", "objekt_dokument_georef")
    assert neu.down_revision == "0331" and neu.revision == "0332"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'georef.db'}")
    with engine.begin() as conn:
        for tabelle in ("objekt", "fire_dept", "user", "objekt_dokument", "objekt_karten_objekt"):
            conn.exec_driver_sql(f"CREATE TABLE {tabelle} (id BIGINT PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(conn)):
            vorher.upgrade()
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                neu.upgrade()
            spalten = {c["name"]: c for c in sa.inspect(conn).get_columns("objekt_dokument_georef")}
            for c in ObjektDokumentGeoref.__table__.columns:
                assert spalten[c.name]["nullable"] == c.nullable
            fk = next(
                f
                for f in sa.inspect(conn).get_foreign_keys("objekt_karten_vorschlag")
                if f["constrained_columns"] == ["georef_id"]
            )
            assert fk["options"]["ondelete"] == "SET NULL"
            with Operations.context(MigrationContext.configure(conn)):
                neu.downgrade()
            assert "objekt_dokument_georef" not in sa.inspect(conn).get_table_names()
            assert "georef_id" not in {c["name"] for c in sa.inspect(conn).get_columns("objekt_karten_vorschlag")}
    engine.dispose()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_bigint_mariadb_ddl(dialect):
    neu = migration("0332", "objekt_dokument_georef")
    buf = StringIO()
    with Operations.context(
        MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": buf})
    ):
        neu.upgrade()
        neu.downgrade()
    ddl = buf.getvalue()
    assert "georef_id BIGINT" in ddl
    assert "DROP FOREIGN KEY fk_vorschlag_georef" in ddl
    for fk in ObjektDokumentGeoref.__table__.foreign_keys:
        assert isinstance(fk.parent.type, sa.BigInteger)
    assert ddl.index("DROP FOREIGN KEY fk_vorschlag_georef") < ddl.index("DROP TABLE objekt_dokument_georef")
