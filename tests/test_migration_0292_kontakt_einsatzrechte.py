"""Upgrade bewahrt bestehende Kontaktfreigaben; Downgrade entfernt neue Tabellen."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_und_downgrade(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0292_kontakt_einsatzrechte.py"
    spec = importlib.util.spec_from_file_location("migration_0292", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        for table in ("fire_dept", "kontakt", "incident", "objekt_kontakt"):
            conn.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO objekt_kontakt (id) VALUES (1)")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT einsatz_sichtbar FROM objekt_kontakt WHERE id = 1").scalar() == 1
        assert "einsatz_kontakt" in sa.inspect(conn).get_table_names()
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "einsatz_kontakt" not in sa.inspect(conn).get_table_names()
        assert "einsatz_sichtbar" not in {column["name"] for column in sa.inspect(conn).get_columns("objekt_kontakt")}
        assert conn.exec_driver_sql("SELECT id FROM objekt_kontakt").scalar() == 1
