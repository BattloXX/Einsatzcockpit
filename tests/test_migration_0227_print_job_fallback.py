"""Portabilitaets- und Wiederaufsetzbarkeitstest fuer Migration 0227."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0227_print_job_fallback_of.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0227", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion):
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_downgrade_ist_wiederaufsetzbar(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0227.db'}")
    modul = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE print_job (id INTEGER PRIMARY KEY, error VARCHAR(500))")
        _lauf(conn, modul.upgrade)
        _lauf(conn, modul.upgrade)
        assert "fallback_of_job_id" in {s["name"] for s in sa.inspect(conn).get_columns("print_job")}
        _lauf(conn, modul.downgrade)
        _lauf(conn, modul.downgrade)
        assert "fallback_of_job_id" not in {s["name"] for s in sa.inspect(conn).get_columns("print_job")}
