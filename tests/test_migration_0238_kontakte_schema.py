"""SQLite-Upgrade/Downgrade-Test fuer das Kontakte-Grundschema."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0238_kontakte_schema.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0238", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_und_downgrade_kontakte_schema(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0238.db'}")
    modul = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fire_dept (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE objekt (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE objekt_kontakt (id INTEGER PRIMARY KEY)")

        _lauf(conn, modul.upgrade)
        inspector = sa.inspect(conn)
        assert {
            "kontakt", "kontakt_telefon", "kontakt_kategorie", "kontakt_kategorie_zuordnung",
            "kontakt_anhang", "kontakt_externe_referenz", "objekt_kontakt_freigabe",
        } <= set(inspector.get_table_names())
        assert "kontakt_id" in {column["name"] for column in inspector.get_columns("objekt_kontakt")}

        _lauf(conn, modul.downgrade)
        inspector = sa.inspect(conn)
        assert "kontakt" not in inspector.get_table_names()
        assert "kontakt_id" not in {column["name"] for column in inspector.get_columns("objekt_kontakt")}
