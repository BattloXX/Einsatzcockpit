"""Richtung und Batch: reversible Migration und MariaDB-DDL."""

from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.objekt import ObjektKartenVorschlag
from tests.test_migration_0332_objekt_dokument_georef import migration


def test_upgrade_downgrade_modell(tmp_path):
    neu = migration("0334", "objekt_karten_richtung_batch")
    assert neu.revision == "0334" and neu.down_revision == "0333"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'richtung.db'}")
    with engine.begin() as conn:
        for tabelle in ("objekt", "fire_dept", "user", "objekt_dokument", "objekt_karten_objekt"):
            conn.exec_driver_sql(f"CREATE TABLE {tabelle} (id BIGINT PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(conn)):
            migration("0331", "objekt_karten_vorschlag").upgrade()
            migration("0332", "objekt_dokument_georef").upgrade()
            migration("0333", "objekt_karten_quellhash").upgrade()
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                neu.upgrade()
            spalten = {c["name"]: c for c in sa.inspect(conn).get_columns("objekt_karten_vorschlag")}
            for c in ObjektKartenVorschlag.__table__.columns:
                assert spalten[c.name]["nullable"] == c.nullable
            index = next(i for i in sa.inspect(conn).get_indexes("objekt_karten_vorschlag")
                         if i["name"] == "ix_objekt_karten_vorschlag_org_batch")
            assert index["column_names"] == ["org_id", "objekt_id", "batch_id"]
            with Operations.context(MigrationContext.configure(conn)):
                neu.downgrade()
            assert not {"bild_richtung_x", "bild_richtung_y", "batch_id"} & {
                c["name"] for c in sa.inspect(conn).get_columns("objekt_karten_vorschlag")
            }
    engine.dispose()


@pytest.mark.parametrize("dialekt", ["mysql", "mariadb"])
def test_mariadb_ddl(dialekt):
    neu = migration("0334", "objekt_karten_richtung_batch")
    ausgabe = StringIO()
    with Operations.context(MigrationContext.configure(
        dialect_name=dialekt, opts={"as_sql": True, "output_buffer": ausgabe},
    )):
        neu.upgrade()
        neu.downgrade()
    ddl = ausgabe.getvalue()
    assert "bild_richtung_x FLOAT" in ddl and "batch_id VARCHAR(64)" in ddl
    assert "(org_id, objekt_id, batch_id)" in ddl
    assert "DROP INDEX ix_objekt_karten_vorschlag_org_batch" in ddl
    assert "DROP COLUMN bild_richtung_y" in ddl
