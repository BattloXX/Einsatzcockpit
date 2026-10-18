"""Migration erhält Mehrfachzuordnungen und erteilt keine automatische Druckfreigabe."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_downgrade_mit_bestandsdaten(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0293_kontakt_druckverzeichnis.py"
    spec = importlib.util.spec_from_file_location("migration_0293", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fire_dept (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE kontakt (id INTEGER PRIMARY KEY, anzeigename VARCHAR(150))")
        conn.exec_driver_sql(
            "CREATE TABLE kontakt_kategorie (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "org_id INTEGER, name VARCHAR(100), UNIQUE(org_id, name))"
        )
        conn.exec_driver_sql("CREATE TABLE kontakt_kategorie_zuordnung (kontakt_id INTEGER, kategorie_id INTEGER)")
        conn.exec_driver_sql("INSERT INTO fire_dept VALUES (1), (2)")
        conn.exec_driver_sql("INSERT INTO kontakt VALUES (1, 'Bestandskontakt')")
        conn.exec_driver_sql(
            "INSERT INTO kontakt_kategorie (id, org_id, name) VALUES "
            "(1, 1, 'Feuerwehr & Einsatzorganisationen'), (2, 1, 'Eigene Kategorie')"
        )
        conn.exec_driver_sql("INSERT INTO kontakt_kategorie_zuordnung VALUES (1, 1), (1, 2)")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT druck_freigegeben FROM kontakt WHERE id=1").scalar() == 0
        assert conn.exec_driver_sql("SELECT druck_prioritaet FROM kontakt WHERE id=1").scalar() == "normal"
        assert conn.exec_driver_sql("SELECT count(*) FROM kontakt_kategorie").scalar() == 17
        assert conn.exec_driver_sql("SELECT druck_standard FROM kontakt_kategorie WHERE id=1").scalar() == 1
        assert conn.exec_driver_sql("SELECT count(*) FROM kontakt_kategorie_zuordnung").scalar() == 2
        assert "kontakt_druckprofil" in sa.inspect(conn).get_table_names()
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "kontakt_druckprofil" not in sa.inspect(conn).get_table_names()
        assert conn.exec_driver_sql("SELECT anzeigename FROM kontakt WHERE id=1").scalar() == "Bestandskontakt"
        assert conn.exec_driver_sql("SELECT count(*) FROM kontakt_kategorie_zuordnung").scalar() == 2
