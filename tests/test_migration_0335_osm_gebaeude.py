"""Reversibles Schema und MariaDB-kompatible BIGINT-Fremdschluessel."""

from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.osm import OsmGebaeude, OsmImportLauf
from tests.test_migration_0332_objekt_dokument_georef import migration


def test_upgrade_downgrade_modell(tmp_path):
    neu = migration("0335", "osm_gebaeude")
    assert neu.revision == "0335" and neu.down_revision == "0334"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'osm_migration.db'}")
    with engine.begin() as conn:
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                neu.upgrade()
            for modell in (OsmImportLauf, OsmGebaeude):
                spalten = {c["name"]: c for c in sa.inspect(conn).get_columns(modell.__tablename__)}
                assert set(spalten) == set(modell.__table__.columns.keys())
                for spalte in modell.__table__.columns:
                    assert spalten[spalte.name]["nullable"] == spalte.nullable
                indizes = sa.inspect(conn).get_indexes(modell.__tablename__)
                for index in modell.__table__.indexes:
                    gefunden = next(i for i in indizes if i["name"] == index.name)
                    assert gefunden["column_names"] == [c.name for c in index.columns]
                    assert bool(gefunden["unique"]) == bool(index.unique)
            fk = sa.inspect(conn).get_foreign_keys("osm_gebaeude")[0]
            assert fk["referred_table"] == "osm_import_lauf" and fk["options"]["ondelete"] == "CASCADE"
            with Operations.context(MigrationContext.configure(conn)):
                neu.downgrade()
            assert "osm_gebaeude" not in sa.inspect(conn).get_table_names()
            assert "osm_import_lauf" not in sa.inspect(conn).get_table_names()
    engine.dispose()


@pytest.mark.parametrize("dialekt", ["mysql", "mariadb"])
def test_mariadb_ddl(dialekt):
    ausgabe = StringIO()
    with Operations.context(MigrationContext.configure(
        dialect_name=dialekt, opts={"as_sql": True, "output_buffer": ausgabe},
    )):
        migration("0335", "osm_gebaeude").upgrade()
        migration("0335", "osm_gebaeude").downgrade()
    ddl = ausgabe.getvalue()
    assert "lauf_id BIGINT NOT NULL" in ddl
    assert "FOREIGN KEY(lauf_id) REFERENCES osm_import_lauf (id) ON DELETE CASCADE" in ddl
    assert "CREATE UNIQUE INDEX uq_osm_gebaeude_lauf_way ON osm_gebaeude (lauf_id, osm_way_id)" in ddl
    assert "WHERE" not in ddl
