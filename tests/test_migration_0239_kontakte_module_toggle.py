"""SQLite-Upgrade/Downgrade-Test fuer den Kontakte-Modulschalter."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0239_kontakte_module_toggle.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0239", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_und_downgrade_kontakte_module_toggle(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0239.db'}")
    modul = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE org_settings (id INTEGER PRIMARY KEY)")
        _lauf(conn, modul.upgrade)
        assert "kontakte_module_enabled" in {
            column["name"] for column in sa.inspect(conn).get_columns("org_settings")
        }
        _lauf(conn, modul.downgrade)
        assert "kontakte_module_enabled" not in {
            column["name"] for column in sa.inspect(conn).get_columns("org_settings")
        }
