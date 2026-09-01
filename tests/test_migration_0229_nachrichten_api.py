"""Wiederaufsetzbarkeit und Alt-Key-Backfill der Migration 0229."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0229_nachrichten_api.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0229", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion):
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_downgrade_ist_wiederaufsetzbar_und_backfillt_alt_keys(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0229.db'}")
    modul = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE api_key (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO api_key (id) VALUES (1)")
        _lauf(conn, modul.upgrade)
        _lauf(conn, modul.upgrade)
        assert conn.exec_driver_sql("SELECT scopes FROM api_key WHERE id = 1").scalar() == (
            "einsatz:write,mailing:import"
        )
        _lauf(conn, modul.downgrade)
        _lauf(conn, modul.downgrade)
        assert "scopes" not in {s["name"] for s in sa.inspect(conn).get_columns("api_key")}
